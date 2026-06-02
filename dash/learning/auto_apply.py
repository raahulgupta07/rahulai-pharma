"""Atomic vertical auto-apply for Dash.

After a classifier picks a vertical (e.g. "pharmacy" with confidence 0.93),
this module applies the full vertical pack in a single orchestrated call:

  1. snapshot               -- pre-apply state for REVERT
  2. apply_agent_template   -- dash.templates.apply + reconcile
  3. apply_vertical         -- brain seeds + workflows from dash/verticals/<v>/
  4. apply_template         -- dash.learning.template_generator (LLM-driven;
                                falls back to dash.feature_config.apply_preset
                                when generator returns _degraded)
  5. apply_visibility_template -- dash/policy/templates/<v>.py
  6. seed_roles             -- replace_roles from policy template suggested_roles
  7. derive_scope_guardrail -- dash.scope_deriver.derive_scope (best-effort)
  8. import_marketplace_skills -- up to 5 tagged skills (best-effort)
  9. activate_workflows     -- flip pending -> active for this project
 10. record_event           -- final history row

Each step is wrapped in try/except. Individual step failures are recorded in
``applied_steps`` but do NOT abort the whole apply (fail-soft). If more than
half of the steps fail, ``applied`` is set to False on the history row.

Companion helper ``revert_auto_apply(slug, history_id, user_id)`` rolls back
using the stored snapshot.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Engine helper — reuse canonical NullPool engine
# ─────────────────────────────────────────────────────────────────────────────
def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ─────────────────────────────────────────────────────────────────────────────
# Step result helpers
# ─────────────────────────────────────────────────────────────────────────────
def _step(name: str, ok: bool, details: str = "", error: str | None = None) -> dict:
    return {"step": name, "ok": bool(ok), "details": details, "error": error}


def _first(results: list[dict] | None) -> dict:
    """Return the first step dict from a phase-task result list, or a fail-soft
    placeholder if the task produced nothing (should not happen — guard only)."""
    if results:
        return results[0]
    return _step("unknown", False, error="step produced no result")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Snapshot
# ─────────────────────────────────────────────────────────────────────────────
def _take_snapshot(slug: str) -> dict:
    """Capture pre-apply state for revert."""
    snap: dict[str, Any] = {
        "captured_at": _now_iso(),
        "feature_config": None,
        "template_bindings": [],
        "autonomous_workflows": [],
        "visibility_policy": None,
        "user_roles": [],
        "brain_count": 0,
        "brain_max_id": 0,
    }
    eng = _engine()
    if eng is None:
        return snap
    try:
        with eng.connect() as conn:
            # feature_config
            try:
                row = conn.execute(text(
                    "SELECT feature_config FROM public.dash_projects WHERE slug=:s"
                ), {"s": slug}).first()
                if row and row[0]:
                    fc = row[0]
                    if isinstance(fc, str):
                        try: fc = json.loads(fc)
                        except Exception: fc = {}
                    snap["feature_config"] = fc
            except Exception as e:
                logger.warning("snapshot feature_config failed: %s", e)

            # template_bindings
            try:
                rows = conn.execute(text(
                    "SELECT project_slug, template_ref, real_ref, status, match_method, confidence "
                    "FROM dash_template_bindings WHERE project_slug=:s"
                ), {"s": slug}).fetchall()
                snap["template_bindings"] = [
                    {"template_ref": r[1], "real_ref": r[2], "status": r[3],
                     "match_method": r[4], "confidence": float(r[5]) if r[5] is not None else None}
                    for r in rows
                ]
            except Exception as e:
                logger.warning("snapshot template_bindings failed: %s", e)

            # autonomous_workflows
            try:
                rows = conn.execute(text(
                    "SELECT id, name, status FROM dash_autonomous_workflows "
                    "WHERE project_slug=:s"
                ), {"s": slug}).fetchall()
                snap["autonomous_workflows"] = [
                    {"id": int(r[0]), "name": r[1], "status": r[2]} for r in rows
                ]
            except Exception as e:
                logger.warning("snapshot workflows failed: %s", e)

            # visibility_policy
            try:
                row = conn.execute(text(
                    "SELECT version, policy_json FROM public.dash_visibility_policy "
                    "WHERE project_slug=:s"
                ), {"s": slug}).first()
                if row:
                    pj = row[1]
                    if isinstance(pj, str):
                        try: pj = json.loads(pj)
                        except Exception: pj = {}
                    snap["visibility_policy"] = {"version": int(row[0]) if row[0] else 1,
                                                 "policy_json": pj}
            except Exception as e:
                logger.warning("snapshot visibility_policy failed: %s", e)

            # user_roles
            try:
                rows = conn.execute(text(
                    "SELECT user_id, role_name FROM public.dash_user_roles "
                    "WHERE project_slug=:s"
                ), {"s": slug}).fetchall()
                snap["user_roles"] = [
                    {"user_id": int(r[0]) if r[0] is not None else None,
                     "role_name": r[1]} for r in rows
                ]
            except Exception as e:
                logger.warning("snapshot user_roles failed: %s", e)

            # brain count + max id (project-scoped) for revert id-range delete
            try:
                row = conn.execute(text(
                    "SELECT COUNT(*), COALESCE(MAX(id),0) FROM public.dash_company_brain "
                    "WHERE project_slug=:s"
                ), {"s": slug}).first()
                if row:
                    snap["brain_count"] = int(row[0] or 0)
                    snap["brain_max_id"] = int(row[1] or 0)
            except Exception as e:
                logger.warning("snapshot brain_count failed: %s", e)
    except Exception as e:
        logger.warning("snapshot connect failed: %s", e)
    return snap


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers — each returns a step dict
# ─────────────────────────────────────────────────────────────────────────────
def _step_apply_agent_template(slug: str, template_name: str, user_id: str) -> dict:
    # Industry preset agent template apply/reconcile has been removed.
    # Step preserved as a no-op so the step list and history rows stay stable.
    return _step("apply_agent_template", True,
                 details=f"skipped (industry preset removed); template={template_name}")


def _step_apply_vertical(slug: str, vertical_name: str) -> dict:
    try:
        from dash.verticals import get_vertical
        bundle = get_vertical(vertical_name)
        if not bundle:
            return _step("apply_vertical", True, details=f"no vertical pack for '{vertical_name}', skipped")

        brain_entries = bundle.get("brain_entries") or []
        workflows = bundle.get("workflows") or []

        eng = _engine()
        if eng is None:
            return _step("apply_vertical", False, error="no db engine")

        brain_inserted = 0
        wf_inserted = 0
        with eng.begin() as conn:
            # Brain entries (scope project_slug)
            for entry in brain_entries:
                try:
                    name = (entry.get("name") or "").strip()
                    category = (entry.get("category") or "glossary").strip()
                    definition = entry.get("definition") or ""
                    metadata = entry.get("metadata") or {}
                    if not name or not definition:
                        continue
                    res = conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(category, name, definition, metadata, project_slug, created_by) "
                        "VALUES (:cat, :name, :defn, CAST(:meta AS jsonb), :slug, :cb) "
                        "ON CONFLICT DO NOTHING"
                    ), {
                        "cat": category, "name": name, "defn": definition,
                        "meta": json.dumps(metadata),
                        "slug": slug, "cb": "auto_apply",
                    })
                    if res.rowcount:
                        brain_inserted += 1
                except Exception as e:
                    logger.debug("brain insert skip (%s): %s", entry.get("name"), e)

            # Workflows
            for wf in workflows:
                try:
                    name = (wf.get("name") or "").strip()
                    if not name:
                        continue
                    conn.execute(text(
                        "INSERT INTO dash_autonomous_workflows "
                        "(project_slug, template_name, name, description, schedule, "
                        " query_template, expected_entity, expected_columns, action, status) "
                        "VALUES (:slug, :tname, :name, :desc, :sched, :qt, :ee, "
                        " CAST(:ec AS jsonb), :act, 'pending') "
                        "ON CONFLICT DO NOTHING"
                    ), {
                        "slug": slug,
                        "tname": f"vertical:{vertical_name}",
                        "name": name,
                        "desc": wf.get("description") or "",
                        "sched": wf.get("schedule") or "",
                        "qt": wf.get("query_template") or wf.get("query") or "",
                        "ee": wf.get("expected_entity") or "",
                        "ec": json.dumps(wf.get("expected_columns") or []),
                        "act": wf.get("action") or "log",
                    })
                    wf_inserted += 1
                except Exception as e:
                    logger.debug("workflow insert skip (%s): %s", wf.get("name"), e)

        return _step("apply_vertical", True,
                     details=f"vertical={vertical_name} brain+={brain_inserted} workflows+={wf_inserted}")
    except Exception as e:
        return _step("apply_vertical", False, error=str(e))


def _build_profile(slug: str) -> dict:
    """Build a `profile` dict for template_generator from the project's
    live schema. Shape matches what `generate_template` expects:
        {tables: [{name, row_count, columns:[{name,type,sample}], sample_rows}]}

    Best-effort — returns `{tables: []}` on any failure so the caller can
    fall back to the static-preset path.
    """
    import re as _re
    eng = _engine()
    if eng is None:
        return {"tables": []}
    schema = slug  # Dash convention: project slug == schema name
    ident = _re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    if not ident.fullmatch(schema or ""):
        return {"tables": []}
    tables_out: list[dict] = []
    try:
        with eng.connect() as conn:
            # tables (cap 40 — matches generator's _summarize_tables cap)
            t_rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=:s AND table_type='BASE TABLE' "
                "ORDER BY table_name LIMIT 40"
            ), {"s": schema}).fetchall()
            for (tname,) in t_rows:
                if not ident.fullmatch(tname or ""):
                    continue
                # columns (cap 30 per table)
                c_rows = conn.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name=:t "
                    "ORDER BY ordinal_position LIMIT 30"
                ), {"s": schema, "t": tname}).fetchall()
                cols = [{"name": cn, "type": ct, "sample": ""} for (cn, ct) in c_rows]
                # row count (cheap, capped via reltuples estimate fallback)
                row_count = None
                try:
                    rc = conn.execute(text(
                        f'SELECT COUNT(*) FROM "{schema}"."{tname}"'
                    )).scalar()
                    row_count = int(rc) if rc is not None else None
                except Exception:
                    row_count = None
                # sample rows (2)
                sample_rows: list[dict] = []
                try:
                    s_rows = conn.execute(text(
                        f'SELECT * FROM "{schema}"."{tname}" LIMIT 2'
                    )).mappings().all()
                    sample_rows = [dict(r) for r in s_rows]
                except Exception:
                    sample_rows = []
                tables_out.append({
                    "name": tname,
                    "row_count": row_count,
                    "columns": cols,
                    "sample_rows": sample_rows,
                })
    except Exception as e:
        logger.debug("_build_profile failed for %s: %s", slug, e)
    return {"tables": tables_out}


def _step_apply_template(slug: str, vertical: str) -> dict:
    """Generate a tailored agent template via the LLM-driven generator and
    apply it. Falls back to the static `apply_preset` path when the
    generator returns a degraded shell (LLM unavailable) so projects
    still get sane feature_config defaults.
    """
    try:
        from dash.learning.template_generator import (
            generate_template, apply_generated_template,
        )
        profile = _build_profile(slug)
        generated = generate_template(slug, profile, vertical or "generic") or {}

        if generated.get("_degraded"):
            # LLM unavailable / parse failure. Industry preset fallback removed —
            # report degraded outcome but keep pipeline alive.
            return _step(
                "apply_template", False,
                details=f"vertical={vertical or 'generic'} generator degraded",
                error="generator returned _degraded; industry preset fallback removed",
            )

        apply_res = apply_generated_template(slug, generated) or {}
        steps = apply_res.get("steps") or []
        ok_inner = sum(1 for s in steps if s.get("ok"))
        n_tabs = len((generated.get("tabs_recommended") or {}))
        n_kpi = len((generated.get("kpis") or []))
        n_wf = len((generated.get("workflow_scaffolds") or []))
        n_rules = len((generated.get("business_rules") or []))
        n_seeds = len((generated.get("question_seeds") or []))
        details = (
            f"vertical={vertical or 'generic'} tables={len(profile.get('tables') or [])} "
            f"kpis={n_kpi} workflows={n_wf} rules={n_rules} seeds={n_seeds} "
            f"tabs={n_tabs} inner_steps_ok={ok_inner}/{len(steps)}"
        )
        if not apply_res.get("ok"):
            return _step("apply_template", False, details=details,
                         error="; ".join([str(e) for e in (apply_res.get("errors") or []) if e])[:500])
        return _step("apply_template", True, details=details)
    except Exception as e:
        # Industry preset fallback removed. Report failure but never bubble up.
        return _step("apply_template", False,
                     error=f"generator exception: {e}")


def _step_apply_visibility_template(slug: str, vertical_name: str, user_id: str) -> dict:
    try:
        from dash.policy.templates import get_template as get_vis_tpl
        from dash.policy.loader import save_policy
        from dash.policy.schema import VisibilityPolicy

        # Try direct vertical → fall back to "generic"
        tpl = get_vis_tpl(vertical_name) or get_vis_tpl("generic")
        if not tpl:
            return _step("apply_visibility_template", True,
                         details=f"no visibility template for '{vertical_name}', skipped")

        policy_dict = tpl.get("policy") or {}
        try:
            pol = VisibilityPolicy(**policy_dict)
        except Exception as e:
            return _step("apply_visibility_template", False,
                         error=f"policy schema invalid: {e}")

        uid_int = user_id if isinstance(user_id, int) else None
        ver = save_policy(slug, pol, uid_int)
        return _step("apply_visibility_template", True,
                     details=f"template={vertical_name} version={ver}")
    except Exception as e:
        return _step("apply_visibility_template", False, error=str(e))


def _step_seed_roles(slug: str, vertical_name: str) -> dict:
    try:
        from dash.policy.templates import get_template as get_vis_tpl
        from dash.policy.roles import replace_roles
        tpl = get_vis_tpl(vertical_name) or get_vis_tpl("generic")
        if not tpl:
            return _step("seed_roles", True, details="no template, skipped")
        roles = tpl.get("suggested_roles") or []
        if not roles:
            return _step("seed_roles", True, details="no suggested_roles, skipped")
        replace_roles(slug, roles)
        return _step("seed_roles", True, details=f"roles={len(roles)}")
    except Exception as e:
        return _step("seed_roles", False, error=str(e))


def _step_derive_scope(slug: str) -> dict:
    try:
        from dash.scope_deriver import derive_scope
        res = derive_scope(slug)
        ok = bool(res and isinstance(res, dict))
        return _step("derive_scope_guardrail", ok,
                     details=f"topics={len((res or {}).get('topics') or [])}")
    except Exception as e:
        return _step("derive_scope_guardrail", False, error=str(e))


def _step_import_marketplace_skills(slug: str, vertical_name: str, user_id: str, limit: int = 5) -> dict:
    try:
        try:
            from dash.learning.skill_marketplace import list_marketplace, install_skill
        except Exception as e:
            return _step("import_marketplace_skills", True,
                         details=f"marketplace unavailable: {e}")

        try:
            skills = list_marketplace(template=vertical_name, limit=limit) or []
        except Exception as e:
            return _step("import_marketplace_skills", False, error=f"list failed: {e}")

        # Filter out error rows
        skills = [s for s in skills if isinstance(s, dict) and "id" in s and "error" not in s]
        uid_int = user_id if isinstance(user_id, int) else 0
        installed = 0
        for s in skills[:limit]:
            try:
                res = install_skill(int(s["id"]), slug, uid_int)
                if res and res.get("ok"):
                    installed += 1
            except Exception as e:
                logger.debug("install_skill skip id=%s: %s", s.get("id"), e)
        return _step("import_marketplace_skills", True,
                     details=f"candidates={len(skills)} installed={installed}")
    except Exception as e:
        return _step("import_marketplace_skills", False, error=str(e))


def _step_activate_workflows(slug: str) -> dict:
    try:
        eng = _engine()
        if eng is None:
            return _step("activate_workflows", False, error="no db engine")
        with eng.begin() as conn:
            res = conn.execute(text(
                "UPDATE dash_autonomous_workflows "
                "SET status='active' "
                "WHERE project_slug=:s AND status='pending'"
            ), {"s": slug})
        n = res.rowcount or 0
        return _step("activate_workflows", True, details=f"activated={n}")
    except Exception as e:
        return _step("activate_workflows", False, error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# History row persistence
# ─────────────────────────────────────────────────────────────────────────────
def _insert_history_row(
    slug: str,
    detection: dict,
    snapshot: dict,
    applied_steps: list[dict],
    applied: bool,
    applied_by: str,
    error: str | None = None,
) -> int | None:
    """Insert history row. Returns new id or None."""
    eng = _engine()
    if eng is None:
        return None
    vertical = (detection or {}).get("vertical")
    template = (detection or {}).get("template") or (detection or {}).get("agent_template")
    conf = (detection or {}).get("confidence")
    try:
        conf_f = float(conf) if conf is not None else None
    except Exception:
        conf_f = None
    apply_result = {
        "snapshot": snapshot or {},
        "applied_steps": applied_steps or [],
        "error": error,
    }
    try:
        with eng.begin() as conn:
            row = conn.execute(text(
                "INSERT INTO dash.dash_auto_apply_history "
                "(project_slug, vertical, template, confidence, detection, "
                " applied, apply_result, applied_by) "
                "VALUES (:slug, :v, :t, :c, CAST(:det AS jsonb), :ap, "
                " CAST(:apply_result AS jsonb), :by) "
                "RETURNING id"
            ), {
                "slug": slug, "v": vertical, "t": template, "c": conf_f,
                "det": json.dumps(detection or {}, default=str),
                "ap": bool(applied),
                "apply_result": json.dumps(apply_result, default=str),
                "by": str(applied_by or "auto"),
            }).first()
            return int(row[0]) if row else None
    except Exception as e:
        logger.warning("history insert failed: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────
def auto_apply_vertical(
    slug: str,
    detection: dict,
    user_id: str = "auto",
    *,
    derive_scope_step: bool = True,
) -> dict:
    """Apply full vertical pack atomically (fail-soft per step).

    Args:
        slug: project slug
        detection: classifier output. Must include ``vertical`` (key into
            ``dash/verticals``). May include ``template`` (agent template name
            from ``dash/templates``), ``confidence``, plus any extra metadata.
        user_id: 'auto' for daemon-driven, or string user_id for user-triggered.

    Returns:
        ``{ok, applied_steps[], errors[], history_id}``
    """
    if not slug:
        return {"ok": False, "applied_steps": [], "errors": ["missing slug"], "history_id": None}
    detection = detection or {}
    vertical = (detection.get("vertical") or "").strip().lower()
    template = (detection.get("template") or detection.get("agent_template") or vertical or "blank").strip()

    applied_steps: list[dict] = []

    def _timed(label: str, fn, *a, **k) -> dict:
        """Run a step fn, inject elapsed_ms into its returned _step() dict, log it."""
        t = time.perf_counter()
        d = fn(*a, **k)
        elapsed_ms = round((time.perf_counter() - t) * 1000)
        try:
            d["elapsed_ms"] = elapsed_ms
        except Exception:
            pass
        logger.info("auto_apply step %s: %sms", label, elapsed_ms)
        return d

    # ── Phase 0 (serial, MUST be first) — snapshot ─────────────────────────
    try:
        snapshot = _take_snapshot(slug)
        snap_step = _step("snapshot", True,
                          details=f"brain_count={snapshot.get('brain_count',0)} "
                                  f"workflows={len(snapshot.get('autonomous_workflows') or [])}")
    except Exception as e:
        snapshot = {}
        snap_step = _step("snapshot", False, error=str(e))
    # (snapshot itself isn't a _step_* fn; record it with no separate timing call)
    applied_steps.append(snap_step)

    # ── Phase 1 (PARALLEL) — independent steps ─────────────────────────────
    # Each opens its own DB connection / does its own work and is internally
    # fail-soft (try/except → _step dict). We run them concurrently.
    #
    # Ordering caveat: _step_apply_vertical and _step_apply_template both write
    # the SHARED tables dash_company_brain + dash_autonomous_workflows.
    # _step_apply_vertical uses INSERT ... ON CONFLICT DO NOTHING (idempotent),
    # but _step_apply_template delegates to apply_generated_template whose write
    # strategy is opaque from here. To stay correct-over-fast, we keep those two
    # SERIAL relative to each other (apply_vertical → apply_template) inside a
    # single parallel task, while everything else runs fully parallel.
    #
    # _step_activate_workflows is NOT here — it flips dash_autonomous_workflows
    # rows from 'pending'→'active', and those pending rows are INSERTED by
    # apply_vertical / apply_template. It MUST run after Phase 1 (see Phase 2).

    def _vertical_then_template() -> list[dict]:
        v = _timed("apply_vertical", _step_apply_vertical, slug, vertical)
        t = _timed("apply_template", _step_apply_template, slug, vertical or "full")
        return [v, t]

    phase1_tasks: list[tuple[str, Any]] = [
        ("apply_agent_template",
         lambda: [_timed("apply_agent_template", _step_apply_agent_template, slug, template, user_id)]),
        # apply_vertical + apply_template kept serial relative to each other
        ("vertical_then_template", _vertical_then_template),
        ("apply_visibility_template",
         lambda: [_timed("apply_visibility_template", _step_apply_visibility_template, slug, vertical, user_id)]),
        ("seed_roles",
         lambda: [_timed("seed_roles", _step_seed_roles, slug, vertical)]),
        ("import_marketplace_skills",
         lambda: [_timed("import_marketplace_skills", _step_import_marketplace_skills, slug, vertical, user_id)]),
    ]
    if derive_scope_step:
        phase1_tasks.append(
            ("derive_scope_guardrail",
             lambda: [_timed("derive_scope_guardrail", _step_derive_scope, slug)]))

    phase1_results: dict[str, list[dict]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): label for (label, fn) in phase1_tasks}
        for fut in concurrent.futures.as_completed(futs):
            label = futs[fut]
            try:
                phase1_results[label] = fut.result()
            except Exception as e:
                # A parallel task should never explode (each step is fail-soft),
                # but guard anyway so one failure can't kill the whole apply.
                logger.warning("auto_apply phase1 task %s raised: %s", label, e)
                phase1_results[label] = [_step(label, False, error=str(e))]

    # Collect phase-1 results in a STABLE order (matches original step ordering).
    applied_steps.append(_first(phase1_results.get("apply_agent_template")))
    for s in (phase1_results.get("vertical_then_template") or []):
        applied_steps.append(s)
    applied_steps.append(_first(phase1_results.get("apply_visibility_template")))
    applied_steps.append(_first(phase1_results.get("seed_roles")))
    if derive_scope_step:
        applied_steps.append(_first(phase1_results.get("derive_scope_guardrail")))
    else:
        # The training tail already derives scope separately this run; re-running
        # the ≤90s scope LLM here is pure duplicate work + concurrent-LLM
        # contention. Record a skipped marker so history still shows the step.
        applied_steps.append(_step("derive_scope_guardrail", True,
                                   details="skipped — already derived this run"))
    applied_steps.append(_first(phase1_results.get("import_marketplace_skills")))

    # ── Phase 2 (serial, MUST be last) — activate workflows ────────────────
    applied_steps.append(_timed("activate_workflows", _step_activate_workflows, slug))

    # Determine overall success: applied=True unless MORE THAN HALF failed
    total = len(applied_steps)
    failed = sum(1 for s in applied_steps if not s.get("ok"))
    applied = failed * 2 <= total  # majority succeeded
    errors = [f"{s['step']}: {s.get('error')}" for s in applied_steps if not s.get("ok") and s.get("error")]

    # Step 10 — record event
    history_id = _insert_history_row(
        slug=slug,
        detection=detection,
        snapshot=snapshot,
        applied_steps=applied_steps,
        applied=applied,
        applied_by=user_id,
        error="; ".join(errors)[:2000] if errors else None,
    )

    return {
        "ok": applied,
        "applied_steps": applied_steps,
        "errors": errors,
        "history_id": history_id,
        "vertical": vertical,
        "template": template,
        "failed_count": failed,
        "total_count": total,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Revert
# ─────────────────────────────────────────────────────────────────────────────
def revert_auto_apply(slug: str, history_id: int, user_id: str = "auto") -> dict:
    """Restore project from snapshot stored on a history row.

    - Resets ``dash_projects.feature_config`` to snapshot value
    - Deletes ``dash_template_bindings`` rows added since snapshot
    - Deletes ``dash_autonomous_workflows`` rows added since snapshot
    - Deletes ``dash_company_brain`` rows added since snapshot (id > snapshot.brain_max_id)
    - Restores ``dash_visibility_policy`` to snapshot
    - Marks history row reverted

    Returns ``{ok, restored_steps[]}``.
    """
    restored: list[dict] = []
    eng = _engine()
    if eng is None:
        return {"ok": False, "restored_steps": [_step("load_engine", False, error="no engine")]}

    # Load snapshot from history
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT snapshot, project_slug, reverted FROM dash.dash_auto_apply_history "
                "WHERE id=:id"
            ), {"id": int(history_id)}).first()
        if not row:
            return {"ok": False, "restored_steps": [_step("load_history", False, error="history not found")]}
        if row[1] != slug:
            return {"ok": False, "restored_steps": [_step("load_history", False, error="slug mismatch")]}
        if row[2]:
            return {"ok": False, "restored_steps": [_step("load_history", False, error="already reverted")]}
        snapshot = row[0]
        if isinstance(snapshot, str):
            try: snapshot = json.loads(snapshot)
            except Exception: snapshot = {}
        snapshot = snapshot or {}
        restored.append(_step("load_history", True, details=f"history_id={history_id}"))
    except Exception as e:
        return {"ok": False, "restored_steps": [_step("load_history", False, error=str(e))]}

    # 1. feature_config restore
    try:
        fc = snapshot.get("feature_config")
        with eng.begin() as conn:
            if fc is None:
                conn.execute(text(
                    "UPDATE public.dash_projects SET feature_config = NULL WHERE slug=:s"
                ), {"s": slug})
            else:
                conn.execute(text(
                    "UPDATE public.dash_projects SET feature_config = CAST(:c AS jsonb) "
                    "WHERE slug=:s"
                ), {"s": slug, "c": json.dumps(fc)})
        restored.append(_step("restore_feature_config", True))
    except Exception as e:
        restored.append(_step("restore_feature_config", False, error=str(e)))

    # 2. delete template bindings not in snapshot
    try:
        snap_refs = {b.get("template_ref") for b in (snapshot.get("template_bindings") or [])}
        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT template_ref FROM dash_template_bindings WHERE project_slug=:s"
            ), {"s": slug}).fetchall()
            to_del = [r[0] for r in rows if r[0] not in snap_refs]
            for ref in to_del:
                conn.execute(text(
                    "DELETE FROM dash_template_bindings "
                    "WHERE project_slug=:s AND template_ref=:r"
                ), {"s": slug, "r": ref})
        restored.append(_step("delete_new_template_bindings", True, details=f"deleted={len(to_del)}"))
    except Exception as e:
        restored.append(_step("delete_new_template_bindings", False, error=str(e)))

    # 3. delete autonomous_workflows not in snapshot
    try:
        snap_ids = {int(w.get("id")) for w in (snapshot.get("autonomous_workflows") or []) if w.get("id")}
        with eng.begin() as conn:
            if snap_ids:
                rows = conn.execute(text(
                    "SELECT id FROM dash_autonomous_workflows WHERE project_slug=:s"
                ), {"s": slug}).fetchall()
                to_del = [int(r[0]) for r in rows if int(r[0]) not in snap_ids]
                if to_del:
                    conn.execute(text(
                        "DELETE FROM dash_autonomous_workflows WHERE id = ANY(:ids)"
                    ), {"ids": to_del})
                deleted = len(to_del)
            else:
                # No prior workflows — delete all for project
                res = conn.execute(text(
                    "DELETE FROM dash_autonomous_workflows WHERE project_slug=:s"
                ), {"s": slug})
                deleted = res.rowcount or 0
        restored.append(_step("delete_new_workflows", True, details=f"deleted={deleted}"))
    except Exception as e:
        restored.append(_step("delete_new_workflows", False, error=str(e)))

    # 4. delete new brain entries (id > snapshot.brain_max_id)
    try:
        max_id = int(snapshot.get("brain_max_id") or 0)
        with eng.begin() as conn:
            res = conn.execute(text(
                "DELETE FROM public.dash_company_brain "
                "WHERE project_slug=:s AND id > :mid"
            ), {"s": slug, "mid": max_id})
        restored.append(_step("delete_new_brain_entries", True,
                              details=f"deleted={res.rowcount or 0} since_id>{max_id}"))
    except Exception as e:
        restored.append(_step("delete_new_brain_entries", False, error=str(e)))

    # 5. restore visibility_policy
    try:
        vp = snapshot.get("visibility_policy")
        with eng.begin() as conn:
            if vp is None:
                conn.execute(text(
                    "DELETE FROM public.dash_visibility_policy WHERE project_slug=:s"
                ), {"s": slug})
            else:
                pj = vp.get("policy_json") or {}
                ver = int(vp.get("version") or 1)
                conn.execute(text(
                    "INSERT INTO public.dash_visibility_policy "
                    "(project_slug, version, policy_json, updated_at) "
                    "VALUES (:s, :v, CAST(:p AS jsonb), now()) "
                    "ON CONFLICT (project_slug) DO UPDATE "
                    "  SET version=EXCLUDED.version, "
                    "      policy_json=EXCLUDED.policy_json, "
                    "      updated_at=now()"
                ), {"s": slug, "v": ver, "p": json.dumps(pj)})
        restored.append(_step("restore_visibility_policy", True))
    except Exception as e:
        restored.append(_step("restore_visibility_policy", False, error=str(e)))

    # 6. mark history row reverted
    try:
        with eng.begin() as conn:
            conn.execute(text(
                "UPDATE dash.dash_auto_apply_history "
                "SET reverted=TRUE, reverted_at=now(), reverted_by=:by "
                "WHERE id=:id"
            ), {"by": str(user_id or "auto"), "id": int(history_id)})
        restored.append(_step("mark_reverted", True))
    except Exception as e:
        restored.append(_step("mark_reverted", False, error=str(e)))

    failed = sum(1 for s in restored if not s.get("ok"))
    ok = failed == 0
    return {"ok": ok, "restored_steps": restored, "failed_count": failed}
