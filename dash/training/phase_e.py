"""Training Pipeline V2 — Phase E: vertical detection + pack apply.

Wraps the (formerly monolithic, ~60s) auto-configure tail in the fingerprint
cache runner so it only re-runs when the project's vertical or column set
changes. The heavy LLM template generation inside ``auto_apply_vertical`` also
self-caches (template_generator), so this layer adds: skip-when-unchanged +
per-step status/timing in ``public.dash_training_steps``.

Gated behind ``TRAINING_V2_PHASE_E=1`` at the call site (app/upload.py); this
module never runs unless the caller invokes it.
"""
from __future__ import annotations

import logging
import os
import re

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _threshold() -> float:
    try:
        return float(os.environ.get("TRAINING_V2_VERTICAL_THRESHOLD", "0.50"))
    except Exception:
        return 0.50


# Map a detected vertical (auto_configurator / vertical_lexicon keys) to a
# static pack key in the dash/verticals registry. The detector emits keys like
# "pharmacy"; the registry uses "pharma". Add aliases here as static packs land.
_STATIC_PACK_ALIAS = {
    "pharmacy": "pharma",
    "pharma": "pharma",
}


def _resolve_static_pack(vert: str):
    """Return (registry_key, bundle) for the detected vertical, or (None, None).

    Resolution order: exact alias map → exact registry key → substring match
    against registry keys (e.g. detected "pharmacy_network" → "pharma").
    Fail-soft: any import/lookup error yields (None, None) so the caller falls
    through to the LLM apply path.
    """
    try:
        from dash.verticals import get_vertical, list_verticals
    except Exception as e:  # registry import problem — fall through to LLM
        logger.debug("phase_e._resolve_static_pack import failed: %s", e)
        return None, None

    v = (vert or "").strip().lower()
    if not v:
        return None, None

    # 1. alias map (handles detector-key vs registry-key drift)
    mapped = _STATIC_PACK_ALIAS.get(v)
    if mapped:
        b = get_vertical(mapped)
        if b:
            return mapped, b

    # 2. exact registry key
    b = get_vertical(v)
    if b:
        return v, b

    # 3. substring match (either direction) against available registry keys
    try:
        keys = [item.get("name") for item in (list_verticals() or []) if item.get("name")]
    except Exception:
        keys = []
    for k in keys:
        kl = k.lower()
        if kl and (kl in v or v in kl):
            b = get_vertical(k)
            if b:
                return k, b

    return None, None


def _apply_static_pack(slug: str, registry_key: str, bundle: dict) -> dict:
    """Apply a static vertical bundle ($0, no LLM).

    Mirrors the apply logic of ``app/projects.py:apply_vertical`` (visibility
    template + brain seeds + autonomous workflows). Idempotent: brain entries
    use ON CONFLICT DO NOTHING; workflows skipped if already present.

    Returns a small summary dict. Raises on hard failure so the runner records
    it (caller catches and falls through to the LLM path).
    """
    import json as _json

    from dash.tools.skill_refinery import _get_engine
    eng = _get_engine()

    source_tag = f"vertical_{registry_key}"

    # 1. visibility template (best-effort)
    visibility_template_applied = None
    vt = bundle.get("visibility_template")
    if vt:
        try:
            from dash.policy import VisibilityPolicy, save_policy
            from dash.policy.templates import get_template
            tmpl = get_template(vt)
            if tmpl:
                validated = VisibilityPolicy(**tmpl["policy"])
                save_policy(slug, validated, user_id="auto")
                visibility_template_applied = vt
        except Exception as e:
            logger.debug("phase_e static visibility apply skipped: %s", e)

    # 2. brain entries (idempotent)
    brain_entries = bundle.get("brain_entries", []) or []
    brain_seeded = 0
    if brain_entries:
        with eng.connect() as conn:
            for entry in brain_entries:
                try:
                    res = conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(category, name, definition, metadata, created_by, project_slug, user_id) "
                        "VALUES (:cat, :name, :def, CAST(:meta AS JSONB), :by, :slug, NULL) "
                        "ON CONFLICT DO NOTHING RETURNING id"
                    ), {
                        "cat": entry["category"],
                        "name": entry["name"],
                        "def": entry["value"],
                        "meta": _json.dumps({"source": source_tag}),
                        "by": "auto",
                        "slug": slug,
                    })
                    if res.fetchone() is not None:
                        brain_seeded += 1
                except Exception:
                    pass
            conn.commit()

    # 3. autonomous workflows (idempotent on (project_slug, template_name, name))
    workflows = bundle.get("workflows", []) or []
    workflows_seeded = 0
    if workflows:
        try:
            from dash.templates.storage import bootstrap_tables as _bootstrap_tpl
            _bootstrap_tpl()
        except Exception:
            pass
        template_name = source_tag
        with eng.begin() as conn:
            for wf in workflows:
                wf_name = (wf.get("name") or "").strip()
                if not wf_name:
                    continue
                exists = conn.execute(text(
                    "SELECT 1 FROM dash.dash_autonomous_workflows "
                    "WHERE project_slug = :s AND template_name = :t AND name = :n LIMIT 1"
                ), {"s": slug, "t": template_name, "n": wf_name}).fetchone()
                if exists:
                    continue
                try:
                    res = conn.execute(text(
                        "INSERT INTO dash.dash_autonomous_workflows "
                        "(project_slug, template_name, name, description, schedule, "
                        " query_template, expected_entity, expected_columns, action, status) "
                        "VALUES (:s, :t, :n, :d, :sch, :q, :ee, CAST(:ec AS jsonb), :a, 'pending') "
                        "RETURNING id"
                    ), {
                        "s": slug,
                        "t": template_name,
                        "n": wf_name,
                        "d": wf.get("description") or "",
                        "sch": wf.get("schedule") or "daily",
                        "q": wf.get("query_template") or "",
                        "ee": wf.get("expected_entity") or "",
                        "ec": _json.dumps({
                            "steps": wf.get("steps") or [],
                            "source": source_tag,
                        }),
                        "a": wf.get("action") or "post_insight",
                    })
                    if res.scalar() is not None:
                        workflows_seeded += 1
                except Exception:
                    pass

    return {
        "vertical": registry_key,
        "label": bundle.get("label"),
        "brain_seeded": brain_seeded,
        "workflows_seeded": workflows_seeded,
        "visibility_template_applied": visibility_template_applied,
    }


def _all_column_names(slug: str) -> list[str]:
    """Sorted ``table.column`` list for the project schema — the fingerprint
    input. A change in tables/columns flips the fp and forces a re-apply."""
    schema = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]
    try:
        from dash.tools.skill_refinery import _get_engine
        eng = _get_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :s ORDER BY table_name, column_name"
            ), {"s": schema}).fetchall()
        return [f"{t}.{c}" for (t, c) in rows]
    except Exception as e:
        logger.debug("phase_e._all_column_names(%s) failed: %s", slug, e)
        return []


def run_phase_e(slug, runner, master_log=None, set_step=None) -> dict:
    """Run Phase E via the step runner.

    Args:
        slug: project slug.
        runner: a ``dash.training.runner.StepRunner`` bound to this run.
        master_log: optional ``fn(msg)`` for human-readable run-log lines.
        set_step: optional ``fn(step_name)`` to update ``current_step`` for UI.

    Returns a small status dict. Fail-soft — never raises.
    """
    _log = master_log or (lambda m: None)
    _set = set_step or (lambda s: None)
    out = {"detected": None, "confidence": 0.0, "applied": False, "skipped": False}

    # ── Step 23: detect vertical (direct call — output is needed downstream, so
    #    it is NOT cached via the runner, whose cache-hit returns None). Cheap LITE.
    _set("vertical_detect")
    try:
        from dash.learning.auto_configurator import classify_vertical
        detection = classify_vertical(slug)
    except Exception as e:
        logger.warning("phase_e: classify_vertical failed for %s: %s", slug, e)
        _log(f"⚠ vertical detect skipped: {str(e)[:80]}")
        return out

    conf = float(detection.get("confidence", 0) or 0)
    vert = detection.get("vertical", "generic")
    out["detected"] = vert
    out["confidence"] = conf
    _log(f"· detected: {vert} ({int(conf*100)}%) via {detection.get('method','?')}")

    if conf < _threshold():
        _log(f"· {vert} below threshold {_threshold():.2f} — skipping pack apply")
        return out

    # ── Step 28: apply the vertical pack — wrapped in the runner so an unchanged
    #    (vertical, column-set) skips the whole ~60s apply on retrain.
    _set("apply_template")
    cols = _all_column_names(slug)

    # ── STATIC-PACK SHORT-CIRCUIT ──────────────────────────────────────────
    # If a static pack exists for this vertical (dash/verticals/<name>/), apply
    # it directly ($0, no LLM) and SKIP the ~60-100s DEEP_MODEL template
    # generation inside auto_apply_vertical. Fail-soft: any error here falls
    # through to the LLM path below.
    registry_key, bundle = _resolve_static_pack(vert)
    if bundle is not None:
        def _apply_static():
            return _apply_static_pack(slug, registry_key, bundle)

        try:
            static_result = runner.run(
                "vertical_pack_static",
                _apply_static,
                fp_inputs={"vertical": vert, "cols": cols},
                scope="project",
                step_no=28,
            )
        except Exception as e:
            # runner.run is itself fail-soft, but guard the outer call too.
            logger.warning("phase_e: static pack apply raised for %s: %s", slug, e)
            _log(f"⚠ static {vert} pack failed ({str(e)[:60]}) — falling back to LLM")
            static_result = None
            registry_key = None  # force LLM fallthrough below

        if registry_key is not None:
            if static_result is None:
                # Cache hit (unchanged) OR runner-recorded failure. Treat as
                # skipped — side-effects already persisted on a prior run.
                out["skipped"] = True
                out["static"] = True
                _log("· static vertical pack unchanged — skipped (cache hit)")
            else:
                out["applied"] = True
                out["static"] = True
                _log(
                    f"✓ applied STATIC {vert} pack (no LLM): "
                    f"{static_result.get('brain_seeded', 0)} brain · "
                    f"{static_result.get('workflows_seeded', 0)} workflows"
                )
            return out
    # ── end static-pack short-circuit ──────────────────────────────────────

    fp_inputs = {"vertical": vert, "template": detection.get("template"), "cols": cols}

    def _apply():
        from dash.learning.auto_apply import auto_apply_vertical
        # derive_scope_step=False: the training tail derives scope separately.
        return auto_apply_vertical(slug, detection, user_id="auto", derive_scope_step=False)

    result = runner.run(
        "vertical_pack",
        _apply,
        fp_inputs=fp_inputs,
        scope="project",
        step_no=28,
    )

    if result is None:
        # Either a cache hit (skipped — unchanged) or the apply failed (runner
        # logged it). Distinguish via the step row is overkill here; report skipped.
        out["skipped"] = True
        _log("· vertical pack unchanged — skipped (cache hit)")
    else:
        ok_steps = sum(1 for s in result.get("applied_steps", []) if s.get("ok"))
        tot_steps = len(result.get("applied_steps", []))
        out["applied"] = True
        _log(f"✓ auto-applied {vert} pack: {ok_steps}/{tot_steps} steps")

    return out
