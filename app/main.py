"""
Dash AgentOS
============

The main entry point for Dash.

Run:
    python -m app.main
"""

import time
import logging as _main_logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

# Module-level logger. The router-registration blocks below (and several
# `except` branches) reference a global `logger`; it was never defined, so
# any branch that actually fired raised NameError (surfaced when SIM/AutoML
# gates added unconditional else-logs). Define it once here.
logger = _main_logging.getLogger("dash.main")
from os import getenv
from pathlib import Path


def _tier_label(complexity: str | None = None, reasoning: str | None = None) -> str:
    """Map router complexity + explicit override → exec-view tier.

    CityPharma (pharmacy counter) ships only 3 presentation tiers (2026-06-08):
      quick     — instant lookup, 1 KPI + 1 line  (UI "FAST")
      standard  — analytical card, KPI strip + breakdown  (UI "STANDARD" / AUTO default)
    DEEP / ULTRA / REASONING (RCA / forecast / verification / visible-chain) were
    removed from the UI picker — no pharmacy-counter use. AUTO is now CAPPED at
    `standard`: a complex question still gets a smart MODEL (complexity_router) but
    the ANSWER never explodes into deep/ultra scaffolding. `/deep ` slash command
    is the only remaining escape hatch into the deep layout (power user).
    """
    try:
        # Explicit override from slash command (`/deep`) still honored.
        r = (reasoning or "").lower().strip()
        if r in ("quick", "fast", "instant"):
            return "quick"
        if r in ("standard", "normal", "auto-standard"):
            return "standard"
        if r in ("deep", "high"):
            return "deep"
        # ultra/reasoning unreachable from the UI now; fold to deep if ever forced.
        if r in ("ultra", "max", "exhaustive", "reasoning", "chain", "step-by-step"):
            return "deep"
        # AUTO fallthrough — CAPPED at standard (deep/ultra/reasoning removed).
        c = (complexity or "").upper().strip()
        if c in ("TRIVIAL", "LOOKUP"):
            return "quick"
        return "standard"
    except Exception:
        return "standard"

_bg_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="dash-bg")

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from agno.os import AgentOS

# Rate limiting
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    _limiter = Limiter(key_func=get_remote_address, default_limits=[getenv("RATE_LIMIT", "200/minute")])
    _HAS_LIMITER = True
except ImportError:
    _HAS_LIMITER = False

# Sentry crash reporting (opt-in via SENTRY_DSN env). PROD-RISK: not initialized
# unless SENTRY_DSN is set — operator must configure for production observability.
try:
    _sentry_dsn = getenv("SENTRY_DSN", "").strip()
    if _sentry_dsn:
        import sentry_sdk
        try:
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration
            _integrations = [FastApiIntegration(), StarletteIntegration()]
        except Exception:
            _integrations = []
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=_integrations,
            environment=getenv("DASH_ENV") or getenv("RUNTIME_ENV", "dev"),
            traces_sample_rate=float(getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            send_default_pii=False,
        )
except Exception:
    pass

from dash.agents.analyst import analyst
from dash.agents.engineer import engineer
from dash.agents.researcher import researcher
from dash.settings import SLACK_SIGNING_SECRET, SLACK_TOKEN, TRAINING_MODEL, LITE_MODEL, dash_knowledge, dash_learnings
from dash.team import dash
from db import get_postgres_db, db_url
from sqlalchemy import create_engine as _sa_create_engine, text as sa_text
from sqlalchemy.pool import NullPool

_shared_engine = _sa_create_engine(db_url, poolclass=NullPool)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
runtime_env = getenv("RUNTIME_ENV", "prd")
scheduler_base_url = getenv("AGENTOS_URL", "http://127.0.0.1:8000")

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
interfaces: list = []
if SLACK_TOKEN and SLACK_SIGNING_SECRET:
    from agno.os.interfaces.slack import Slack

    interfaces.append(
        Slack(
            team=dash,
            streaming=True,
            token=SLACK_TOKEN,
            signing_secret=SLACK_SIGNING_SECRET,
            resolve_user_identity=True,
        )
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
def _register_schedules() -> None:
    """Register all scheduled tasks (idempotent — safe to run on every startup)."""
    from agno.scheduler import ScheduleManager

    mgr = ScheduleManager(get_postgres_db())
    mgr.create(
        name="knowledge-refresh",
        cron="0 4 * * *",
        endpoint="/knowledge/reload",
        payload={},
        timezone="UTC",
        description="Daily knowledge file re-index",
        if_exists="update",
    )


@asynccontextmanager
async def lifespan(app):  # type: ignore[no-untyped-def]
    import os
    if not os.getenv("OPENROUTER_API_KEY"):
        import logging
        logging.critical("OPENROUTER_API_KEY not set — cannot start")
        raise RuntimeError("OPENROUTER_API_KEY environment variable is required")
    # Master daemon gate: in K8s where CronJobs handle scheduled work, set
    # DAEMONS_DISABLED=1 (or K8S_DAEMON_MODE=cronjob) on API pods so 8 uvicorn
    # workers don't each spin up duplicate background loops. Per-daemon flags
    # (AUTO_CAMPAIGN_DAEMON_DISABLED, etc.) still override individually.
    import os as _os_dg
    # Issue #13: also gate by WORKER_RANK so 8 uvicorn workers don't each
    # spawn duplicate in-process daemons. Only rank 0 spawns; other workers
    # serve traffic only. WORKER_RANK must be set per-worker by the process
    # manager (e.g. entrypoint.sh exporting it before exec uvicorn). When
    # unset, default to spawning (single-worker dev / unknown env).
    _WORKER_RANK = _os_dg.environ.get("WORKER_RANK")
    # Explicit operator override (K8s CronJob mode / hard-off).
    _DAEMONS_EXPLICIT_OFF = (
        _os_dg.environ.get("DAEMONS_DISABLED") in ("1", "true", "TRUE", "yes")
        or _os_dg.environ.get("K8S_DAEMON_MODE") == "cronjob"
    )
    # Single-runner election: pick exactly ONE worker/pod to run daemons via a
    # DB heartbeat leader (replaces the broken WORKER_RANK==0 gate — gunicorn
    # worker.age starts at 1 and drifts on respawn, so rank 0 was never present
    # → ALL daemons silently never ran). Heartbeat election is PgBouncer-safe and
    # auto-fails-over on a stale leader. Skipped entirely if explicitly off.
    _DAEMON_LEADER = False
    if not _DAEMONS_EXPLICIT_OFF:
        try:
            from dash.runtime.daemon_leader import try_become_leader
            _DAEMON_LEADER = try_become_leader()
        except Exception as _dle:
            import logging as _dl_log
            _dl_log.getLogger(__name__).warning(
                f"daemon leader election errored ({_dle}) — failing open (running daemons)"
            )
            _DAEMON_LEADER = True
    _DAEMONS_OFF = _DAEMONS_EXPLICIT_OFF or (not _DAEMON_LEADER)
    def _should_run_daemons() -> bool:
        return not _DAEMONS_OFF
    if _DAEMONS_OFF and not _DAEMONS_EXPLICIT_OFF:
        import logging as _rk_log
        _rk_log.getLogger(__name__).info(
            "daemons suppressed on this worker (another worker is the daemon leader)"
        )

    # Issue #17: WORKER_RANK gating is per-pod; multi-replica K8s deployments
    # have N pods each with rank-0, which still spawns N copies of every daemon.
    # Warn once per process if we appear to be in K8s and daemons aren't
    # explicitly disabled, so operators see the misconfiguration in logs.
    if (
        (_WORKER_RANK is None or _WORKER_RANK == "0")
        and _os_dg.environ.get("DAEMONS_DISABLED") not in ("1", "true", "TRUE", "yes")
        and _os_dg.environ.get("K8S_DAEMON_MODE") != "cronjob"
        and _os_dg.environ.get("KUBERNETES_SERVICE_HOST")
    ):
        import logging as _k8s_log
        _k8s_log.getLogger(__name__).warning(
            "daemons spawning on this pod — in multi-replica K8s, ensure only "
            "one replica runs daemons via DAEMONS_DISABLED=1 on others, or use "
            "a dedicated daemon-host deployment with replicas=1"
        )

    # Layer 3 of stale-image defense (Caveat #5): if BUILD_TIME is older than
    # 24h, log an amber warning so operators see it in container logs.
    # Throttled to once per process startup. Also surfaces via /api/health
    # `staleness_warning` field for external monitors.
    try:
        from datetime import datetime as _dt_st, timezone as _tz_st
        _bt = _os_dg.environ.get("BUILD_TIME", "").strip()
        if _bt:
            _bt_norm = _bt.replace("Z", "+00:00")
            _dt_built = _dt_st.fromisoformat(_bt_norm)
            if _dt_built.tzinfo is None:
                _dt_built = _dt_built.replace(tzinfo=_tz_st.utc)
            _age_h = (_dt_st.now(_tz_st.utc) - _dt_built).total_seconds() / 3600.0
            globals()["_BUILD_AGE_HOURS_AT_START"] = round(_age_h, 2)
            if _age_h > 24:
                import logging as _st_log
                _st_log.getLogger(__name__).warning(
                    f"⚠ Container {_age_h:.1f}h old (built {_bt}). "
                    f"Recent source changes may not be deployed. "
                    f"Run `make rebuild` if you expected fresher code."
                )
        else:
            globals()["_BUILD_AGE_HOURS_AT_START"] = None
    except Exception:
        globals()["_BUILD_AGE_HOURS_AT_START"] = None

    from app.auth import init_auth
    init_auth()
    # Federated-auth transient flow store (OAuth state/nonce/PKCE). Guarded.
    try:
        from app.auth_federation import init_federation
        init_federation()
    except Exception as _fed_e:
        import logging as _fed_log
        _fed_log.getLogger(__name__).warning(f"init_federation skipped: {_fed_e}")
    # DB migration auto-runner: applies db/migrations/*.sql files in lexical
    # order, tracked in public.dash_migrations. Multi-worker safe via
    # pg_advisory_lock. Failures log + continue unless RAISE_ON_MIGRATION_FAIL=1.
    # WORKER_RANK gate: only worker 0 migrates. preload=False means every
    # gunicorn worker runs this lifespan; without the gate, N workers stampede
    # a fresh DB concurrently (the advisory lock alone is void through a
    # transaction-mode pgbouncer) → partial/empty schema. The runner also now
    # connects DIRECT (bypasses pgbouncer) so its advisory lock holds for the
    # multi-container case. Both guards together = no migration race.
    if getenv("WORKER_RANK", "0") == "0":
        try:
            from dash.db_runner.migrate import run_migrations as _run_migrations
            _mig_result = _run_migrations()
            import logging as _mig_log
            _mig_log.getLogger(__name__).info(
                f"migrations: applied={len(_mig_result.get('applied', []))} "
                f"skipped={_mig_result.get('skipped', 0)} "
                f"errors={len(_mig_result.get('errors', []))}"
            )
        except Exception as _mig_e:
            import logging as _mig_log
            _mig_log.getLogger(__name__).warning(f"migration runner skipped: {_mig_e}")
    # Register builtin skills (idempotent upsert into dash_skill_registry).
    # Includes skl_dash_builder / skl_panel_designer / skl_dash_critic for
    # Deep Dash 9-stage pipeline. Worker-rank gated to avoid 8x duplicate inserts.
    try:
        if getenv("WORKER_RANK", "0") == "0":
            from dash.skills.builtin import register_builtins as _reg_skills
            _skill_count = _reg_skills()
            import logging as _skill_log
            _skill_log.getLogger(__name__).info(f"skills: registered {_skill_count} builtins")
    except Exception as _skill_e:
        import logging as _skill_log
        _skill_log.getLogger(__name__).warning(f"skill register skipped: {_skill_e}")
    # Seed shipped RLS blueprints (idempotent UPSERT on slug PK; is_system=TRUE).
    try:
        from dash.embed import _get_engine as _bp_eng
        from dash.embed.rls_blueprints import seed_system_blueprints
        _bp_res = seed_system_blueprints(_bp_eng())
        import logging as _bp_log
        _bp_log.getLogger(__name__).info(
            f"rls_blueprints: seeded={_bp_res.get('seeded', 0)} "
            f"errors={len(_bp_res.get('errors', []))}"
        )
    except Exception as _bp_e:
        import logging as _bp_log
        _bp_log.getLogger(__name__).warning(f"rls_blueprints seed skipped: {_bp_e}")
    try:
        from app.connectors import init_connectors
        init_connectors()
    except Exception as _conn_e:
        import logging as _cl
        _cl.getLogger(__name__).warning(f"connectors init skipped: {_conn_e}")
    # pruned: sharepoint/gdrive/onedrive init removed (single-agent)
    try:
        from app.brain import init_brain
        init_brain()
    except ImportError:
        pass
    try:
        from app.brain_versions import init_brain_versions
        init_brain_versions()
    except Exception:
        pass
    # Brain versions auto-purge: rows older than 365 days, ~daily cadence.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_bv
        async def _brain_versions_purge_loop() -> None:
            import logging as _bv_log
            from sqlalchemy import create_engine as _bv_ce, text as _bv_text
            from sqlalchemy.pool import NullPool as _bv_np
            from db import db_url as _bv_url
            log = _bv_log.getLogger("brain_versions.purge")
            while True:
                try:
                    eng = _bv_ce(_bv_url, poolclass=_bv_np)
                    with eng.begin() as conn:
                        res = conn.execute(_bv_text(
                            "DELETE FROM public.dash_brain_versions "
                            "WHERE created_at < now() - INTERVAL '365 days'"
                        ))
                        if res.rowcount:
                            log.info(f"purged {res.rowcount} brain version rows older than 365d")
                except Exception as e:
                    log.warning(f"brain versions purge failed: {e}")
                await _asyncio_bv.sleep(86400)  # 24h
        _asyncio_bv.create_task(_brain_versions_purge_loop())
    except Exception:
        pass
    # Keep template_bindings + autonomous_workflows infra tables bootstrapped
    # for callers that still write to them (auto_apply, ontology_api). The
    # industry preset registry/apply layer is gone but storage helpers remain.
    try:
        from dash.templates.storage import bootstrap_tables as _bootstrap_agent_template_tables
        _bootstrap_agent_template_tables()
    except Exception as _e:
        import logging as _t_log
        _t_log.getLogger(__name__).warning(f"agent template tables bootstrap skipped: {_e}")
    try:
        from dash.templates.customer_scores import bootstrap_tables as _bootstrap_customer_scores
        _bootstrap_customer_scores()
    except Exception as _e:
        import logging as _lg
        _lg.getLogger(__name__).warning(f"customer_scores bootstrap failed: {_e}")
    try:
        from dash.templates.runner import start_runner as _start_autonomous
        _start_autonomous(poll_seconds=60)
        import logging as _ar_log
        _ar_log.getLogger(__name__).info("autonomous workflow runner started")
    except Exception as _e:
        import logging as _ar_log
        _ar_log.getLogger(__name__).warning(f"autonomous runner not started: {_e}")
    # Minion consumer — drains the durable dash.dash_minions Postgres queue.
    # Importing dash.minions.worker runs all its register_handler(...) calls
    # (autosim_generate_grounded, dream_lite, sim_run, reflect_sessions, …) so
    # handlers are registered before the loop claims anything. Without this,
    # pending minions accumulate forever (nothing was consuming them).
    # WORKER_RANK-gated so 8 uvicorn workers don't each spawn a consumer.
    # Opt-out via MINION_WORKER_DISABLED=1 (also honored inside start_worker).
    # NOTE: NOT rank-gated. The queue uses SELECT FOR UPDATE SKIP LOCKED, so
    # running a consumer on every uvicorn/gunicorn worker is safe (no duplicate
    # processing) and drains faster. The rank-0 gate is unreliable here anyway:
    # gunicorn's WORKER_RANK = worker.age starts at 1 (and drifts on respawn),
    # so rank==0 is essentially never true → daemons gated on it never start.
    # Honored opt-out: MINION_WORKER_DISABLED=1 (checked inside start_worker).
    try:
        from dash.minions.worker import start_worker as _start_minion_worker
        _start_minion_worker()
        import logging as _mw_log
        _mw_log.getLogger(__name__).info("minion consumer started")
    except Exception as _e:
        import logging as _mw_log
        _mw_log.getLogger(__name__).warning(f"minion consumer not started: {_e}")
    # Vector sync worker + nightly re-embed loop (in-process, ~6h cadence).
    # Defensive — failures here must never block app startup.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_vs
        from dash.tools.vector_sync import VECTOR_SYNC as _VECTOR_SYNC
        from dash.cron.reembed_stale import reembed_loop as _reembed_loop
        _VECTOR_SYNC.start()
        _asyncio_vs.create_task(_reembed_loop())
        import logging as _vs_log
        _vs_log.getLogger(__name__).info("vector_sync worker + reembed_loop started")
    except Exception as _e:
        import logging as _vs_log
        _vs_log.getLogger(__name__).warning(f"vector_sync not started: {_e}")
    # Ingest staging cleanup daemon — purges old promoted/rejected batches (dirs +
    # rows) on a daily cadence. Honors INGEST_CLEANUP_* env. Daemon-gated.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_ic
        from dash.ingest.cleanup import cleanup_loop as _ingest_cleanup_loop
        _asyncio_ic.create_task(_ingest_cleanup_loop())
        import logging as _ic_log
        _ic_log.getLogger(__name__).info("ingest cleanup daemon started")
    except Exception as _e:
        import logging as _ic_log
        _ic_log.getLogger(__name__).warning(f"ingest cleanup not started: {_e}")

    # Golden-SQL drift daemon (24h). Re-executes pinned goldens, demotes drifted ones.
    # Mirrors Dataherald's golden_sql validation. Disable: GOLDEN_DRIFT_DISABLED=1.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_gd
        from dash.cron.golden_drift import golden_drift_loop as _golden_drift_loop
        _asyncio_gd.create_task(_golden_drift_loop())
        import logging as _gd_log
        _gd_log.getLogger(__name__).info("golden_drift daemon started (24h)")
    except Exception as _e:
        import logging as _gd_log
        _gd_log.getLogger(__name__).warning(f"golden_drift not started: {_e}")

    # Chemist clinical-eval daemon (24h). Runs the golden forward+inverse eval and
    # refreshes Dashboard 🧪 "Clinical accuracy %". Disable: CHEMIST_EVAL_DISABLED=1.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_ce
        from dash.cron.chemist_eval_daemon import chemist_eval_loop as _chemist_eval_loop
        _asyncio_ce.create_task(_chemist_eval_loop())
        import logging as _ce_log
        _ce_log.getLogger(__name__).info("chemist_eval daemon started (24h)")
    except Exception as _e:
        import logging as _ce_log
        _ce_log.getLogger(__name__).warning(f"chemist_eval not started: {_e}")

    # Ontology auto-cluster daemon (~6h cadence). Mirrors reembed_loop pattern.
    # default OFF (Phase-1 trim: pure-burn daemon, output not consumed). Set ONTOLOGY_CLUSTER_ENABLED=1 to re-enable.
    # Opt-out via ONTOLOGY_CLUSTER_DISABLED=1 (also honored inside the loop).
    try:
        import os as _os_oc
        if _os_oc.environ.get("ONTOLOGY_CLUSTER_ENABLED") not in ("1", "true", "TRUE", "yes"):
            raise RuntimeError("ontology cluster daemon disabled (default OFF)")
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        if _os_oc.environ.get("ONTOLOGY_CLUSTER_DISABLED") in ("1", "true", "TRUE", "yes"):
            raise RuntimeError("daemon disabled via ONTOLOGY_CLUSTER_DISABLED")
        import asyncio as _asyncio_oc
        from dash.cron.ontology_cluster_daemon import (
            ontology_cluster_loop as _ontology_cluster_loop,
        )
        _asyncio_oc.create_task(_ontology_cluster_loop())
        import logging as _oc_log
        _oc_log.getLogger(__name__).info("ontology cluster daemon started")
    except Exception as _e:
        import logging as _oc_log
        _oc_log.getLogger(__name__).warning(
            f"ontology cluster daemon not started: {_e}"
        )
    # Table usage refresh daemon (~hourly). Parses dash_traces SQL via
    # sqlglot + populates mv_table_usage for retrieval popularity boost.
    # Disable via TABLE_USAGE_REFRESH_DISABLED=1 (also honored inside loop).
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_tu
        from dash.cron.table_usage_refresh import (
            table_usage_refresh_loop as _table_usage_refresh_loop,
        )
        _asyncio_tu.create_task(_table_usage_refresh_loop())
        import logging as _tu_log
        _tu_log.getLogger(__name__).info("table_usage_refresh daemon started")
    except Exception as _e:
        import logging as _tu_log
        _tu_log.getLogger(__name__).warning(
            f"table_usage_refresh daemon not started: {_e}"
        )

    # Workflow runner daemon — claims queued workflow runs + builds dashboards.
    # Disable via WORKFLOW_RUNNER_DISABLED=1.
    # Run in a dedicated thread (not asyncio.create_task) so it survives lifespan
    # unwind + can't be GC'd. Mirrors autonomous_workflow_runner pattern.
    def _start_workflow_runner_thread(reason: str = "lifespan") -> bool:
        """Spawn workflow_runner thread. Idempotent — no-op if already running."""
        import logging as _wfr_log_inner
        _log = _wfr_log_inner.getLogger(__name__)
        existing = globals().get("_WORKFLOW_RUNNER_THREAD")
        if existing is not None and getattr(existing, "is_alive", lambda: False)():
            _log.info("workflow_runner thread already alive — skip spawn (%s)", reason)
            return True
        try:
            import threading as _wfr_threading
            import asyncio as _asyncio_wfr
            from dash.cron.workflow_runner import workflow_runner_loop as _wf_runner_loop

            def _wf_runner_thread_target() -> None:
                try:
                    _asyncio_wfr.run(_wf_runner_loop())
                except Exception:
                    _wfr_log_inner.getLogger(__name__).exception(
                        "workflow_runner thread crashed"
                    )

            _wfr_thread = _wfr_threading.Thread(
                target=_wf_runner_thread_target,
                name="workflow-runner",
                daemon=True,
            )
            _wfr_thread.start()
            globals()["_WORKFLOW_RUNNER_THREAD"] = _wfr_thread
            _log.info(
                "workflow_runner daemon started via %s (thread tid=%s)",
                reason, _wfr_thread.ident,
            )
            return True
        except Exception as _se:
            _log.warning("workflow_runner daemon failed to spawn (%s): %s", reason, _se)
            return False

    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        _start_workflow_runner_thread("lifespan")
    except Exception as _e:
        import logging as _wfr_log
        _wfr_log.getLogger(__name__).warning(
            f"workflow_runner daemon not started: {_e}"
        )
        # Register retry callback — fires when leader election finally wins
        try:
            from dash.runtime.daemon_leader import register_post_claim_callback
            register_post_claim_callback(lambda: _start_workflow_runner_thread("post-claim-retry"))
        except Exception:
            pass

    # Workflow cron scheduler (Agent #4) — fires due dash.dash_workflow_schedules.
    # Disable via WORKFLOW_SCHEDULER_DISABLED=1 (also honored inside loop).
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_ws
        from dash.cron.workflow_scheduler import (
            workflow_scheduler_loop as _workflow_scheduler_loop,
        )
        _asyncio_ws.create_task(_workflow_scheduler_loop())
        import logging as _ws_log
        _ws_log.getLogger(__name__).info("workflow_scheduler daemon started")
    except Exception as _e:
        import logging as _ws_log
        _ws_log.getLogger(__name__).warning(
            f"workflow_scheduler daemon not started: {_e}"
        )

    # Auto-Campaign daemon (Tier 4) — daily RFM/churn snapshot + auto-draft.
    # Disable via AUTO_CAMPAIGN_DAEMON_DISABLED=1 (also honored inside loop).
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_ac
        from dash.cron.auto_campaign_daemon import (
            auto_campaign_loop as _auto_campaign_loop,
        )
        _asyncio_ac.create_task(_auto_campaign_loop())
        import logging as _ac_log
        _ac_log.getLogger(__name__).info("auto_campaign daemon started")
    except Exception as _e:
        import logging as _ac_log
        _ac_log.getLogger(__name__).warning(
            f"auto_campaign daemon not started: {_e}"
        )
    # Scope-guardrail auto-derive daemon — nightly fills missing per-project scope
    # so off-topic guardrail (Layer 1 classifier + Layer 2 hard rule) always has data.
    # Disable via SCOPE_DERIVE_DAEMON_DISABLED=1.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_sd
        from dash.cron.scope_derive_daemon import scope_derive_loop as _sd_loop
        _asyncio_sd.create_task(_sd_loop())
        import logging as _sd_log
        _sd_log.getLogger(__name__).info("scope_derive daemon started")
    except Exception as _e:
        import logging as _sd_log
        _sd_log.getLogger(__name__).warning(
            f"scope_derive daemon not started: {_e}"
        )
    # Pruned-vertical daemons (venture/ops/supply) query portco/suppliers tables
    # that don't exist single-tenant → boot-time list errors. Gate the whole
    # cluster off via VERTICAL_DAEMONS_DISABLED=1.
    import os as _os_vd
    _VERTICAL_OFF = _os_vd.environ.get("VERTICAL_DAEMONS_DISABLED", "").lower() in ("1", "true", "yes")

    # VentureDesk monthly rescore daemon — re-runs DCF/IRR on saved scenarios, flags verdict drift.
    # Disable via VENTURE_RESCORE_DAEMON_DISABLED=1.
    try:
        if not _should_run_daemons() or _VERTICAL_OFF:
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_vr
        from dash.cron.venture_rescore import (
            venture_rescore_loop as _venture_rescore_loop,
        )
        _asyncio_vr.create_task(_venture_rescore_loop())
        import logging as _vr_log
        _vr_log.getLogger(__name__).info("venture_rescore daemon started")
    except Exception as _e:
        import logging as _vr_log
        _vr_log.getLogger(__name__).warning(
            f"venture_rescore daemon not started: {_e}"
        )
    # Ops anomaly scan daemon
    try:
        if not _should_run_daemons() or _VERTICAL_OFF:
            raise RuntimeError("daemons disabled")
        import asyncio as _aio_ops_a
        from dash.cron.ops_anomaly_scan import ops_anomaly_loop as _ops_anomaly_loop
        _aio_ops_a.create_task(_ops_anomaly_loop())
        import logging as _ops_a_log
        _ops_a_log.getLogger(__name__).info("ops_anomaly_scan daemon started")
    except Exception as _e:
        import logging as _ops_a_log
        _ops_a_log.getLogger(__name__).warning(
            f"ops_anomaly_scan daemon not started: {_e}"
        )

    # Supply score daemon (Sprint 3)
    try:
        if not _should_run_daemons() or _VERTICAL_OFF:
            raise RuntimeError("daemons disabled")
        import asyncio as _aio_sup_s
        from dash.cron.supply_score_loop import supply_score_loop as _supply_score_loop
        _aio_sup_s.create_task(_supply_score_loop())
        import logging as _sup_s_log
        _sup_s_log.getLogger(__name__).info("supply_score daemon started")
    except Exception as _e:
        import logging as _sup_s_log
        _sup_s_log.getLogger(__name__).warning(
            f"supply_score daemon not started: {_e}"
        )

    # Supply news scan daemon (Sprint 3)
    try:
        if not _should_run_daemons() or _VERTICAL_OFF:
            raise RuntimeError("daemons disabled")
        import asyncio as _aio_sup_n
        from dash.cron.supply_news_scan_loop import supply_news_scan_loop as _supply_news_loop
        _aio_sup_n.create_task(_supply_news_loop())
        import logging as _sup_n_log
        _sup_n_log.getLogger(__name__).info("supply_news_scan daemon started")
    except Exception as _e:
        import logging as _sup_n_log
        _sup_n_log.getLogger(__name__).warning(
            f"supply_news_scan daemon not started: {_e}"
        )

    # Ops initiative staleness daemon
    try:
        if not _should_run_daemons() or _VERTICAL_OFF:
            raise RuntimeError("daemons disabled")
        import asyncio as _aio_ops_i
        from dash.cron.ops_initiative_staleness import ops_staleness_loop as _ops_staleness_loop
        _aio_ops_i.create_task(_ops_staleness_loop())
        import logging as _ops_i_log
        _ops_i_log.getLogger(__name__).info("ops_initiative_staleness daemon started")
    except Exception as _e:
        import logging as _ops_i_log
        _ops_i_log.getLogger(__name__).warning(
            f"ops_initiative_staleness daemon not started: {_e}"
        )

    # Connector Secret Rotation daemon — scans stale secret rotations, emits notifications.
    # Disable via CONNECTOR_ROTATION_DAEMON_DISABLED=1.
    try:
        if not _should_run_daemons():
            raise RuntimeError("daemons disabled")
        import asyncio as _asyncio_cr
        from dash.cron.connector_rotation_daemon import (
            connector_rotation_loop as _connector_rotation_loop,
        )
        _asyncio_cr.create_task(_connector_rotation_loop())
        import logging as _cr_log
        _cr_log.getLogger(__name__).info("connector_rotation daemon started")
    except Exception as _e:
        import logging as _cr_log
        _cr_log.getLogger(__name__).warning(
            f"connector_rotation daemon not started: {_e}"
        )
    # sim_cleanup daemon DELETED 2026-05-23 (sim chassis removed)
    # Deck schedule runner (Phase 7) — polls public.dash_deck_schedules.
    # Disable via DECK_SCHEDULE_DAEMON_DISABLED=1.
    try:
        import os as _os_ds
        if (
            _should_run_daemons()
            and _os_ds.environ.get("DECK_SCHEDULE_DAEMON_DISABLED") not in (
                "1", "true", "TRUE", "yes",
            )
        ):
            import asyncio as _asyncio_ds
            from dash.cron.deck_schedule_runner import (
                deck_schedule_loop as _deck_schedule_loop,
            )
            _asyncio_ds.create_task(_deck_schedule_loop())
            import logging as _ds_log
            _ds_log.getLogger(__name__).info("deck_schedule_runner started")
    except Exception as _e:
        import logging as _ds_log
        _ds_log.getLogger(__name__).warning(
            f"deck_schedule_runner not started: {_e}"
        )
    # Stale training step reaper (Phase 7) — every 5 min, marks
    # dash_training_steps rows stuck at status='running' for >15min as failed.
    # Frees stuck pipelines, surfaces dead daemons. Disable via
    # STALE_STEP_REAPER_DISABLED=1.
    try:
        if _should_run_daemons():
            import asyncio
            from dash.cron.stale_step_reaper import stale_step_reaper_loop
            asyncio.create_task(stale_step_reaper_loop())
            logger.info("stale_step_reaper started")
    except Exception as e:
        logger.warning(f"stale_step_reaper not started: {e}")
    # Weekly benchmark sync loop — pulls public industry KPIs into the
    # global Brain. Disable via BENCHMARK_SYNC_DISABLED=1 (e.g. when a
    # dedicated K8s CronJob is calling /api/ontology/benchmarks/sync-now).
    # default OFF (Phase-1 trim: pure-burn daemon, output not consumed). Set BENCHMARK_SYNC_ENABLED=1 to re-enable.
    try:
        import os as _os_bs
        if (
            _os_bs.environ.get("BENCHMARK_SYNC_ENABLED") in ("1", "true", "TRUE", "yes")
            and _should_run_daemons()
            and _os_bs.environ.get("BENCHMARK_SYNC_DISABLED") not in (
                "1", "true", "TRUE", "yes",
            )
        ):
            import asyncio as _asyncio_bs
            from dash.cron.benchmark_sync_loop import (
                benchmark_sync_loop as _bm_loop,
            )
            _asyncio_bs.create_task(_bm_loop())
            import logging as _bm_log
            _bm_log.getLogger(__name__).info("benchmark_sync_loop started")
    except Exception as _e:
        import logging as _bm_log
        _bm_log.getLogger(__name__).warning(
            f"benchmark_sync_loop not started: {_e}"
        )
    # MRR snapshot daemon — daily 04:30 UTC. Tier 4.
    try:
        import os as _os_mrr
        if _should_run_daemons() and _os_mrr.environ.get("MRR_SNAPSHOT_DISABLED") not in (
            "1", "true", "TRUE", "yes",
        ):
            import asyncio as _asyncio_mrr
            from dash.cron.mrr_snapshot_loop import (
                mrr_snapshot_loop as _mrr_loop,
            )
            _asyncio_mrr.create_task(_mrr_loop())
            import logging as _mrr_log
            _mrr_log.getLogger(__name__).info("mrr_snapshot_loop started")
    except Exception as _e:
        import logging as _mrr_log
        _mrr_log.getLogger(__name__).warning(
            f"mrr_snapshot_loop not started: {_e}"
        )
    # Eval canary daemon — scheduled production eval (OpenAI-style continuous
    # regression catch). Runs the capped SMOKE suite daily, compares per-group
    # pass/fail to the previous canary run, WARNs + notifies on regressions.
    # Gated like every other daemon: only the daemon leader/rank-0 worker spawns
    # it. Opt-out EVAL_CANARY_DISABLED=1. NOT run inline at boot — loop sleeps
    # the interval first. try/except so a failure can't break boot.
    try:
        import os as _os_ec
        if _should_run_daemons() and _os_ec.environ.get("EVAL_CANARY_DISABLED") not in (
            "1", "true", "TRUE", "yes",
        ):
            import asyncio as _asyncio_ec
            from dash.cron.eval_canary import eval_canary_loop as _ec_loop
            _asyncio_ec.create_task(_ec_loop())
            import logging as _ec_log
            _ec_log.getLogger(__name__).info("eval_canary_loop started")
    except Exception as _e:
        import logging as _ec_log
        _ec_log.getLogger(__name__).warning(
            f"eval_canary_loop not started: {_e}"
        )
    _register_schedules()
    # Start daily self-learning scheduler (opt-out via LEARNING_SCHEDULER_DISABLED=1)
    try:
        import logging as _ls_log
        from dash.learning.scheduler import start_scheduler
        start_scheduler()
        _ls_log.getLogger(__name__).info("self-learning scheduler started")
    except Exception as e:
        import logging as _ls_log
        _ls_log.getLogger(__name__).warning(f"learning scheduler not started: {e}")
    # Daily digest scheduler (5-min poll, +/- 2 min match window).
    # Sends per-project SMTP + Slack digests at digest_time_utc.
    try:
        import logging as _ds_log
        from app.digest_api import start_digest_scheduler
        start_digest_scheduler()
        _ds_log.getLogger(__name__).info("digest scheduler started")
    except Exception as e:
        import logging as _ds_log
        _ds_log.getLogger(__name__).warning(f"digest scheduler not started: {e}")
    # Connector sync scheduler (APScheduler, 60s tick).
    # Opt-out via CONNECTOR_SCHEDULER_DISABLED=1 (auto-disabled under K8S unless
    # CONNECTOR_SCHEDULER_FORCE_INPROCESS=1) — when running as a separate cronjob.
    try:
        import logging as _cs_log
        from app.scheduler_connectors import start_connector_scheduler
        start_connector_scheduler()
        _cs_log.getLogger(__name__).info("connector scheduler started")
    except Exception as e:
        import logging as _cs_log
        _cs_log.getLogger(__name__).warning(f"connector scheduler not started: {e}")
    # Training queue worker — Option B (Redis + in-process). Only the
    # daemon-eligible worker spawns the loop (Issue #13 pattern). Disable
    # via TRAINING_QUEUE_DISABLED=1.
    try:
        if _should_run_daemons():
            import asyncio as _asyncio_tq
            from dash.training.train_queue import run_worker_loop as _tq_loop
            _asyncio_tq.create_task(_asyncio_tq.to_thread(_tq_loop))
            logger.info("training_queue worker started")
    except Exception as _e:
        logger.warning(f"training_queue worker not started: {_e}")
    # Auto-train daemon — watches for data changes (new tables / row deltas)
    # and enqueues retraining every AUTO_TRAIN_POLL_INTERVAL_S (default 900s).
    # Disable: AUTO_TRAIN_DAEMON_DISABLED=1.
    try:
        if _should_run_daemons():
            import asyncio as _asyncio_at
            from dash.cron.auto_train_daemon import auto_train_loop as _auto_train_loop
            _asyncio_at.create_task(_auto_train_loop())
            logger.info("auto_train_daemon: started")
    except Exception as _e:
        logger.warning(f"auto_train_daemon import failed: {_e}")
    yield
    # ---- Shutdown: dispose all data-source providers (engines + caches).
    try:
        from dash.providers import get_registry
        await get_registry().dispose_all()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Provider dispose on shutdown failed: {e}"
        )


# ---------------------------------------------------------------------------
# Create AgentOS
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    name="Dash",
    tracing=True,
    scheduler=True,
    scheduler_base_url=scheduler_base_url,
    authorization=False,  # We use our own AuthMiddleware, not Agno JWT
    lifespan=lifespan,
    db=get_postgres_db(),
    teams=[dash],
    agents=[analyst, engineer, researcher],
    knowledge=[dash_knowledge, dash_learnings],
    interfaces=interfaces,
    config=str(Path(__file__).parent / "config.yaml"),
)

app = agent_os.get_app()

# Rate limiting middleware
if _HAS_LIMITER:
    app.state.limiter = _limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Sentry context tagging — annotate every event with project_slug + user_id
# pulled from request path / auth state. No-op when SENTRY_DSN unset.
# ---------------------------------------------------------------------------
try:
    if getenv("SENTRY_DSN", "").strip():
        import re as _re_sentry
        import sentry_sdk as _sentry_mw
        from starlette.middleware.base import BaseHTTPMiddleware as _BHM

        _SLUG_RE = _re_sentry.compile(r"/api/projects/([a-z0-9_-]+)")

        class _SentryTagMiddleware(_BHM):
            async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
                try:
                    scope = _sentry_mw.Hub.current.scope
                    m = _SLUG_RE.search(request.url.path or "")
                    if m:
                        scope.set_tag("project_slug", m.group(1))
                    user = getattr(getattr(request, "state", None), "user", None)
                    if user is not None:
                        uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
                        if uid is not None:
                            scope.set_user({"id": str(uid)})
                except Exception:
                    pass
                return await call_next(request)

        app.add_middleware(_SentryTagMiddleware)
except Exception:
    pass


# ---------------------------------------------------------------------------
# Sentry debug endpoint — guarded by DASH_DEBUG flag. Used to verify wiring.
# ---------------------------------------------------------------------------
@app.get("/api/_debug/sentry-test")
def _sentry_test_endpoint():
    if getenv("DASH_DEBUG", "").lower() not in ("1", "true", "yes", "on"):
        raise HTTPException(404, "not found")
    raise RuntimeError("dash sentry-test: intentional error to verify Sentry wiring")


# ---------------------------------------------------------------------------
# Prometheus /metrics — instrument FastAPI request latency + status codes,
# expose at /metrics. Custom Dash counters defined in dash/utils/metrics.py.
# ---------------------------------------------------------------------------
try:
    from prometheus_fastapi_instrumentator import Instrumentator as _PromInstrumentator
    _PromInstrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=False,
        excluded_handlers=["/metrics", "/health", "/api/health"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    # Ensure custom counter module imports (registers metrics with default registry).
    try:
        from dash.utils import metrics as _dash_metrics  # noqa: F401
    except Exception as _me:
        logger.warning(f"dash.utils.metrics import failed: {_me}")
except Exception as _pe:
    logger.warning(f"prometheus instrumentator not loaded: {_pe}")


# ---------------------------------------------------------------------------
# Custom endpoints
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Upload API
# ---------------------------------------------------------------------------
from app.auth import router as auth_router
from app.projects import router as projects_router
from app.upload import router as upload_router
from app.rules import router as rules_router
from app.dashboards import router as dashboards_router
from app.dashboards_api import router as dashboards_v2_router
try:
    from app.dashboard_to_deck import router as dashboard_to_deck_router
except Exception:
    dashboard_to_deck_router = None  # Phase 3 — Dashboard → Deck (POST /api/dashboards/{id}/to-deck)
from app.accuracy_api import router as accuracy_router
from app.actions_api import router as actions_router
from app.metricflow_api import router as metricflow_router
from app.research_api import router as research_router
from app.golden_api import router as golden_router
from app.mdl_editor_api import router as mdl_editor_router
from app.diff_api import router as diff_router
from app.scope_audit_api import router as scope_audit_router
try:
    from app.sql_validator_api import router as sql_validator_router
except Exception:
    sql_validator_router = None  # SQL validator telemetry (migration 164)
# Obsidian-style steals (links/graph/journal/canvas/dataview/packs)
try:
    from app.links_api import router as links_router
except Exception as _e:
    links_router = None
try:
    from app.graph_api import router as graph_router
except Exception as _e:
    graph_router = None
try:
    from app.journal_api import router as journal_router
except Exception as _e:
    journal_router = None
try:
    from app.canvas_api import router as canvas_router
except Exception as _e:
    canvas_router = None
try:
    from app.dataview_api import router as dataview_router
except Exception as _e:
    dataview_router = None
try:
    from app.packs_api import router as packs_router
except Exception as _e:
    packs_router = None
try:
    from app.venture_api import router as venture_router
except Exception as _e:
    venture_router = None
try:
    from app.ops_api import router as ops_api_router
except Exception as _e:
    ops_api_router = None
try:
    from app.market_api import router as market_api_router
except Exception as _e:
    market_api_router = None
try:
    from app.supply_api import router as supply_api_router
except Exception as _e:
    supply_api_router = None
from app.suggested_rules import router as suggested_rules_router
from app.scores import router as scores_router
from app.export import router as export_router
from app.schedules import router as schedules_router
from app.learning import router as learning_router, templates_router as visibility_templates_router, marketplace_router as skill_marketplace_router, admin_router as engines_admin_router
# Industry preset agent template API removed.
agent_templates_router = None
from app.connectors import router as connectors_router
# pruned: sharepoint/gdrive/onedrive connectors (single-agent, file upload is the path)
sharepoint_router = None
gdrive_router = None
onedrive_router = None
try:
    from app.embed import router as embed_router
except ImportError:
    embed_router = None
try:
    from app.embed import blueprints_router as embed_blueprints_router
except ImportError:
    embed_blueprints_router = None
try:
    from app.embed import cache_admin_router as embed_cache_admin_router
except ImportError:
    embed_cache_admin_router = None
try:
    from app.brain import router as brain_router
except ImportError:
    brain_router = None
try:
    from app.brain_seeds import router as brain_seeds_router
except ImportError:
    brain_seeds_router = None
try:
    from app.brain_versions import router as brain_versions_router
except ImportError:
    brain_versions_router = None
try:
    from app.brain_unified import router as brain_unified_router
except Exception:
    brain_unified_router = None
try:
    from app.brain_actions import router as brain_actions_router
except Exception:
    brain_actions_router = None
try:
    from app.ontology_api import router as ontology_router
except ImportError:
    ontology_router = None
try:
    from app.customer_360 import router as customer_360_router
except ImportError:
    customer_360_router = None
try:
    from app.campaigns import router as campaigns_router
except ImportError:
    campaigns_router = None
try:
    from app.embeddings_api import router as embeddings_router
except ImportError:
    embeddings_router = None
try:
    from app.attribution import router as attribution_router
except Exception:
    attribution_router = None
try:
    from app.mrr_analytics import router as mrr_router
except Exception:
    mrr_router = None
# automl chassis dropped — replaced by LLM-conductor auto_ml tool
# agents_api (My Agent / per-user digital twin) REMOVED 2026-06-06 — dead in single-agent CityPharma
agents_api_router = None

# auto_apply_api DELETED 2026-05-23 (orphan, 0 frontend refs)

app.include_router(auth_router)
# Federated auth — LDAP + OIDC/SSO (boot-safe: guarded so a missing dep never blocks startup)
try:
    from app.auth_federation import fed_router
    app.include_router(fed_router)
except Exception as _fed_e:
    import logging as _fed_log
    _fed_log.getLogger(__name__).warning(f"federated auth router skipped: {_fed_e}")
# Admin ops endpoints (migrations status/apply-pending, daemon health) — Issues #11/#12/#13/#27
try:
    from app.admin_ops_api import router as _admin_ops_router
    app.include_router(_admin_ops_router)
except Exception as _e:
    import logging as _ao_log
    _ao_log.getLogger(__name__).warning(f"admin_ops_api not mounted: {_e}")
# Agent-OS admin backend (super-admin only). Migration 121.
try:
    from app.agent_os_admin_api import router as _agent_os_admin_router
    app.include_router(_agent_os_admin_router)
except Exception as _e:
    import logging as _aos_log
    _aos_log.getLogger(__name__).warning(f"agent_os_admin_api not mounted: {_e}")
app.include_router(projects_router)
# OpenAI-compatible API gateway (/api/v1) for external apps (e.g. PHP storefront)
try:
    from app.api_gateway import router as api_gateway_router
    app.include_router(api_gateway_router)
except Exception:
    _main_logging.exception("main: api_gateway router not mounted")
# Vertical workflow packs — schema-aware auto-install (Issue #4)
try:
    from app.vertical_packs_api import router as vpacks_router
    app.include_router(vpacks_router)
except Exception as _e:
    import logging as _vp_log
    _vp_log.getLogger(__name__).warning(f"vertical_packs_api not mounted: {_e}")
# Feature E — shareable read-only conversation links (public GET /api/s/{token})
try:
    from app.share_api import router as share_router
    app.include_router(share_router)
except Exception as _e:
    import logging as _sh_log
    _sh_log.getLogger(__name__).warning(f"share_api not mounted: {_e}")
# Feature D — admin "ask the logs" agent (super-admin gated)
try:
    from app.log_agent_api import router as log_agent_router
    app.include_router(log_agent_router)
except Exception as _e:
    import logging as _la_log
    _la_log.getLogger(__name__).warning(f"log_agent_api not mounted: {_e}")
app.include_router(upload_router)
# Training queue (Option B: in-process workers + Redis).
try:
    from app.training_queue_api import router as _tq_router
    app.include_router(_tq_router)
    logger.info("training_queue router mounted")
except Exception as _e:
    logger.warning(f"training_queue router not mounted: {_e}")
# GB-scale streaming ingest (CSV/Excel/Parquet) — no RAM cap, COPY-based.
try:
    from app.upload_stream import router as _upload_stream_router
    app.include_router(_upload_stream_router)
    logger.info("upload_stream router mounted")
except Exception as _e:
    logger.warning(f"upload_stream router not mounted: {_e}")
app.include_router(rules_router)
app.include_router(dashboards_router)
app.include_router(dashboards_v2_router)
if dashboard_to_deck_router is not None:
    app.include_router(dashboard_to_deck_router)
app.include_router(accuracy_router)
app.include_router(actions_router)
app.include_router(metricflow_router)
app.include_router(research_router)
app.include_router(golden_router)
app.include_router(mdl_editor_router)
app.include_router(diff_router)
app.include_router(scope_audit_router)
if sql_validator_router is not None:
    try:
        app.include_router(sql_validator_router)
    except Exception as _e:
        logger.warning(f"sql_validator_router include failed: {_e}")
# Obsidian-style steals
for _r in (links_router, graph_router, journal_router, canvas_router, dataview_router, packs_router, venture_router, ops_api_router, market_api_router):
    if _r is not None:
        try:
            app.include_router(_r)
        except Exception as _e:
            import logging as _lg
            _lg.getLogger(__name__).warning("steal router not registered: %s", _e)
app.include_router(suggested_rules_router)
app.include_router(scores_router)
app.include_router(export_router)
try:
    from app.slides_api import router as slides_router
    app.include_router(slides_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("slides_api router not registered: %s", _e)
try:
    from app.deep_deck_api import router as deep_deck_router
    app.include_router(deep_deck_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("deep_deck_api router not registered: %s", _e)
# Phase 7: deck distribution (schedule → render → email/Slack/PDF). Stub-safe
# when SMTP/Slack creds missing. Kill switch: DECK_DISTRIBUTION_DISABLED=1.
import os as _os_dd
if _os_dd.getenv("DECK_DISTRIBUTION_DISABLED", "").lower() not in ("1", "true", "yes"):
    try:
        from app.deck_distribution import combined_router as deck_distribution_router
        app.include_router(deck_distribution_router)
    except Exception as _e:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("deck_distribution router not registered: %s", _e)
app.include_router(schedules_router)
app.include_router(learning_router)
try:
    from app.training_api import router as _training_router
    app.include_router(_training_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("training_api router not registered: %s", _e)
try:
    from app.datasource_api import router as _datasource_router
    app.include_router(_datasource_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("datasource_api router not registered: %s", _e)
try:
    from app.overview_api import router as _overview_router
    app.include_router(_overview_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("overview_api router not registered: %s", _e)
try:
    from app.wiki_api import router as _wiki_router
    app.include_router(_wiki_router)
except Exception as _e:  # noqa: BLE001
    import logging as _lg
    _lg.getLogger(__name__).warning("wiki_api router not registered: %s", _e)
app.include_router(engines_admin_router)
app.include_router(visibility_templates_router)
app.include_router(skill_marketplace_router)
if agent_templates_router is not None:
    app.include_router(agent_templates_router)
app.include_router(connectors_router)
if embed_router is not None:
    app.include_router(embed_router)
if embed_blueprints_router is not None:
    app.include_router(embed_blueprints_router)
if embed_cache_admin_router is not None:
    app.include_router(embed_cache_admin_router)
if brain_router is not None:
    app.include_router(brain_router)
if brain_seeds_router is not None:
    app.include_router(brain_seeds_router)
if brain_versions_router is not None:
    app.include_router(brain_versions_router)
if brain_unified_router is not None:
    app.include_router(brain_unified_router)
if brain_actions_router is not None:
    app.include_router(brain_actions_router)
if ontology_router is not None:
    app.include_router(ontology_router)
if customer_360_router is not None:
    app.include_router(customer_360_router)
if campaigns_router is not None:
    app.include_router(campaigns_router)
if embeddings_router is not None:
    app.include_router(embeddings_router)
if attribution_router is not None:
    app.include_router(attribution_router)
if mrr_router is not None:
    app.include_router(mrr_router)
if agents_api_router is not None:
    app.include_router(agents_api_router)
# VentureDesk pillars 1-2 (deal analyst + ops + market sentinel)
if venture_router is not None:
    app.include_router(venture_router)
if ops_api_router is not None:
    app.include_router(ops_api_router)
if market_api_router is not None:
    app.include_router(market_api_router)
# Sprint 3 — Supply Sentry
if supply_api_router is not None:
    app.include_router(supply_api_router)
# auto_config_router DELETED 2026-05-23

import logging as _gate_log
_glog = _gate_log.getLogger(__name__)

# recall_api DELETED 2026-05-23 (orphan, 0 frontend refs)
# investment_api DELETED 2026-05-23 (orphan, 0 frontend refs)

# sim_api router DELETED 2026-05-23 (sim chassis removed)


# Lightweight global flags — lets the frontend hide opt-in surfaces (Scenario
# Lab, AutoML) whose backend routers are env-gated, instead of showing dead tabs.
@app.get("/api/flags")
def _global_flags():
    import os as _os_flags
    _on = lambda k: _os_flags.getenv(k, "").strip().lower() in ("1", "true", "yes", "on")
    from dash.single_agent import is_single_agent, locked_slug, product_name
    # Integration kill switches (super-admin). Fail-open to enabled.
    try:
        from dash.admin.settings import integrations_enabled
        _integ = integrations_enabled()
    except Exception:
        _integ = {"gateway": True, "embed": True}
    return {
        # sim_lab_enabled DELETED 2026-05-23
        "automl_enabled": _on("AUTOML_ENABLED"),
        "investment_vertical_enabled": _on("INVESTMENT_VERTICAL_ENABLED"),
        # Integration surface kill switches (API Gateway / Embed)
        "gateway_enabled": bool(_integ.get("gateway", True)),
        "embed_enabled": bool(_integ.get("embed", True)),
        # Single-agent product (CityPharma)
        "single_agent": is_single_agent(),
        "locked_slug": locked_slug() if is_single_agent() else None,
        "product_name": product_name(),
        # Canonical public origin for embed snippets/SDK/docs. Set PUBLIC_URL
        # (or WEBUI_URL) to the AWS domain; blank → frontend falls back to
        # window.location.origin.
        "public_base_url": (_os_flags.getenv("PUBLIC_URL") or _os_flags.getenv("WEBUI_URL") or "").rstrip("/"),
    }


# Correction-learning loop — capture edits, extract durable rules
try:
    from app.corrections_api import router as corrections_router
    app.include_router(corrections_router)
except Exception as e:
    logger.warning(f"corrections_api not loaded: {e}")

# Per-project user-configurable metric system
try:
    from app.metrics_api import router as metrics_api_router
    app.include_router(metrics_api_router)
except Exception as e:
    logger.warning(f"metrics_api not loaded: {e}")

# B2: Deterministic zero-LLM auto-linker for knowledge graph
try:
    from app.linker_api import router as linker_router
    app.include_router(linker_router)
except Exception as e:
    logger.warning(f"linker_api not loaded: {e}")

# pages_api DELETED 2026-05-23 (orphan, 0 frontend refs)

# Build B3 — hybrid retrieval (BM25 + vector + RRF + multi-query expansion)
try:
    from app.retrieval_api import router as retrieval_router
    app.include_router(retrieval_router)
except Exception as e:
    logger.warning(f"retrieval_api not loaded: {e}")

# Human-in-loop (HITL + Approvals) UNMOUNTED 2026-05-20 — over-engineered, no
# agent produces requests (confirm_dangerous_op/approval tools dropped).
# app.hitl_api (→ /api/hitl) kept on disk. app.hitl_requests_api DELETED
# (no producer). Extended-workflows router (workflows_extended_api) DELETED —
# duplicated app.workflows_api (→ /api/workflows, mounted below).

# Zero-LLM Entity Linker — KG extraction cost stats.
try:
    from app.entity_linker_api import router as entity_linker_router
    app.include_router(entity_linker_router)
except Exception as e:
    logger.warning(f"entity_linker_api not loaded: {e}")

# Dash-OS Phase 1C — @approval framework UNMOUNTED 2026-05-20 (human-in-loop,
# no producer). Router kept on disk (app.approval_api → /api/approvals).
# NOTE: skill-draft approval queue (Phase 10, below) is separate and stays.

# Dash-OS Phase 2A — Reporter file generation (PDF/PPTX/CSV/XLSX/DOCX/JSON/MD)
try:
    from app.reporter_api import router as reporter_router
    app.include_router(reporter_router)
except Exception as e:
    logger.warning(f"reporter_api not loaded: {e}")

# Artifact Gallery — auto-captured run outputs (CSV/PNG/JSON/PDF/MD/...)
try:
    from app.artifacts_api import router as artifacts_router
    app.include_router(artifacts_router)
except Exception as e:
    logger.warning(f"artifacts_api not loaded: {e}")

# Dash-OS Phase 2C — MCP client server registry + tool bindings + invocation audit
try:
    from app.mcp_api import router as mcp_router
    app.include_router(mcp_router)
except Exception as e:
    logger.warning(f"mcp_api not loaded: {e}")

# Dash MCP Server (provider mode) — exposes Dash tools (sql_query, recall,
# apply_skill, search_brain, list_projects, …) over HTTP JSON-RPC at
# /api/mcp/rpc so external coding agents (ChatGPT, Claude Desktop HTTP,
# n8n) can call Dash. Stdio transport lives in `python -m mcp_server`.
try:
    from mcp_server.http_server import admin_router as mcp_provider_admin_router
    from mcp_server.http_server import router as mcp_provider_router
    app.include_router(mcp_provider_router)
    app.include_router(mcp_provider_admin_router)
except Exception as e:
    logger.warning(f"mcp_server (provider) not loaded: {e}")

# Dash-OS Phase 2E — Agent-callable schedules CRUD + run history
try:
    from app.agent_schedules_api import router as agent_schedules_router
    app.include_router(agent_schedules_router)
except Exception as e:
    logger.warning(f"agent_schedules_api not loaded: {e}")

# Dash-OS Phase 3 — DAG workflow engine + 5 reference workflows
try:
    from app.workflows_api import router as workflows_router
    app.include_router(workflows_router)
except Exception as e:
    logger.warning(f"workflows_api not loaded: {e}")

# Dash-OS Phase 4 — Skills system + 10 builtin domain experts
try:
    from app.skills_api import router as skills_router
    app.include_router(skills_router)
except Exception as e:
    logger.warning(f"skills_api not loaded: {e}")

# B4 — Skill resolver (LLM intent-classification router)
try:
    from app.resolver_api import router as resolver_router
    app.include_router(resolver_router)
except Exception as e:
    logger.warning(f"resolver_api not loaded: {e}")

# Dash-OS Phase 5 — Comm surfaces (Slack/Email/Voice)
try:
    from app.channels_api import router as channels_router
    app.include_router(channels_router)
except Exception as e:
    logger.warning(f"channels_api not loaded: {e}")

# Dash-OS Phase 6 — 4-layer eval framework + secret-leak audit
try:
    from app.evals_api import router as evals_router
    app.include_router(evals_router)
except Exception as e:
    logger.warning(f"evals_api not loaded: {e}")

# Dash-OS Phase 7 — Entity memory + agentic state + run-context audit
try:
    from app.memory_api import router as memory_router
    app.include_router(memory_router)
except Exception as e:
    logger.warning(f"memory_api not loaded: {e}")

# Dash-OS Phase 10 — Skill draft approval queue
try:
    from app.skill_drafts_api import router as skill_drafts_router
    app.include_router(skill_drafts_router)
except Exception as e:
    logger.warning(f"skill_drafts_api not loaded: {e}")

# minions_api DELETED 2026-05-23 (orphan, 0 frontend refs)
# custom_agents_api DELETED 2026-05-23 (orphan, 0 frontend refs)

# Public embed endpoints (Phase 2) — no-auth widget bootstrap (session/create)
try:
    from app.embed_public import router as embed_public_router
    app.include_router(embed_public_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"embed_public router not loaded: {_e}")

# Ontology public read API (Phase E) — Bearer-key gated, mounted at /v1/ontology
try:
    from app.ontology_public_api import router as ontology_public_router
    app.include_router(ontology_public_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"ontology_public_api router not loaded: {_e}")

# Self-learning cycle API (cycle orchestrator + SSE endpoints + stats)
try:
    from app.learning_api import router as learning_cycle_router
    app.include_router(learning_cycle_router)
    from app.learning_api import projects_learning_router
    app.include_router(projects_learning_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"learning_api not loaded: {_e}")

# Branding API — public endpoints (logo, theme, company info) for white-label support
try:
    from app.branding import router as branding_router
    app.include_router(branding_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"branding router not loaded: {_e}")

# Admin settings API — runtime config managed via UI (super-admin only)
try:
    from app.admin_api import router as admin_router
    app.include_router(admin_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"admin_api router not loaded: {_e}")

# Traces API — admin observability over public.dash_traces (super-admin only)
try:
    from app.traces_api import router as traces_router
    app.include_router(traces_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"traces_api router not loaded: {_e}")

# Table usage telemetry — per-table query stats from dash_traces (Task #2)
try:
    from app.table_usage_api import router as table_usage_router
    app.include_router(table_usage_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"table_usage_api router not loaded: {_e}")

# Drift detection API — per-source drift events (schema/ndv/row_count/watermark/pii)
try:
    from app.drift_api import router as drift_router
    app.include_router(drift_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"drift_api not loaded: {_e}")

# Federation smoke test endpoint
try:
    from app.connectors_test_federation import router as fed_test_router
    app.include_router(fed_test_router)
except Exception:
    pass

# Federation health API — per-project + cross-project federation analytics
try:
    from app.federation_health_api import router as fed_health_router
    app.include_router(fed_health_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"federation_health_api not loaded: {_e}")

# Cross-agent workflows hub — aggregates workflows across owned + shared projects
try:
    from app.agent_os_workflows import router as agent_os_workflows_router
    app.include_router(agent_os_workflows_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"agent-os workflows router not loaded: {_e}")

# Workflow cron schedules (Agent #4) — per-workflow cron entries + run-now
try:
    from app.workflow_schedules_api import router as workflow_schedules_router
    app.include_router(workflow_schedules_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"workflow_schedules_api not loaded: {_e}")

# workforce_api DELETED 2026-05-23

# Daily digest API — SMTP + Slack delivery + scheduler test/config endpoints
try:
    from app.digest_api import router as digest_router
    app.include_router(digest_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"digest_api not loaded: {_e}")

# Telemetry admin API — super-admin observability (live/cost/errors/latency/etc)
try:
    from app.telemetry_admin_api import router as telemetry_admin_router
    app.include_router(telemetry_admin_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"telemetry_admin_api not loaded: {_e}")

# Usage API — super-admin unified usage/cost dashboard (v_usage_unified spine)
try:
    from app.usage_api import router as usage_router
    app.include_router(usage_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"usage_api not loaded: {_e}")

# Connector subsystem (admin CRUD + user-facing query) — connectors_contract §8
try:
    from app.admin_connectors import router as admin_connectors_router
    app.include_router(admin_connectors_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"admin_connectors router not loaded: {_e}")

# Governance backend (policies, approvals, data zones, PII, retention, audit, compliance)
try:
    from app.governance_api import router as governance_router
    app.include_router(governance_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"governance_api router not loaded: {_e}")
try:
    from app.connectors_v2 import router as connectors_v2_router
    app.include_router(connectors_v2_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"connectors_v2 router not loaded: {_e}")
try:
    from app.connector_obo_api import router as connector_obo_router
    app.include_router(connector_obo_router)
except Exception as _e:
    import logging as _logging
    _logging.warning(f"connector_obo_api router not loaded: {_e}")

# ---------------------------------------------------------------------------
# CORS Middleware (production)
# ---------------------------------------------------------------------------
from fastapi.middleware.cors import CORSMiddleware

_cors_origins = [o.strip() for o in getenv("CORS_ORIGINS", "").split(",") if o.strip()]
# Fall back to PUBLIC_URL before the wildcard so a normal AWS deploy (which sets
# PUBLIC_URL) gets a correct, credentialed allow-list without extra config.
if (not _cors_origins or _cors_origins == ["*"]):
    _pub = (getenv("PUBLIC_URL") or getenv("WEBUI_URL") or "").strip().rstrip("/")
    if _pub:
        _cors_origins = [_pub]
# allow_credentials + "*" is unsafe (and the browser rejects it). If we still
# have no explicit origin, allow all BUT without credentials so it can't be
# abused for credentialed cross-site reads.
_cors_allow_credentials = True
if not _cors_origins or _cors_origins == ["*"]:
    import logging
    logging.warning("CORS_ORIGINS/PUBLIC_URL not set — allowing all origins WITHOUT credentials. "
                    "Set CORS_ORIGINS (or PUBLIC_URL) in .env for production.")
    _cors_origins = ["*"]
    _cors_allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Environment Validation
# ---------------------------------------------------------------------------
_required_env = ["OPENROUTER_API_KEY"]
for var in _required_env:
    if not getenv(var):
        import logging
        logging.warning(f"WARNING: Required env var {var} is not set!")

# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Integration kill-switch cache: (value_dict, ts). Reading the settings on
# every request would hit the DB; cache for ~30s and fail-open to enabled.
import time as _integ_time
_INTEG_CACHE: list = [None, 0.0]
_INTEG_TTL_S = 30.0


def _integrations_state() -> dict:
    """Cached {gateway: bool, embed: bool}. Fail-open to enabled on any error."""
    now = _integ_time.time()
    cached, ts = _INTEG_CACHE[0], _INTEG_CACHE[1]
    if cached is not None and (now - ts) < _INTEG_TTL_S:
        return cached
    try:
        from dash.admin.settings import integrations_enabled
        state = integrations_enabled()
    except Exception:
        state = {"gateway": True, "embed": True}
    _INTEG_CACHE[0] = state
    _INTEG_CACHE[1] = now
    return state


class AuthMiddleware(BaseHTTPMiddleware):
    SKIP_PATHS = {"/health", "/api/health", "/api/flags", "/", "/info", "/config", "/api/auth/login", "/api/auth/register", "/api/auth/methods", "/api/auth/ldap/login", "/api/sharepoint/callback", "/api/gdrive/callback", "/api/onedrive/callback", "/api/embed/session/create", "/api/embed/chat", "/api/embed/chat/stream", "/api/embed/widget.js", "/api/embed/docs"}
    SKIP_PREFIXES = ("/ui", "/docs", "/openapi.json", "/redoc", "/api/branding", "/v1/ontology", "/brand", "/decks", "/api/health", "/health", "/api/embed/try", "/api/embed/config", "/api/embed/sdk", "/api/embed/logo", "/api/s/", "/api/v1/docs", "/api/auth/oidc/")

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path

        # Redirect root to UI
        if path == "/":
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/ui/home", status_code=302)

        # Integration kill switches — block disabled surfaces with a 403.
        # Never block /api/flags itself or /api/admin/* (operators must be able
        # to read state + re-enable). Fail-open on any error.
        if path.startswith("/api/v1") or path.startswith("/api/embed"):
            try:
                _state = _integrations_state()
                if path.startswith("/api/v1") and not _state.get("gateway", True):
                    return JSONResponse({"detail": "API Gateway is disabled"}, status_code=403)
                if path.startswith("/api/embed") and not _state.get("embed", True):
                    return JSONResponse({"detail": "Embed is disabled"}, status_code=403)
            except Exception:
                pass

        # Skip auth for UI, docs, health, and auth endpoints
        if path in self.SKIP_PATHS or any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        # Check token
        from app.auth import get_current_user, validate_token
        user = get_current_user(request)
        # Fallback for new-tab links (e.g. embed sandbox): allow ?token= on GET.
        if not user and request.method == "GET":
            qtok = request.query_params.get("token")
            if qtok:
                user = validate_token(qtok)
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        # Attach user to request state
        request.state.user = user

        # Optional X-Scope-Id header → validate + thread into RLS ContextVar
        scope_id = request.headers.get("X-Scope-Id") or request.headers.get("x-scope-id")
        request.state.scope_id = None
        if scope_id:
            try:
                from app.auth import validate_scope, get_user_scopes
                project_slug = (
                    request.query_params.get("project_slug")
                    or request.path_params.get("slug")
                    or request.headers.get("X-Project-Slug")
                )
                if project_slug and validate_scope(user["user_id"], project_slug, scope_id):
                    request.state.scope_id = scope_id
                    # Look up role for this scope so RLS can use it
                    role = "staff"
                    for s in get_user_scopes(user["user_id"], project_slug):
                        if s["scope_id"] == scope_id:
                            role = s["role"] or "staff"
                            break
                    try:
                        from dash.tools.skill_refinery import set_request_context
                        set_request_context(
                            user_id=user["user_id"],
                            user_attrs={"store_id": scope_id, "region": scope_id, "role": role},
                        )
                    except Exception:
                        pass
            except Exception:
                pass

        # Phase 4 — gate query_intent by visibility role (silent downgrade, fail-open).
        try:
            project_slug = (
                request.query_params.get("project_slug")
                or request.path_params.get("slug")
                or request.headers.get("X-Project-Slug")
            )
            requested_intent = (request.headers.get("X-Query-Intent") or "private").strip().lower()
            if requested_intent not in ("private", "network", "public"):
                requested_intent = "private"
            allowed_intent = "private"
            if project_slug:
                try:
                    from dash.policy.roles import get_user_role, get_role_intents
                    role_name = get_user_role(user["user_id"], project_slug)
                    allowed = get_role_intents(project_slug, role_name)
                    if requested_intent in allowed:
                        allowed_intent = requested_intent
                    else:
                        if requested_intent != "private":
                            import logging
                            logging.getLogger(__name__).warning(
                                "visibility role downgrade: user=%s slug=%s requested=%s role=%s allowed=%s",
                                user.get("user_id"), project_slug, requested_intent, role_name, allowed,
                            )
                except Exception:
                    allowed_intent = "private"
            try:
                from dash.tools.skill_refinery import set_request_context
                set_request_context(
                    query_intent=allowed_intent,
                    viewer_user_id=user.get("user_id"),
                    viewer_scope_id=request.state.scope_id,
                )
            except Exception:
                pass
        except Exception:
            pass

        return await call_next(request)


# Rate-limit middleware (Track C3). Added BEFORE AuthMiddleware so that
# AuthMiddleware (outermost) runs first and populates request.state.user_id
# before the rate-limit identity resolver reads it.
# Whitelisted paths (/api/health, /metrics, /health, /ui, ...) bypass. 429
# response includes Retry-After per RFC 6585.
try:
    from app.rate_limit import RateLimitMiddleware as _RateLimitMW
    app.add_middleware(_RateLimitMW)
except Exception as _rl_e:  # noqa: BLE001
    import logging as _rl_log
    _rl_log.getLogger(__name__).warning(f"rate-limit middleware not loaded: {_rl_e}")

app.add_middleware(AuthMiddleware)

# ---------------------------------------------------------------------------
# Frontend (Brutalist Chat UI)
# ---------------------------------------------------------------------------
_frontend_build = Path(__file__).parent.parent / "frontend" / "build"

if _frontend_build.exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    # Serve SvelteKit _app assets
    _app_dir = _frontend_build / "_app"
    if _app_dir.exists():
        app.mount("/ui/_app", StaticFiles(directory=str(_app_dir)), name="ui-app")

    # Serve bundled brand assets (logo, favicon)
    _brand_dir = _frontend_build / "brand"
    if _brand_dir.exists():
        app.mount("/brand", StaticFiles(directory=str(_brand_dir)), name="brand")

    # Serve deck QA jpgs (per-deck preview thumbnails). Fail-soft: a non-writable
    # /app/knowledge volume (e.g. root-owned mount) must never crash worker boot —
    # skip the mount and log instead. Ownership is fixed in the Dockerfile; this is
    # defense-in-depth so a misconfigured volume degrades gracefully.
    import os as _os
    _decks_dir = "/app/knowledge/_decks"
    try:
        _os.makedirs(_decks_dir, exist_ok=True)
        app.mount("/decks", StaticFiles(directory=_decks_dir, follow_symlink=True), name="decks")
    except OSError as _e:
        _main_logging.getLogger("dash.main").warning(
            "/decks mount skipped — cannot create %s (%s). "
            "Fix volume ownership: chown -R dash:dash /app/knowledge", _decks_dir, _e)

    @app.get("/ui/{path:path}")
    @app.get("/ui")
    def serve_ui(path: str = "") -> FileResponse:
        """Serve the Dash chat UI."""
        return FileResponse(str(_frontend_build / "index.html"))


# ---------------------------------------------------------------------------
# Notifications + Audit API
# ---------------------------------------------------------------------------

@app.get("/api/me")
def get_me(request: Request):
    """Return the current authenticated user's identity (used by frontend hero greeting)."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    # Pull a richer profile if available
    full_name = None
    email = None
    try:
        with _shared_engine.connect() as conn:
            row = conn.execute(sa_text(
                "SELECT full_name, email FROM public.dash_users WHERE id = :uid"
            ), {"uid": user["user_id"]}).fetchone()
            if row:
                full_name = row[0]
                email = row[1]
    except Exception:
        pass
    return {
        "user_id": user.get("user_id"),
        "username": user.get("username"),
        "name": full_name or user.get("username"),
        "full_name": full_name,
        "email": email,
        "is_super": user.get("is_super", False),
        "is_admin": user.get("is_admin", user.get("is_super", False)),
    }


@app.get("/api/notifications")
def get_notifications(request: Request):
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        return {"notifications": []}
    _eng = _shared_engine
    with _eng.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT id, type, title, message, read, created_at FROM public.dash_notifications "
            "WHERE user_id = :uid ORDER BY created_at DESC LIMIT 30"
        ), {"uid": user["user_id"]}).fetchall()
    return {"notifications": [
        {"id": r[0], "type": r[1], "title": r[2], "message": r[3], "read": r[4], "created_at": str(r[5]) if r[5] else None}
        for r in rows
    ], "unread": sum(1 for r in rows if not r[4])}


@app.post("/api/notifications/{nid}/read")
def mark_notification_read(nid: int, request: Request):
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        return {"status": "skip"}
    _eng = _shared_engine
    with _eng.connect() as conn:
        conn.execute(sa_text("UPDATE public.dash_notifications SET read = TRUE WHERE id = :id AND user_id = :uid"), {"id": nid, "uid": user["user_id"]})
        conn.commit()
    return {"status": "ok"}


@app.post("/api/notifications/read-all")
def mark_all_read(request: Request):
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        return {"status": "skip"}
    _eng = _shared_engine
    with _eng.connect() as conn:
        conn.execute(sa_text("UPDATE public.dash_notifications SET read = TRUE WHERE user_id = :uid"), {"uid": user["user_id"]})
        conn.commit()
    return {"status": "ok"}


@app.get("/api/audit-log")
def get_audit_log(request: Request):
    """Get audit log (super admin only)."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        return {"logs": []}
    from app.auth import SUPER_ADMIN
    if user.get("username") != SUPER_ADMIN:
        return {"logs": []}
    _eng = _shared_engine
    with _eng.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT id, username, action, resource_type, resource_id, details, created_at "
            "FROM public.dash_audit_log ORDER BY created_at DESC LIMIT 100"
        )).fetchall()
    return {"logs": [
        {"id": r[0], "username": r[1], "action": r[2], "resource_type": r[3], "resource_id": r[4], "details": r[5], "created_at": str(r[6]) if r[6] else None}
        for r in rows
    ]}


# Alias for frontend that calls /api/auth/admin/audit-log
@app.get("/api/auth/admin/audit-log")
def get_audit_log_admin_alias(request: Request):
    return get_audit_log(request)


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------

@app.get("/api/search")
def global_search(q: str, request: Request):
    """Search across projects, chats, tables, rules for the current user."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user or not q or len(q) < 2:
        return {"results": []}
    _eng = _shared_engine
    results = []
    query = f"%{q.lower()}%"

    with _eng.connect() as conn:
        # Search projects
        rows = conn.execute(sa_text(
            "SELECT slug, name, agent_name FROM public.dash_projects "
            "WHERE user_id = :uid AND (LOWER(name) LIKE :q OR LOWER(agent_name) LIKE :q) LIMIT 5"
        ), {"uid": user["user_id"], "q": query}).fetchall()
        for r in rows:
            results.append({"type": "project", "title": r[2], "subtitle": r[1], "url": f"/ui/project/{r[0]}"})

        # Search chat sessions
        rows = conn.execute(sa_text(
            "SELECT session_id, first_message, project_slug FROM public.dash_chat_sessions "
            "WHERE user_id = :uid AND LOWER(first_message) LIKE :q ORDER BY updated_at DESC LIMIT 5"
        ), {"uid": user["user_id"], "q": query}).fetchall()
        for r in rows:
            results.append({"type": "chat", "title": r[1], "subtitle": r[2] or "Dash Agent", "url": f"/ui/project/{r[2]}" if r[2] else "/ui/chat"})

    return {"results": results}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Reasoning Mode Detection (backend)
# ---------------------------------------------------------------------------
import re as _re

_DEEP_KEYWORDS = _re.compile(
    r'\b(why|compare|explain|suggest|recommend|correlate|analyze|break down|'
    r'what should|how can|investigate|diagnose|root cause)\b', _re.IGNORECASE
)

# Smart routing: detect if question needs data, context, or BOTH
_DATA_KEYWORDS = _re.compile(
    r'\b(how many|total|count|sum|average|revenue|sales|amount|cost|growth|'
    r'margin|budget|show me|list|top \d|trend|forecast|predict)\b', _re.IGNORECASE
)
_CONTEXT_KEYWORDS = _re.compile(
    r'\b(why|what caused|context|document|slide|pptx|pdf|report|presentation|'
    r'according to|what did|summary|narrative|board|executive|explain why|'
    r'reason|background|mentioned|decision|risk)\b', _re.IGNORECASE
)


def _detect_routing_hint(message: str) -> str:
    """Detect if question needs data agent, context agent, or BOTH.
    Returns: 'data', 'context', or 'both'."""
    has_data = bool(_DATA_KEYWORDS.search(message))
    has_context = bool(_CONTEXT_KEYWORDS.search(message))
    if has_data and has_context:
        return "both"
    if has_context and not has_data:
        return "context"
    return "data"  # default: most questions need data


_CHITCHAT_RE = _re.compile(
    r'^\s*(who\s+(are|r)\s+(you|u)|what\s+(can|are)\s+(you|u)|what\s+(do|can)\s+(you|u)\s+(do|can)|'
    r'what\s+u\s+can\s+do|what\s+can\s+u\s+do|introduce|tell\s+me\s+about\s+yourself|'
    r'help|hello|hi|hey|thanks|thank\s+you|bye|good\s+(morning|evening|afternoon)|'
    r'how\s+(are|r)\s+(you|u)|what\s+information\s+(do\s+you|u)\s+have|'
    r'what\s+can\s+you\s+help)\b',
    _re.IGNORECASE,
)


def _is_chitchat(message: str) -> bool:
    """Conversational/meta/capability question — answer like a pharmacist, plain prose, no dashboard tags."""
    m = (message or "").strip()
    if not m:
        return False
    if len(m) <= 60 and _CHITCHAT_RE.search(m):
        return True
    return False


def _chitchat_instructions() -> str:
    """Plain-prose directive for conversational/capability questions. No cards, no tags, no charts."""
    return (
        "CONVERSATIONAL MODE — the user asked a casual, greeting, or capability question, NOT a data question. "
        "Answer like a friendly, knowledgeable pharmacist talking to a colleague. "
        "Plain conversational prose only. 2-5 short sentences or a tight bulleted list. "
        "ABSOLUTELY DO NOT output ANY of these structured tags: [MODE:...], [HEADLINE:...], [CONFIDENCE:...], "
        "[CONFIDENCE_BREAKDOWN:...], [SO_WHAT:...], [FINDING:...], [SEGMENT:...], [ANCHOR:...], [KPI:...], "
        "[RELATED:...], [BECAUSE:...], [KILL:...], [ASSUMPTION:...]. No card title, no confidence breakdown, "
        "no tables, no charts, no SOURCES section. Just talk normally. "
        "If asked what you can do, briefly describe in plain words that you analyse pharmacy stock data — "
        "stock levels, product/medicine info, substitutes, prices, categories across stores — and invite a question."
    )


def _apply_reasoning_mode(message: str, mode: str, analysis_type: str = "auto") -> str:
    """Apply FAST/DEEP reasoning + analysis type. Called server-side."""
    parts = []

    # Analysis type → TOOL CALL instruction (forces Analyst to use the right specialist tool)
    if analysis_type and analysis_type != "auto":
        type_instructions = {
            "descriptive": "ANALYSIS TYPE: DESCRIPTIVE. Answer directly with key metrics and a clean data table. Use run_sql to query the data. Keep it concise. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "diagnostic": "ANALYSIS TYPE: DIAGNOSTIC. You MUST call the diagnostic_analysis tool to decompose the metric into sub-dimensions and find what's driving the result. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "comparative": "ANALYSIS TYPE: COMPARATIVE. You MUST call the comparator_analysis tool to show period-over-period comparison with MoM and YoY deltas. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "trend": "ANALYSIS TYPE: TREND. You MUST call the trend_analysis tool to show the metric over time with moving averages and direction detection. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "predictive": "ANALYSIS TYPE: PREDICTIVE. You MUST call the run_forecast tool to generate a time-series forecast with confidence intervals. If run_forecast is not available, use trend_analysis and extrapolate. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "prescriptive": "ANALYSIS TYPE: PRESCRIPTIVE. You MUST call the prescriptive_analysis tool to generate actionable recommendations with expected quantified impact. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "anomaly": "ANALYSIS TYPE: ANOMALY. You MUST call the anomaly_analysis tool to detect Z-score outliers across numeric columns. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "root_cause": "ANALYSIS TYPE: ROOT CAUSE. You MUST call the root_cause_analysis tool to iteratively drill down from top-level metric to specific cause. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "pareto": "ANALYSIS TYPE: PARETO. You MUST call the pareto_analysis tool to sort by impact and calculate cumulative percentage (80/20 rule). Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "scenario": "ANALYSIS TYPE: SCENARIO. You MUST call the planner_analysis tool to model base case (60%), upside (25%), and downside (15%) scenarios. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
            "benchmark": "ANALYSIS TYPE: BENCHMARK. You MUST call the benchmark_analysis tool to compare entities against the group average. Pass the user's question and project slug. After getting the tool result, format the response with [KPI:...] tags for key metrics and [CONFIDENCE:...] tag.",
        }
        if analysis_type in type_instructions:
            parts.append(type_instructions[analysis_type])

    # Determine actual mode (auto-detect for auto)
    actual_mode = mode
    if mode == "auto":
        is_deep = bool(_DEEP_KEYWORDS.search(message))
        if not is_deep:
            is_deep = len(_re.findall(r'\band\b', message, _re.IGNORECASE)) >= 2 or message.count('?') >= 2
        actual_mode = "deep" if is_deep else "fast"

    # DEEP MODE — think out loud. Narrate the plan via the `think` tool BEFORE
    # any querying, and use think/analyze to narrate between tool calls. This
    # makes the trace show named reasoning steps (OpenAI-style). FAST mode adds
    # NOTHING here so it stays sub-second.
    if actual_mode == "deep":
        parts.insert(0,
            "DEEP MODE: Before querying, call `think(title='Planning the analysis', "
            "thought='<your plan>')`. Narrate your reasoning as short titled steps via "
            "think/analyze before and between tool calls. Then execute."
        )

    # STRONG mode enforcement — these go at the END so the LLM follows them
    if actual_mode == "fast":
        parts.append(
            "CRITICAL STYLE RULE — FAST MODE (McKinsey Pyramid Principle): "
            "Answer-first. Top line = governing thought. No buildup. "
            "Structure: ONE bold verdict sentence → SO_WHAT tag → 2-3 MECE FINDING tags ranked by impact → "
            "1-2 short paragraphs of WHY → CONFIDENCE_BREAKDOWN tag → 1 ANCHOR tag for context → RELATED pills. "
            "Up to 10 sentences + up to 2 tables. Numbers > adjectives. "
            "Do NOT show a SOURCES section (frontend renders separately). "
            "Do NOT write 'Related questions'/'Follow-up questions' in markdown — frontend renders [RELATED:] tags as pills. "
            "Include [MODE:fast] at the start. "
            "REQUIRED TAGS (frontend renders these visually — output structured, not prose): "
            "- [HEADLINE:short title 4-10 words summarizing the answer] — MUST appear right after [MODE:fast]. "
            "  Example: [HEADLINE:80% Pareto Analysis — Distribution of generic names] "
            "- [CONFIDENCE:HIGH|MEDIUM|LOW] right after HEADLINE, based on data quality. "
            "- [CONFIDENCE_BREAKDOWN:dq|qm|rp] three integers 0-100 for data-quality, query-match, reasoning. "
            "  Example: [CONFIDENCE_BREAKDOWN:85|90|78] "
            "- [SO_WHAT:action|owner|effort|risk] — decision-ready next move. "
            "  Example: [SO_WHAT:Fix top-3 SKU stockouts to recover ₹1.6Cr/mo|Supply Ops|2wk|Low] "
            "- 2-3 [FINDING:rank|text|impact|severity] tags. severity ∈ {HIGH, MED, LOW}. Rank by $ impact. MECE — non-overlapping. "
            "  Example: [FINDING:1|SKU-A47 stockout 42 days/qtr in Zone-3|₹68L loss|HIGH] "
            "- 1 [ANCHOR:comparison] tag making the headline number meaningful. "
            "  Example: [ANCHOR:= 12% of zone revenue, 2.3× cost of fix] "
            "- 2-4 [KPI:value|label|change] lines for headline metrics. "
            "- In tables, TREND column with ▲ +5% / ▼ -2% / ━ 0%. "
            "- 3 [RELATED:question] tags for drill-down. "
            "Do NOT output [IMPACT:...] tags. Do NOT invent numbers — every figure must come from the SQL result."
        )
    else:
        parts.append(
            "CRITICAL STYLE RULE — DEEP MODE (McKinsey/BCG full pyramid): "
            "Answer-first. Pyramid Principle: governing thought → grouped findings → evidence. "
            "Structure: ONE-line answer → SO_WHAT box → CONFIDENCE_BREAKDOWN → 3-5 MECE FINDING tags → "
            "BY-SEGMENT cut → BECAUSE causal chain → KILL-criteria → ANCHOR comparisons → ASSUMPTIONS → "
            "RECOMMENDATIONS with owner/effort/timeline → NEXT STEPS → RELATED pills. "
            "Every number has context (vs last period, vs average, vs total). Numbers > adjectives. "
            "Include [MODE:deep] at the start. "
            "REQUIRED TAGS (frontend renders visually — output structured, not prose duplicates): "
            "- [HEADLINE:short title 4-10 words] — MUST appear right after [MODE:deep]. "
            "- [CONFIDENCE:HIGH|VERY HIGH] at start based on cross-validation. "
            "- [CONFIDENCE_BREAKDOWN:dq|qm|rp] three integers 0-100. "
            "- [SO_WHAT:action|owner|effort|risk] — decision-ready top recommendation. "
            "  Example: [SO_WHAT:Recover ₹1.6Cr/mo by fixing 3 SKU stockouts|Supply Ops + Distribution|2wk|Low] "
            "- 3-5 [FINDING:rank|text|impact|severity] tags. severity ∈ {HIGH,MED,LOW}. MECE — must add to whole. "
            "- 3-6 [SEGMENT:label|value|pct] tags showing cohort/zone/category breakdown of the top finding. "
            "  pct = share of total 0-100. Example: [SEGMENT:Zone-3|₹84L|52.0] "
            "- 2-3 [ANCHOR:comparison] tags grounding numbers. "
            "  Example: [ANCHOR:= 12% of zone revenue] "
            "- [BECAUSE:cause1|cause2|cause3] causal chain (2-3 causes max). "
            "- [KILL:cond1|cond2|cond3] kill-criteria — what would invalidate this finding. "
            "  Example: [KILL:Q2 monsoon disrupts Zone-3|Competitor drops price >12%|Supplier defaults] "
            "- 2-3 [ASSUMPTION:text] tags surfacing limits. "
            "- 3-5 [KPI:value|label|change] for headline metrics. "
            "- In tables, TREND column: ▲ +5% / ▼ -2% / ━ 0%. "
            "- 3 [RELATED:question] for deeper drill. "
            "RECOMMENDATIONS section (markdown): each item = action + expected $ impact + cost (low/med/high) + timeline. "
            "Do NOT invent numbers — every figure must trace to SQL result. Hallucinated numbers caught + flagged."
        )

    return " ".join(parts) + f"\n\nQuestion: {message}"


def _smart_route(message: str, projects: list[dict], session_id: str | None = None) -> dict | None:
    """Pick the best project for a question using keyword matching + LLM fallback.

    Routing signals (in priority order):
    1. Explicit agent/project name mention → score 10/8
    2. Table name match → score 5
    3. Column name match → score 3 (e.g., "revenue" matches total_revenue column)
    4. Persona/domain keyword match → score 2 (e.g., "factory" for manufacturing project)
    5. Role keyword match → score 2
    6. Session continuity → score 4 (if last message went to same project)
    7. LLM fallback → picks from catalog with table+column context
    """
    msg_lower = message.lower()
    # Tokenize message for word-level matching
    msg_words = set(msg_lower.replace(",", " ").replace("?", " ").replace(".", " ").split())

    # Check if it's a general question (no project needed)
    general_patterns = ['who are you', 'what can you do', 'hello', 'hi ', 'hey', 'help',
                        'what are you', 'introduce', 'thanks', 'thank you', 'bye']
    if any(msg_lower.startswith(p) or msg_lower.strip() == p.strip() for p in general_patterns):
        return None

    # Step 0: Session continuity — check what project the last message was routed to
    last_routed_slug = None
    if session_id:
        try:
            from sqlalchemy import text as sa_text
            from db import get_sql_engine
            _eng = get_sql_engine()
            with _eng.connect() as conn:
                row = conn.execute(sa_text(
                    "SELECT project_slug FROM public.dash_chat_sessions "
                    "WHERE session_id = :sid ORDER BY updated_at DESC LIMIT 1"
                ), {"sid": session_id}).fetchone()
                if row and row[0]:
                    last_routed_slug = row[0]
        except Exception:
            pass

    # Step 1: Multi-signal keyword scoring
    scores = []
    for p in projects:
        score = 0
        reasons = []
        # Match agent name
        agent_clean = p["agent_name"].lower().replace(" agent", "").strip()
        if agent_clean and agent_clean in msg_lower:
            score += 10
            reasons.append(f"agent name '{agent_clean}'")
        # Match project name
        proj_clean = p["name"].lower().replace(" demo", "").strip()
        if proj_clean and len(proj_clean) > 2 and proj_clean in msg_lower:
            score += 8
            reasons.append(f"project name '{proj_clean}'")
        # Match table names
        for t in p.get("tables", []):
            tl = t.lower()
            if tl in msg_lower:
                score += 5
                reasons.append(f"table '{t}'")
            elif len(tl) > 3 and tl.rstrip('s') in msg_lower:
                score += 3
                reasons.append(f"partial table '{t}'")
        # Match column names (new: e.g., "revenue" matches total_revenue)
        for col in p.get("columns", []):
            col_lower = col.lower()
            # Exact word match (avoid substring false positives)
            col_words = col_lower.replace("_", " ").split()
            matched_col_words = [w for w in col_words if len(w) > 3 and w in msg_words]
            if matched_col_words:
                score += 3
                reasons.append(f"column '{col}'")
                break  # Don't over-count columns
        # Match persona/domain keywords
        for kw in p.get("persona_keywords", []):
            if kw in msg_words:
                score += 2
                reasons.append(f"domain '{kw}'")
                if score > 20:
                    break  # Cap persona contribution
        # Match role keywords
        if p.get("agent_role"):
            role_words = [w for w in p["agent_role"].lower().split() if len(w) > 3]
            for w in role_words:
                if w in msg_words:
                    score += 2
                    reasons.append(f"role '{w}'")
                    break
        # Session continuity bonus
        if last_routed_slug and p["slug"] == last_routed_slug:
            score += 4
            reasons.append("session continuity")
        scores.append((p, score, reasons))

    scores.sort(key=lambda x: x[1], reverse=True)

    # If clear winner with score >= 3, AND not a tie (>2 point gap), use it
    if scores and scores[0][1] >= 3:
        is_tie = len(scores) > 1 and (scores[0][1] - scores[1][1]) <= 2 and scores[1][1] >= 3
        if not is_tie:
            winner = scores[0][0]
            top_reasons = scores[0][2][:3]
            winner["reason"] = f"matched: {', '.join(top_reasons)} (score: {scores[0][1]})"
            return winner
        # Tie detected — fall through to LLM for disambiguation
        # Only send tied candidates to LLM for faster response
        tie_threshold = scores[0][1] - 2
        projects = [p for p, s, _ in scores if s >= tie_threshold]

    # Step 2: LLM routing for ambiguous questions (also handles ties)
    try:
        import json as _json
        from os import getenv
        import httpx

        api_key = getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return scores[0][0] if scores else None

        catalog = []
        for p in projects:
            tables_str = ", ".join(p["tables"][:10]) if p["tables"] else "no tables"
            cols_str = ", ".join(set(p.get("columns", [])[:15])) if p.get("columns") else ""
            domain_str = ", ".join(p.get("persona_keywords", [])[:8])
            line = f"- slug: {p['slug']} | agent: {p['agent_name']} | role: {p.get('agent_role', '')} | tables: {tables_str}"
            if cols_str:
                line += f" | columns: {cols_str}"
            if domain_str:
                line += f" | domain: {domain_str}"
            catalog.append(line)

        prompt = f"""Pick the BEST project to answer this question. If this is a greeting or general question, respond with "none".

PROJECTS:
{chr(10).join(catalog)}

QUESTION: {message}

Respond with ONLY valid JSON: {{"slug": "the_slug_or_none", "reason": "brief reason"}}"""

        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": LITE_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 80, "temperature": 0},
            timeout=20,
        )
        result = resp.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = _json.loads(content.strip().strip("`").strip())
        slug = parsed.get("slug", "none")
        reason = parsed.get("reason", "LLM selected")

        if slug == "none" or not slug:
            return None

        matched = [p for p in projects if p["slug"] == slug]
        if matched:
            matched[0]["reason"] = f"LLM: {reason}"
            return matched[0]
    except Exception:
        pass

    # Fallback: return first project for data questions, None for general
    if any(w in msg_lower for w in ['data', 'table', 'query', 'show', 'how many', 'total', 'count', 'list', 'top', 'revenue', 'amount']):
        if scores:
            scores[0][0]["reason"] = "fallback (data keyword detected)"
            return scores[0][0]

    return None


def _route_message(message: str, projects: list[dict], session_id: str | None = None) -> dict | None:
    """2-tier routing: keyword pre-filter → Router Agent for ambiguous cases.

    Tier 1: Fast keyword scoring (same as _smart_route, < 10ms, $0)
    Tier 2: Router Agent with tools (LITE_MODEL, < 1.5s, ~$0.001)
    """
    msg_lower = message.lower()

    # General question check (instant)
    general_patterns = ['who are you', 'what can you do', 'hello', 'hi ', 'hey', 'help',
                        'what are you', 'introduce', 'thanks', 'thank you', 'bye']
    if any(msg_lower.startswith(p) or msg_lower.strip() == p.strip() for p in general_patterns):
        return None

    # Tier 1: Keyword pre-filter (reuse _smart_route logic)
    # Run keyword scoring from _smart_route
    result = _smart_route(message, projects, session_id=session_id)

    # Check if it was a clear win (score gap >= 5) or if _smart_route already used LLM
    # If _smart_route returned a result, check if the reason indicates it was a keyword match with high score
    if result:
        reason = result.get("reason", "")
        # If keyword match with high score (>= 8), trust it
        import re
        score_match = re.search(r'score:\s*(\d+)', reason)
        if score_match and int(score_match.group(1)) >= 8:
            return result
        # If it was already LLM-routed, trust it
        if "LLM:" in reason:
            return result

    # Tier 2: Router Agent for ambiguous/low-confidence cases
    try:
        from dash.agents.router import create_router_agent
        import json as _json

        router = create_router_agent(projects, session_id=session_id)

        # Run synchronously with timeout
        import asyncio
        import concurrent.futures

        response = router.run(message)
        content = response.content if hasattr(response, 'content') else str(response)

        # Parse JSON from response
        # Strip markdown fences if present
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if content.startswith("json"):
            content = content[4:].strip()

        parsed = _json.loads(content)
        slugs = parsed.get("slugs", [])
        reason = parsed.get("reason", "Router Agent")
        confidence = parsed.get("confidence", "medium")

        if not slugs:
            return None  # General question

        # Find matching project
        primary_slug = slugs[0]
        matched = [p for p in projects if p["slug"] == primary_slug]
        if matched:
            result = matched[0].copy()
            result["reason"] = f"Router: {reason} (confidence: {confidence})"
            if len(slugs) > 1:
                result["multi_slugs"] = slugs
            return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Router Agent failed: {e}")

    # Fallback: return whatever _smart_route found (even if low confidence)
    return result


# ---------------------------------------------------------------------------
# Super Chat — smart routing with backend mode detection
# ---------------------------------------------------------------------------

# ── SSE reasoning-trace normalization (mirrors app/projects.py) ────────────
# Normalizes raw Agno stream events into a stable trace contract: tool events
# get {id, name, args, result}; ReasoningTools think/analyze/reason calls and
# reasoning content become ReasoningStep events. Fail-soft throughout.
_REASONING_TOOL_NAMES = {"think", "analyze", "reason", "reasoning"}


def _trace_short_str(val, limit: int = 300) -> str:
    try:
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            import json as _j
            s = _j.dumps(val, default=str)
        else:
            s = str(val)
        return s if len(s) <= limit else s[:limit] + "…"
    except Exception:
        return ""


def _trace_normalize_tool(data: dict) -> dict:
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    try:
        name = tool.get("tool_name") or tool.get("name") or ""
        args = tool.get("tool_args")
        if args is None:
            args = tool.get("args")
        if args is None:
            args = {}
        tool_id = tool.get("tool_call_id") or tool.get("id") or f"{name}:{id(tool)}"
        merged = dict(tool)
        merged["id"] = tool_id
        merged["name"] = name
        merged["args"] = args
        if "result" in tool or "content" in tool:
            merged["result"] = _trace_short_str(tool.get("result", tool.get("content")))
        return merged
    except Exception:
        return tool or {}


_TRACE_SQL_TOOL_NAMES = {"run_sql_query", "run_sql", "execute_sql", "query"}
_TRACE_SQL_ARG_KEYS = ("query", "sql", "statement", "sql_query", "q")


def _trace_attach_sql_cost(tool: dict, engine) -> None:
    """Best-effort: attach `cost` (rounded total_cost int) + `est_rows` to a
    normalized SQL tool object. Call only on ToolCallStarted. Fail-soft —
    never raises, never blocks the stream."""
    try:
        if not isinstance(tool, dict):
            return
        name = (tool.get("name") or tool.get("tool_name") or "").lower()
        if name not in _TRACE_SQL_TOOL_NAMES:
            return
        args = tool.get("args")
        if not isinstance(args, dict):
            args = tool.get("tool_args") if isinstance(tool.get("tool_args"), dict) else {}
        sql = None
        for k in _TRACE_SQL_ARG_KEYS:
            v = args.get(k) if isinstance(args, dict) else None
            if v and isinstance(v, str):
                sql = v
                break
        if not sql:
            return
        if engine is None:
            from dash.tools.skill_refinery import _get_engine
            engine = _get_engine()
        from dash.tools.sql_cost_guard import estimate_query_cost
        est = estimate_query_cost(engine, sql)
        if not isinstance(est, dict) or not est.get("ok"):
            return
        tc = est.get("total_cost")
        if tc is None:
            return
        tool["cost"] = int(round(float(tc)))
        tool["est_rows"] = int(est.get("est_rows") or 0)
    except Exception:
        pass


def _trace_reasoning_from_tool(data: dict, agent_name: str):
    try:
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        name = (tool.get("tool_name") or tool.get("name") or "").lower()
        if name not in _REASONING_TOOL_NAMES:
            return None
        args = tool.get("tool_args") or tool.get("args") or {}
        if isinstance(args, dict):
            title = args.get("title") or args.get("topic") or name
            content = (
                args.get("thought")
                or args.get("reasoning")
                or args.get("content")
                or args.get("analysis")
                or ""
            )
        else:
            title, content = name, _trace_short_str(args, 1000)
        return {"title": str(title)[:120], "content": str(content), "agent_name": agent_name or ""}
    except Exception:
        return None


def _trace_reasoning_from_content(data: dict, agent_name: str):
    try:
        title = data.get("title") or data.get("reasoning_title") or "Reasoning"
        content = (
            data.get("reasoning_content")
            or data.get("content")
            or data.get("reasoning")
            or ""
        )
        if not content:
            return None
        return {"title": str(title)[:120], "content": str(content), "agent_name": agent_name or ""}
    except Exception:
        return None


def _trace_usage_from_event(data: dict, agent_name: str = "") -> dict | None:
    """Best-effort token usage + model from an Agno event dict (post to_dict()).
    Returns {input_tokens, output_tokens, model, agent_name} when usage metrics
    are present, else None. Fail-soft. Agno exposes usage on a `metrics` dict
    (RunMetrics.to_dict) with input_tokens/output_tokens/total_tokens on
    completed events; model id on `model` (fallback `model_id`)."""
    try:
        m = data.get("metrics")
        if not isinstance(m, dict):
            m = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if not isinstance(m, dict):
            return None
        in_tok = m.get("input_tokens")
        out_tok = m.get("output_tokens")
        tot_tok = m.get("total_tokens")
        if not any(isinstance(x, (int, float)) and x for x in (in_tok, out_tok, tot_tok)):
            return None
        model = (
            data.get("model")
            or data.get("model_id")
            or (data.get("model_provider") and str(data.get("model_provider")))
            or ""
        )
        payload = {
            "input_tokens": int(in_tok or 0),
            "output_tokens": int(out_tok or 0),
            "model": str(model or ""),
            "agent_name": agent_name or data.get("agent_name") or data.get("member_id") or "",
        }
        if isinstance(tot_tok, (int, float)) and tot_tok:
            payload["total_tokens"] = int(tot_tok)
        return payload
    except Exception:
        return None


def _trace_attach_model_to_tool(tool: dict, data: dict) -> None:
    """Attach `model` (+ `duration` if present) from the surrounding event onto
    the normalized tool object. Fail-soft — never overwrites with empties."""
    try:
        if not isinstance(tool, dict):
            return
        model = data.get("model") or data.get("model_id") or ""
        if model and not tool.get("model"):
            tool["model"] = str(model)
        dur = (
            data.get("duration")
            or (data.get("tool") or {}).get("duration") if isinstance(data.get("tool"), dict) else None
        )
        if isinstance(dur, (int, float)) and dur and not tool.get("duration"):
            tool["duration"] = dur
    except Exception:
        pass


@app.post("/api/super-chat")
async def super_chat(request: Request):
    """Chat that auto-routes to the best project agent using Agno TeamMode.route."""
    from fastapi.responses import StreamingResponse
    import json as _json

    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    form = await request.form()
    message = form.get("message", "")
    stream = str(form.get("stream", "true")).lower() == "true"
    session_id = form.get("session_id")
    mode = form.get("mode", "auto")           # "auto" or project slug
    reasoning = form.get("reasoning", "auto")  # "auto" | "fast" | "deep"
    analysis_type = form.get("analysis_type", "auto")  # "auto" | "descriptive" | "diagnostic" | etc.

    if not message:
        from fastapi import HTTPException
        raise HTTPException(400, "Message required")

    if len(message) > 50000:
        from fastapi import HTTPException
        raise HTTPException(413, "Message too long (max 50000 chars)")

    # Apply reasoning mode — build as SYSTEM instruction, not user message.
    # Chitchat/capability/greeting → plain pharmacist prose, NO dashboard tags/cards/charts.
    _chit = _is_chitchat(message)
    if _chit:
        reasoning_instructions = _chitchat_instructions()
    else:
        reasoning_instructions = _apply_reasoning_mode("", reasoning, analysis_type)

    # Smart routing hint (skip for chitchat — no data/context routing needed)
    routing_hint = "data" if _chit else _detect_routing_hint(message)
    if routing_hint == "both":
        reasoning_instructions = (
            "[ROUTING: This question needs BOTH data AND context. "
            "Ask Analyst for numbers/SQL AND Researcher for document context. "
            "Merge both answers into a comprehensive response.]\n\n" + reasoning_instructions
        )
    elif routing_hint == "context":
        reasoning_instructions = (
            "[ROUTING: This question is about context/documents. "
            "Ask Researcher first. Only involve Analyst if specific numbers are needed.]\n\n" + reasoning_instructions
        )

    # User message stays CLEAN (no system prompt prepended)
    context_msg = message

    # Load user's projects for routing
    from dash.team import _load_user_projects, create_project_team
    all_projects = _load_user_projects(user.get("user_id"))

    if mode != "auto":
        # Pinned to specific project
        from sqlalchemy import text as sa_text
        from db import get_sql_engine
        _eng = get_sql_engine()
        with _eng.connect() as conn:
            row = conn.execute(sa_text(
                "SELECT agent_name, agent_role, agent_personality FROM public.dash_projects WHERE slug = :s"
            ), {"s": mode}).fetchone()
        if not row:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": f"Project '{mode}' not found"}, status_code=404)

        team = create_project_team(
            project_slug=mode, agent_name=row[0], agent_role=row[1],
            agent_personality=row[2], user_id=user.get("user_id"),
        )
        routing_info = {"routed_to": row[0], "slug": mode, "reason": "pinned by user"}
    elif not all_projects:
        # No projects — use general team
        from dash.team import create_team
        team = create_team(user_id=str(user.get("user_id", "")))
        project_info = "You have no projects yet. Go to /ui/projects to create one and upload data."
        reasoning_instructions += f"\n[CONTEXT: {project_info}]"
        routing_info = {"routed_to": "Dash Agent", "slug": None, "reason": "no projects"}
    elif len(all_projects) == 1:
        # Only one project — route directly
        p = all_projects[0]
        team = create_project_team(
            project_slug=p["slug"], agent_name=p["agent_name"], agent_role=p["agent_role"],
            agent_personality=p["agent_personality"], user_id=user.get("user_id"),
        )
        routing_info = {"routed_to": p["agent_name"], "slug": p["slug"], "reason": "only project"}
    else:
        # Multiple projects — smart routing
        target = _route_message(message, all_projects, session_id=session_id)
        if target:
            team = create_project_team(
                project_slug=target["slug"], agent_name=target["agent_name"], agent_role=target["agent_role"],
                agent_personality=target["agent_personality"], user_id=user.get("user_id"),
            )
            routing_info = {"routed_to": target["agent_name"], "slug": target["slug"], "reason": target.get("reason", "auto-matched")}
        else:
            # General question — use general team with project context
            from dash.team import create_team
            team = create_team(user_id=str(user.get("user_id", "")))
            agents_list = ", ".join(f"{p['agent_name']} ({', '.join(p['tables'][:5])})" for p in all_projects)
            reasoning_instructions += f"\n[CONTEXT: User has these data agents: {agents_list}. Help them use the right agent.]"
            routing_info = {"routed_to": "Dash Agent", "slug": None, "reason": "general question"}

    routed_slug = routing_info.get("slug")  # Which project was routed to (None for general)

    # Update session with routed project slug (for session continuity)
    if routed_slug and session_id:
        try:
            from sqlalchemy import text as sa_text
            from db import get_sql_engine
            _eng = get_sql_engine()
            with _eng.connect() as conn:
                conn.execute(sa_text(
                    "UPDATE public.dash_chat_sessions SET project_slug = :slug, updated_at = NOW() "
                    "WHERE session_id = :sid"
                ), {"slug": routed_slug, "sid": session_id})
                conn.commit()
        except Exception:
            pass

    # Inject reasoning/analysis instructions into team (NOT into user message)
    if reasoning_instructions.strip():
        existing = team.instructions or ""
        if isinstance(existing, list):
            existing = "\n".join(existing)
        team.instructions = existing + "\n\n" + reasoning_instructions

    def _run_super_bg(question: str, answer: str):
        """Run self-learning background tasks for the routed project."""
        if not routed_slug:
            return  # No project to learn against
        def _bg():
            try:
                from dash.tools.suggest_rules import suggest_rules_from_conversation
                suggest_rules_from_conversation(routed_slug, session_id or "", question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task suggest_rules failed for {routed_slug}: {e}")
            try:
                from dash.tools.judge import judge_response
                judge_response(routed_slug, session_id or "", question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task judge_response failed for {routed_slug}: {e}")
            try:
                from dash.tools.proactive_insights import generate_proactive_insights
                generate_proactive_insights(routed_slug, question, answer, user.get("user_id"))
            except Exception as e:
                import logging
                logging.error(f"Background task proactive_insights failed for {routed_slug}: {e}")
            try:
                from dash.tools.query_plan_extractor import extract_query_plan
                extract_query_plan(routed_slug, question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task query_plan_extractor failed for {routed_slug}: {e}")
            try:
                from dash.tools.meta_learning import extract_meta_learnings
                extract_meta_learnings(routed_slug, question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task meta_learning failed for {routed_slug}: {e}")
            # Continuous KG learning — extract triples from every chat
            try:
                from dash.tools.knowledge_graph import extract_chat_triples
                extract_chat_triples(routed_slug, question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task chat_triples failed for {routed_slug}: {e}")
            # Auto-memory promotion — save facts without approval
            try:
                from dash.tools.knowledge_graph import auto_promote_facts
                auto_promote_facts(routed_slug, question, answer)
            except Exception as e:
                import logging
                logging.error(f"Background task auto_promote failed for {routed_slug}: {e}")
            # Rich user preference tracking
            try:
                from dash.tools.knowledge_graph import track_user_preferences
                track_user_preferences(routed_slug, user.get("user_id"), question, answer)
            except Exception:
                pass
            # Episodic memory — save user reactions as events
            try:
                from dash.tools.knowledge_graph import extract_episodic_memory
                extract_episodic_memory(routed_slug, question, answer)
            except Exception:
                pass
        _bg_executor.submit(_bg)

    # Surface resolved reasoning tier into SSE meta (exec-view consumer).
    try:
        _complexity = (routing_info or {}).get("complexity_tier") or (routing_info or {}).get("complexity")
        routing_info["tier"] = _tier_label(_complexity, reasoning)
    except Exception:
        try:
            routing_info["tier"] = "standard"
        except Exception:
            pass

    if stream:
        def event_generator():
            import time
            yield f"event: Routing\ndata: {_json.dumps(routing_info, default=str)}\n\n"
            # Send original message so frontend shows clean question (not system prompt)
            yield f"event: OriginalMessage\ndata: {_json.dumps({'message': message}, default=str)}\n\n"
            full_content = []
            _stream_start = time.time()
            # Best-effort EXPLAIN engine for SQL tool cost. Use the routed
            # project's read engine when known; else None (helper falls back to
            # a generic engine). Fail-soft.
            _cost_engine = None
            try:
                if routed_slug:
                    from db import get_project_readonly_engine as _get_proj_ro
                    _cost_engine = _get_proj_ro(routed_slug)
            except Exception:
                _cost_engine = None
            _seen_event_names: set[str] = set()
            try:
                response_iter = team.run(context_msg, stream=True, stream_events=True, session_id=session_id)
                for event in response_iter:
                    if time.time() - _stream_start > 300:  # 5 minute max
                        timeout_msg = _json.dumps({"content": "\n\nResponse timed out after 5 minutes."})
                        yield f"event: TeamRunContent\ndata: {timeout_msg}\n\n"
                        break
                    if hasattr(event, 'to_dict'):
                        data = event.to_dict()
                    elif hasattr(event, 'model_dump'):
                        data = event.model_dump()
                    elif hasattr(event, 'content'):
                        data = {"content": event.content, "event": "TeamRunContent"}
                    else:
                        data = {"content": str(event)}
                    event_name = data.get("event", "TeamRunContent")

                    if event_name not in _seen_event_names:
                        _seen_event_names.add(event_name)
                        try:
                            import logging
                            tool_name = (data.get("tool") or {}).get("tool_name") if isinstance(data.get("tool"), dict) else None
                            agent_name = data.get("agent_name") or data.get("member_id") or data.get("member_name")
                            logging.info(
                                f"[super-chat-stream] first event: name={event_name} "
                                f"agent={agent_name} tool={tool_name} keys={list(data.keys())[:8]}"
                            )
                        except Exception:
                            pass

                    if event_name in (
                        "ToolCallStarted", "ToolCallCompleted",
                        "TeamToolCallStarted", "TeamToolCallCompleted",
                    ):
                        if not data.get("agent_name"):
                            tool_dict = data.get("tool") if isinstance(data.get("tool"), dict) else {}
                            owner = (
                                data.get("member_name")
                                or data.get("member_id")
                                or data.get("agent_id")
                                or tool_dict.get("agent_name")
                            )
                            if owner:
                                data["agent_name"] = owner

                    # ── Reasoning-trace normalization (OpenAI-style trace) ──
                    _agent_name = data.get("agent_name") or ""
                    try:
                        if event_name in (
                            "ToolCallStarted", "ToolCallCompleted",
                            "TeamToolCallStarted", "TeamToolCallCompleted",
                        ):
                            rstep = _trace_reasoning_from_tool(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                            data["tool"] = _trace_normalize_tool(data)
                            _trace_attach_model_to_tool(data["tool"], data)
                            if event_name in ("ToolCallStarted", "TeamToolCallStarted"):
                                _trace_attach_sql_cost(data["tool"], _cost_engine)
                        elif event_name in (
                            "ReasoningStep", "ReasoningStepStarted", "ReasoningStepCompleted",
                            "ReasoningCompleted", "ReasoningContent",
                        ):
                            rstep = _trace_reasoning_from_content(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                                continue
                        elif event_name in ("TeamRunContent", "RunContent") and data.get("reasoning_content"):
                            rstep = _trace_reasoning_from_content(data, _agent_name)
                            if rstep is not None:
                                yield f"event: ReasoningStep\ndata: {_json.dumps(rstep, default=str)}\n\n"
                    except Exception:
                        pass

                    if event_name in ("TeamRunContent", "RunContent") and data.get("content"):
                        full_content.append(data["content"])

                    # ── Usage event — token counts + model when Agno exposes them.
                    try:
                        _usage = _trace_usage_from_event(data, _agent_name)
                        if _usage is not None:
                            yield f"event: Usage\ndata: {_json.dumps(_usage, default=str)}\n\n"
                    except Exception:
                        pass

                    yield f"event: {event_name}\ndata: {_json.dumps(data, default=str)}\n\n"
            except Exception as e:
                import logging
                logging.exception("Chat error")
                yield f"event: TeamRunContent\ndata: {_json.dumps({'content': 'An error occurred while processing your request'})}\n\n"

            # Run background learning tasks
            answer = "".join(full_content)
            if answer:
                _run_super_bg(message, answer)

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        try:
            response = team.run(context_msg, session_id=session_id)
            return {"content": response.content or "", "session_id": session_id, "routing": routing_info}
        except Exception as e:
            import logging
            logging.exception("Chat error")
            return {"content": "An error occurred while processing your request", "session_id": session_id}


@app.get("/api/user-projects-brief")
async def user_projects_brief(request: Request):
    """Get brief list of user's projects for the super chat mode selector."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    _eng = _shared_engine

    with _eng.connect() as conn:
        own = conn.execute(sa_text(
            "SELECT slug, name, agent_name FROM public.dash_projects WHERE user_id = :uid ORDER BY updated_at DESC"
        ), {"uid": user["user_id"]}).fetchall()

        shared = conn.execute(sa_text("""
            SELECT p.slug, p.name, p.agent_name
            FROM public.dash_projects p
            JOIN public.dash_project_shares s ON s.project_id = p.id
            WHERE s.shared_with_user_id = :uid
        """), {"uid": user["user_id"]}).fetchall()

    projects = [{"slug": r[0], "name": r[1], "agent_name": r[2], "owned": True} for r in own]
    projects += [{"slug": r[0], "name": r[1], "agent_name": r[2], "owned": False} for r in shared]

    return {"projects": projects}


@app.get("/api/all-dashboards")
async def list_all_dashboards(request: Request):
    """List all dashboards across all projects for the current user."""
    user = getattr(getattr(request, 'state', None), 'user', None)
    if not user:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    import json as _json
    _eng = _shared_engine

    with _eng.connect() as conn:
        rows = conn.execute(sa_text(
            "SELECT d.id, d.name, d.project_slug, d.widgets, d.updated_at, d.created_at, d.user_id, "
            "p.name as project_name, u.username as creator_name "
            "FROM public.dash_dashboards d "
            "LEFT JOIN public.dash_projects p ON d.project_slug = p.slug "
            "LEFT JOIN public.dash_users u ON d.user_id = u.id "
            "WHERE d.user_id = :uid ORDER BY d.updated_at DESC"
        ), {"uid": user["user_id"]}).fetchall()

    dashboards = []
    for r in rows:
        widgets = r[3] if isinstance(r[3], list) else _json.loads(r[3]) if r[3] else []
        dashboards.append({
            "id": r[0], "name": r[1], "project_slug": r[2], "widget_count": len(widgets),
            "updated_at": str(r[4]) if r[4] else None, "created_at": str(r[5]) if r[5] else None,
            "creator": r[8] or "unknown", "is_owner": True,
            "project_name": r[7] or r[2],
        })
    return {"dashboards": dashboards}




@app.get("/api/health")
def api_health_check():
    """Lightweight health endpoint for probes/load balancers. Mirrors /health."""
    return health_check()


@app.get("/health")
def health_check():
    try:
        with _shared_engine.connect() as conn:
            conn.execute(sa_text("SELECT 1"))
        last_backup = None
        # Prefer the new dash_system_status row (written by dash-backup daemon).
        # Fall back to legacy dash_backup_runs table for back-compat.
        try:
            with _shared_engine.connect() as conn:
                r = conn.execute(sa_text(
                    "SELECT last_backup_at FROM public.dash_system_status WHERE id=1"
                )).fetchone()
                last_backup = r[0].isoformat() if r and r[0] else None
        except Exception:
            last_backup = None
        if last_backup is None:
            try:
                with _shared_engine.connect() as conn:
                    r = conn.execute(sa_text(
                        "SELECT MAX(ts) FROM public.dash_backup_runs WHERE success=TRUE"
                    )).fetchone()
                    last_backup = r[0].isoformat() if r and r[0] else None
            except Exception:
                last_backup = None
        # Layer 3 of stale-image defense — surface container age so monitors
        # can alert when a deploy didn't actually replace the running image.
        _age = globals().get("_BUILD_AGE_HOURS_AT_START")
        _stale = bool(_age is not None and _age > 24)
        return {
            "status": "ok", "db": "connected",
            "last_backup_at": last_backup,
            "staleness_warning": _stale,
            "image_age_hours": _age,
        }
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "unhealthy", "db": str(e)}, status_code=503)


@app.get("/api/admin/image/info")
def admin_image_info(request: Request):
    """Super-admin: report container image metadata so admins can detect stale deploys.

    Returns {built_at, git_commit, version, image_age_hours, stale_warning}.
    BUILD_COMMIT/BUILD_TIME are injected at Docker build via ARG/ENV. Falls back
    to container start time (/proc/1 ctime) when BUILD_TIME is absent.
    """
    from app.branding import _require_super_admin
    _require_super_admin(request)

    import os as _os
    build_commit = _os.getenv("BUILD_COMMIT", "unknown")
    build_time = _os.getenv("BUILD_TIME", "").strip()
    version = _os.getenv("APP_VERSION", "dev")

    built_at_iso = None
    image_age_hours = None
    try:
        from datetime import datetime as _dt, timezone as _tz
        if build_time:
            bt = build_time.replace("Z", "+00:00")
            dt_built = _dt.fromisoformat(bt)
            if dt_built.tzinfo is None:
                dt_built = dt_built.replace(tzinfo=_tz.utc)
            built_at_iso = dt_built.isoformat()
            image_age_hours = round((_dt.now(_tz.utc) - dt_built).total_seconds() / 3600.0, 2)
        else:
            try:
                st = _os.stat("/proc/1")
                dt_started = _dt.fromtimestamp(st.st_ctime, tz=_tz.utc)
                built_at_iso = dt_started.isoformat()
                image_age_hours = round((_dt.now(_tz.utc) - dt_started).total_seconds() / 3600.0, 2)
            except Exception:
                pass
    except Exception:
        pass

    return {
        "built_at": built_at_iso,
        "git_commit": build_commit,
        "version": version,
        "image_age_hours": image_age_hours,
        "stale_warning": (image_age_hours is not None and image_age_hours > 24),
    }


@app.get("/api/health/embeddings")
def api_health_embeddings():
    """Return current embedding-stack health: last successful model, last failure timestamp."""
    try:
        from db.session import get_embedding_health, get_active_embedding_model
        health = get_embedding_health()
        health["active_model"] = get_active_embedding_model()
        health["degraded"] = (
            health["active_model"] is None
            or (health["consecutive_failures"] or 0) > 0
        )
        return health
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/architecture")
def get_architecture():
    """Return full system architecture info: models from env, counts from DB."""
    from os import getenv

    # AI Models from environment
    models = {
        "chat":      getenv("CHAT_MODEL", "google/gemini-3-flash-preview"),
        "deep":      getenv("DEEP_MODEL", "openai/gpt-5.4-mini"),
        "lite":      getenv("LITE_MODEL", "google/gemini-3.1-flash-lite-preview"),
        "embedding": getenv("EMBEDDING_MODEL", "google/gemini-embedding-2-preview"),
        "provider":  "OpenRouter",
    }

    # Live metrics from DB
    metrics = {}
    try:
        with _shared_engine.connect() as conn:
            metrics["projects"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_projects")).scalar() or 0
            metrics["users"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_users")).scalar() or 0
            metrics["chats"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_chat_sessions")).scalar() or 0
            try:
                metrics["brain_entries"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_company_brain")).scalar() or 0
            except Exception:
                metrics["brain_entries"] = 0
            try:
                metrics["memories"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_memories")).scalar() or 0
            except Exception:
                metrics["memories"] = 0
            try:
                metrics["feedback"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_feedback")).scalar() or 0
            except Exception:
                metrics["feedback"] = 0
            try:
                metrics["kg_triples"] = conn.execute(sa_text("SELECT COUNT(*) FROM public.dash_knowledge_triples")).scalar() or 0
            except Exception:
                metrics["kg_triples"] = 0
            try:
                metrics["quality_avg"] = round(float(conn.execute(sa_text("SELECT AVG(score) FROM public.dash_quality_scores")).scalar() or 0), 1)
            except Exception:
                metrics["quality_avg"] = 0
    except Exception:
        pass

    # Infrastructure
    infra = {
        "containers": ["dash-api (8GB)", "dash-db (4GB)", "dash-pgbouncer (512MB)", "dash-ml (1GB)", "dash-caddy (512MB)"],
        "db": "PostgreSQL 18 + pgvector",
        "pooler": "PgBouncer (transaction mode, NullPool)",
        "proxy": "Caddy (auto-SSL/TLS)",
        "workers": int(getenv("WORKERS", "8")),
        "rate_limit": getenv("RATE_LIMIT", "500/minute"),
    }

    # Security
    security = {
        "network": ["Auto-SSL/TLS", "HSTS", "X-Frame-Options", "XSS protection", "Rate limit: " + infra["rate_limit"], "Body max: 250MB", "Timeout: 300s"],
        "auth": ["SCRAM-SHA-256", "Token-based + TTL", "Role-based ACL (viewer/editor/admin)", "Keycloak SSO optional"],
        "database": ["Schema isolation per project", "Read-only Analyst", "Parameterized SQL", "Statement timeout: 120s", "Idle timeout: 60s"],
        "application": ["LLM SQL sandbox (block DROP/ALTER)", "Batch predict cap: 10K rows", "ML worker timeout: 5 min", "ML worker row limit: 100K", "Engine disposal (no leaks)", "Prompt injection sanitization", "Atomic JSON writes", "50K char message limit"],
        "monitoring": ["/health + ML retrain status", "Audit log", "Brain access log", "Training run tracking", "Quality scoring (1-5)"],
    }

    # Agents
    agents = {
        "total": 30,
        "chat_team": ["Leader (coordinator)", "Analyst (31 tools, SQL)", "Engineer (views, dashboards)", "Researcher (document RAG)", "Data Scientist (6 ML tools)"],
        "specialists": ["Comparator", "Diagnostician", "Narrator", "Validator", "Planner", "Trend", "Pareto", "Anomaly", "Benchmark", "Prescriptor"],
        "background": ["Judge", "Rule Suggester", "Proactive Insights", "Query Plan Extractor", "Meta Learner", "Auto Evolver", "KG Extractor", "Auto-Memory", "User Prefs", "Episodic Memory", "Follow-ups"],
        "upload": ["Conductor", "Parser", "Scanner", "Vision", "Inspector"],
    }

    # ML
    ml = {
        "tools": [
            {"name": "predict", "algorithm": "AutoARIMA → LLM fallback", "type": "Pre-trained / LLM", "cost": "$0 / $0.02"},
            {"name": "feature_importance", "algorithm": "LightGBM + GridSearchCV + SHAP", "type": "On-demand", "cost": "$0"},
            {"name": "detect_anomalies_ml", "algorithm": "IsolationForest", "type": "Pre-trained", "cost": "$0"},
            {"name": "classify", "algorithm": "GradientBoosting + GridSearchCV", "type": "On-demand", "cost": "$0"},
            {"name": "cluster", "algorithm": "K-Means (auto-K)", "type": "On-demand", "cost": "$0"},
            {"name": "decompose", "algorithm": "Seasonal Decompose", "type": "On-demand", "cost": "$0"},
        ],
        "preprocessing": ["SimpleImputer (median/mode)", "Temporal features (month, quarter, day, weekend)", "Label encoding (< 50 categories)"],
        "evaluation": ["R², RMSE, MAE", "F1, Precision, Recall, Confusion Matrix", "Silhouette, Calinski-Harabasz", "MAPE on holdout", "Cross-validation (2-5 fold)"],
        "worker": {"row_limit": 100000, "timeout_sec": 300, "retrain_interval": "24h"},
    }

    # Knowledge
    knowledge = {
        "layers": 13,
        "search": "Unified: PgVector + Brain + KG + Facts → Cohere reranking",
        "embedding_cascade": ["Gemini Embed 2", "OpenAI large", "OpenAI small", "Cohere v4"],
        "rerank_cascade": ["Cohere rerank-4-pro", "rerank-4-fast", "rerank-v3.5", "keyword fallback"],
    }

    # Data pipeline
    pipeline = {
        "formats": 18,
        "format_list": ["CSV", "Excel", "JSON", "SQL", "PPTX", "DOCX", "PDF", "MD", "TXT", "JPG", "PNG", "TIFF", "BMP", "GIF", "WEBP", "PY", "XLS"],
        "training_steps": 14,
        "connectors": ["PostgreSQL", "MySQL"],
        "export": ["PPTX (8 themes)", "Excel (4 sheets)", "PDF", "Dashboards"],
    }

    return {
        "models": models, "metrics": metrics, "infra": infra, "security": security,
        "agents": agents, "ml": ml, "knowledge": knowledge, "pipeline": pipeline,
    }


@app.post("/knowledge/reload")
def reload_knowledge() -> dict[str, str]:
    """Reload knowledge files into the vector database."""
    from scripts.load_knowledge import load_knowledge

    try:
        load_knowledge()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    agent_os.serve(
        app="app.main:app",
        reload=runtime_env == "dev",
    )
