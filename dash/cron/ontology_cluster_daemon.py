"""Ontology auto-cluster daemon (Phase B).

Periodically scans the template registry + brain for merge candidates
and either:

* **auto-merges** high-confidence pairs (≥0.95) into a canonical
  `dash_company_brain` entity whose ``metadata.aliases`` enumerates every
  member, marking the losers as superseded via
  ``metadata.superseded_by`` (non-destructive — never DELETE).
* **queues** medium-confidence pairs (0.70–0.95) into ``dash_promotion_log``
  with ``approver IS NULL`` so a human can approve via the existing
  PROMOTE tab in the Ontology Workbench.
* **skips** anything below 0.70.

Reuses the rule-based suggester from ``app.ontology_api`` (extracted
into ``compute_cluster_suggestions`` as a shared helper). No LLM calls.

Operational guarantees:

* **Idempotent.** If the canonical entity already lists the candidate
  in ``metadata.aliases``, the merge is a no-op. Pending promotions are
  deduped on (fact_text, fact_type) before insert.
* **Multi-worker safe.** Auto-merge writes claim each candidate via an
  atomic UPDATE on ``dash_company_brain`` predicated on the *absence*
  of the new alias — so two workers racing on the same pair will only
  produce one merge (the loser sees zero rows updated and skips).
* **Bounded.** Caps at ``MAX_AUTO_MERGES_PER_CYCLE`` per cycle.
* **Disable flag.** ``ONTOLOGY_CLUSTER_DISABLED=1`` skips daemon start.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Observability tracing (fail-soft; no-op if unavailable or TRACING_DISABLED).
try:
    from dash.obs.trace import trace_span
except Exception:  # noqa: BLE001
    from contextlib import contextmanager as _cm

    @_cm
    def trace_span(*_a, **_k):  # type: ignore
        yield None


# ── Tunables ──────────────────────────────────────────────────────────────

DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60  # 6h
AUTO_MERGE_THRESHOLD = 0.95
QUEUE_THRESHOLD = 0.70
MAX_AUTO_MERGES_PER_CYCLE = 50
MAX_SUGGESTIONS_PER_CYCLE = 200


# ── Helpers ───────────────────────────────────────────────────────────────


def _engine():
    """Resolve the shared SQL engine, or None if unavailable."""
    try:
        from db.session import get_sql_engine
    except Exception:  # pragma: no cover — import-time fallback
        from db import get_sql_engine  # type: ignore[no-redef]
    return get_sql_engine()


def _safe_meta(value: Any) -> dict:
    """Coerce a JSONB column read into a dict (handles str / None / dict)."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


# ── Auto-merge ────────────────────────────────────────────────────────────


def _try_auto_merge(cn, primary: str, merge_candidate: str,
                    reason: str, confidence: float) -> str:
    """Attempt to merge ``merge_candidate`` into ``primary``.

    Strategy (non-destructive + multi-worker safe):

    1. Look up (or create) the canonical row for ``primary`` in
       ``dash_company_brain`` (category ``entity``, scope NULL/NULL).
    2. Append ``merge_candidate`` to ``metadata.aliases`` only when not
       already present, using an atomic UPDATE … WHERE predicated on
       *absence* of the alias. If 0 rows updated, another worker already
       merged or it is a no-op — caller skips.
    3. For each row whose ``name`` equals ``merge_candidate`` (any scope),
       set ``metadata.superseded_by = primary`` if not already.

    Returns one of: "merged", "noop", "skipped".
    """
    from sqlalchemy import text as _t

    # Step 1 — ensure canonical row exists. Idempotent.
    row = cn.execute(_t(
        "SELECT id, COALESCE(metadata, '{}'::jsonb) FROM dash_company_brain "
        "WHERE name = :n AND category IN ('entity','object_type') "
        "  AND project_slug IS NULL AND user_id IS NULL "
        "ORDER BY id LIMIT 1"
    ), {"n": primary}).fetchone()

    if row is None:
        # Bootstrap canonical entry. Use INSERT … ON CONFLICT DO NOTHING via
        # a guard SELECT + INSERT inside the same txn — simpler than a
        # partial unique index that may not exist.
        meta = {
            "aliases": [merge_candidate],
            "auto_clustered": True,
            "auto_clustered_reason": reason,
            "auto_clustered_confidence": confidence,
        }
        cn.execute(_t(
            "INSERT INTO dash_company_brain "
            "(category, name, definition, project_slug, user_id, metadata) "
            "VALUES ('entity', :n, :d, NULL, NULL, CAST(:m AS jsonb))"
        ), {"n": primary,
            "d": f"Auto-clustered canonical for {primary}",
            "m": json.dumps(meta)})
    else:
        canonical_id = row[0]
        existing_meta = _safe_meta(row[1])
        existing_aliases = list(existing_meta.get("aliases") or [])
        if merge_candidate in existing_aliases:
            return "noop"

        # Atomic claim — only one worker can append the new alias. The
        # NOT-contains JSON predicate ensures losers see zero rows updated.
        # NOTE: use CAST(:x AS text) instead of `:x::text` — the `::` Postgres
        # cast collides with SQLAlchemy's named-parameter parser inside text()
        # and aborts the transaction silently (same class of bug as the
        # `:m::jsonb` fix in app/upload.py _save_to_db).
        result = cn.execute(_t(
            "UPDATE dash_company_brain "
            "   SET metadata = jsonb_set("
            "         COALESCE(metadata, '{}'::jsonb),"
            "         '{aliases}',"
            "         (COALESCE(metadata->'aliases', '[]'::jsonb) "
            "            || to_jsonb(CAST(:cand AS text))),"
            "         true"
            "       )"
            " WHERE id = :id"
            "   AND NOT (COALESCE(metadata->'aliases','[]'::jsonb) ? :cand)"
        ), {"id": canonical_id, "cand": merge_candidate})
        if (result.rowcount or 0) == 0:
            return "noop"

    # Step 3 — mark losers as superseded. Soft, non-destructive. Skip rows
    # that already point at the same primary.
    # NOTE: writes metadata.superseded_by (JSONB key) — NOT the bi-temporal
    # `superseded_by BIGINT` column added by migration 067. Both can coexist
    # since they target different storage (JSONB vs column). The JSONB path
    # is used here because it predates the bi-temporal migration and stores
    # the canonical NAME (text) rather than a FK id.
    # CAST(:primary AS text) avoids the `::` SQLAlchemy named-param collision.
    cn.execute(_t(
        "UPDATE dash_company_brain "
        "   SET metadata = jsonb_set("
        "         COALESCE(metadata, '{}'::jsonb),"
        "         '{superseded_by}',"
        "         to_jsonb(CAST(:primary AS text)),"
        "         true"
        "       )"
        " WHERE name = :cand "
        "   AND COALESCE(metadata->>'superseded_by', '') <> :primary"
    ), {"primary": primary, "cand": merge_candidate})

    return "merged"


# ── Queue medium-confidence ───────────────────────────────────────────────


def _queue_promotion(cn, primary: str, merge_candidate: str,
                     reason: str, confidence: float) -> str:
    """Queue a pending promotion (approver IS NULL).

    Idempotent — checks for an existing pending row with the same
    fact_text + fact_type before inserting. Returns "queued" or "noop".
    """
    from sqlalchemy import text as _t

    fact_text = (
        f"merge candidate: {merge_candidate} -> {primary} "
        f"(confidence={confidence:.2f}, reason={reason})"
    )
    fact_type = "alias_merge"

    existing = cn.execute(_t(
        "SELECT 1 FROM dash_promotion_log "
        " WHERE approver IS NULL "
        "   AND fact_type = :ft "
        "   AND fact_text = :tx "
        " LIMIT 1"
    ), {"ft": fact_type, "tx": fact_text}).fetchone()
    if existing:
        return "noop"

    cn.execute(_t(
        "INSERT INTO dash_promotion_log "
        "  (source_project_slug, fact_text, fact_type, approval_method, "
        "   pii_scrubbed, triangulation_count, approver) "
        "VALUES (NULL, :tx, :ft, 'auto_cluster_pending', TRUE, 1, NULL)"
    ), {"tx": fact_text, "ft": fact_type})
    return "queued"


# ── Cycle ─────────────────────────────────────────────────────────────────


def run_cycle() -> dict[str, int]:
    """Execute a single auto-cluster cycle.

    Returns a counts dict with keys:
    candidates_found, auto_merged, queued, skipped, errors.
    Safe to call ad-hoc (e.g. from the run-now endpoint).
    """
    from app.ontology_api import compute_cluster_suggestions

    out = {
        "candidates_found": 0,
        "auto_merged": 0,
        "queued": 0,
        "skipped": 0,
        "errors": 0,
    }
    logger.info("ontology_cluster_daemon: cycle_started")

    try:
        suggestions = compute_cluster_suggestions(MAX_SUGGESTIONS_PER_CYCLE)
    except Exception:
        logger.exception("ontology_cluster_daemon: suggester failed")
        out["errors"] += 1
        return out
    out["candidates_found"] = len(suggestions)

    eng = _engine()
    if eng is None:
        logger.warning("ontology_cluster_daemon: no engine, aborting")
        out["errors"] += 1
        return out

    auto_budget = MAX_AUTO_MERGES_PER_CYCLE

    for s in suggestions:
        primary = s.get("primary")
        cand = s.get("merge_candidate")
        conf = float(s.get("confidence") or 0.0)
        reason = str(s.get("reason") or "")
        if not primary or not cand or primary == cand:
            out["skipped"] += 1
            continue

        if conf < QUEUE_THRESHOLD:
            out["skipped"] += 1
            continue

        try:
            if conf >= AUTO_MERGE_THRESHOLD and auto_budget > 0:
                with eng.begin() as cn:
                    status = _try_auto_merge(cn, primary, cand, reason, conf)
                if status == "merged":
                    auto_budget -= 1
                    out["auto_merged"] += 1
                else:
                    out["skipped"] += 1
            else:
                with eng.begin() as cn:
                    status = _queue_promotion(cn, primary, cand, reason, conf)
                if status == "queued":
                    out["queued"] += 1
                else:
                    out["skipped"] += 1
        except Exception:
            logger.exception(
                "ontology_cluster_daemon: candidate failed primary=%s cand=%s",
                primary, cand,
            )
            out["errors"] += 1

    logger.info(
        "ontology_cluster_daemon: cycle_done candidates=%d auto_merged=%d "
        "queued=%d skipped=%d errors=%d",
        out["candidates_found"], out["auto_merged"], out["queued"],
        out["skipped"], out["errors"],
    )
    return out


# ── Async loop ────────────────────────────────────────────────────────────


def _interval_seconds() -> int:
    """Resolve interval from env, defaulting to 6h."""
    raw = os.getenv("ONTOLOGY_CLUSTER_INTERVAL_SECONDS", "")
    try:
        v = int(raw)
        if v > 0:
            return v
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _is_disabled() -> bool:
    return (os.getenv("ONTOLOGY_CLUSTER_DISABLED", "").lower()
            in ("1", "true", "yes"))


async def ontology_cluster_loop() -> None:
    """Forever-loop: run an auto-cluster cycle, sleep, repeat.

    Honors ``ONTOLOGY_CLUSTER_DISABLED=1`` (returns immediately) and
    ``ONTOLOGY_CLUSTER_INTERVAL_SECONDS`` (default 21600 = 6h). Cycle
    failures are caught and logged — the loop never crashes the worker.
    """
    if _is_disabled():
        logger.info("ontology_cluster_daemon: disabled via env")
        return
    interval = _interval_seconds()
    logger.info(
        "ontology_cluster_daemon: starting (interval=%ds)", interval,
    )
    while True:
        try:
            # run_cycle is sync + does DB IO — push to a thread to avoid
            # blocking the event loop.
            with trace_span("cron.ontology_cluster", kind="cron"):
                await asyncio.to_thread(run_cycle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ontology_cluster_daemon: cycle crashed")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


__all__ = ["run_cycle", "ontology_cluster_loop"]
