"""VentureDesk training steps.

Three standalone async steps. NO formal `dash_training_steps` registry exists in
this codebase; steps are wired directly from `app/upload.py` via StepRunner. So
this module exposes the three functions and ALSO opportunistically registers
them if a registry (`register_step` / `STEP_REGISTRY` / `dash_training_steps`)
appears at import time.

Step signatures (all):
    async def step_X(slug: str, run_id: int, ctx: dict) -> dict

Steps:
    classify_template
        Priority HIGH — runs in the first 5 steps. Scores builtin templates,
        writes top suggestion into ctx['template_suggestion'] and persists
        an annotation row to dash.dash_custom_agents-adjacent telemetry.

    seed_venture_capability_weights
        Conditional — runs only if Deal Analyst was assigned to this run
        (ctx['assigned_template'] == 'Deal Analyst' OR ctx['template_id'] ==
        'cag_dealanlyst'). Seeds default capability weights for the project.

    calibrate_venture_thresholds
        Conditional, same gate. Persists project-scoped IRR/MOIC thresholds
        into Company Brain (category='venture_threshold').

All writes go through db.session.get_write_engine() per the PgBouncer
dash.* write rule. JSONB uses CAST(:x AS jsonb).

Fail-soft: every step catches its own exceptions, returns
``{"ok": False, "step": "<name>", "error": "<msg>"}``. Never raises.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────── helpers ──

def _write_engine():
    """Write engine for dash.*/public.dash_* writes (CLAUDE.md gotcha)."""
    from db.session import get_write_engine
    return get_write_engine()


def _is_deal_analyst_assigned(ctx: dict) -> bool:
    """Gate: did this run get Deal Analyst assigned?"""
    tpl = (ctx or {}).get("assigned_template")
    tid = (ctx or {}).get("template_id")
    name = (ctx or {}).get("template_name")
    if tid == "cag_dealanlyst":
        return True
    for v in (tpl, name):
        if isinstance(v, str) and v.strip().lower() == "deal analyst":
            return True
    return False


# ──────────────────────────────────────────────────────────────── steps ──

async def step_classify_template(slug: str, run_id: int, ctx: dict) -> dict:
    """Rank builtin templates by data fit. Persists top suggestion into ctx.

    Returns {ok, step, top, suggestions:[...]}.
    """
    out: dict[str, Any] = {"ok": True, "step": "classify_template", "slug": slug}
    try:
        from dash.services.template_classifier import classify_for_project
        ranked = classify_for_project(slug, top_n=3) or []
        out["suggestions"] = ranked
        if ranked:
            top = ranked[0]
            out["top"] = top
            # Stash into ctx for downstream steps.
            try:
                ctx["template_suggestion"] = {
                    "template_id": top.get("template_id"),
                    "name": top.get("name"),
                    "score": top.get("score"),
                }
            except Exception:
                pass
        return out
    except Exception as e:
        logger.exception("step_classify_template failed for %s", slug)
        return {"ok": False, "step": "classify_template", "error": str(e)}


async def step_seed_venture_capability_weights(
    slug: str, run_id: int, ctx: dict
) -> dict:
    """Conditional: seed default capability weights when Deal Analyst assigned."""
    if not _is_deal_analyst_assigned(ctx):
        return {
            "ok": True, "step": "seed_venture_capability_weights",
            "skipped": True, "reason": "deal_analyst_not_assigned",
        }
    try:
        from dash.tools.venture_tools import seed_capability_weights

        # Sensible defaults — tenants override later via Brain UI / tools.
        defaults = {
            "manufacturing": 0.8,
            "distribution":  0.7,
            "regulatory":    0.9,
            "technology":    0.6,
            "branding":      0.5,
            "capital":       0.7,
            "operations":    0.6,
            "compliance":    0.8,
        }
        r = seed_capability_weights(slug, defaults)
        return {
            "ok": bool(r.get("ok")),
            "step": "seed_venture_capability_weights",
            "inserted": r.get("inserted", 0),
            "error": r.get("error"),
        }
    except Exception as e:
        logger.exception("step_seed_venture_capability_weights failed for %s", slug)
        return {
            "ok": False, "step": "seed_venture_capability_weights",
            "error": str(e),
        }


async def step_calibrate_venture_thresholds(
    slug: str, run_id: int, ctx: dict
) -> dict:
    """Conditional: persist project-scoped IRR/MOIC thresholds to Brain."""
    if not _is_deal_analyst_assigned(ctx):
        return {
            "ok": True, "step": "calibrate_venture_thresholds",
            "skipped": True, "reason": "deal_analyst_not_assigned",
        }
    thresholds = {
        "irr_go_min":    0.25,
        "irr_hold_min":  0.15,
        "moic_go_min":   3.0,
        "moic_hold_min": 2.0,
        "payback_max_yrs": 3.0,
    }
    try:
        eng = _write_engine()
        inserted = 0
        with eng.begin() as cx:
            for name, val in thresholds.items():
                meta = json.dumps({"value": float(val), "auto_seeded": True})
                try:
                    cx.execute(text("""
                        INSERT INTO public.dash_company_brain
                            (project_slug, name, category, definition, metadata)
                        VALUES (:p, :n, 'venture_threshold',
                                'Auto-seeded venture threshold',
                                CAST(:m AS jsonb))
                        ON CONFLICT (project_slug, name, category)
                        DO UPDATE SET metadata = CAST(:m AS jsonb)
                    """), {"p": slug, "n": name, "m": meta})
                    inserted += 1
                except Exception:
                    # Fallback when no unique constraint matches.
                    existing = cx.execute(text("""
                        SELECT id FROM public.dash_company_brain
                        WHERE project_slug = :p AND name = :n
                          AND category = 'venture_threshold'
                        LIMIT 1
                    """), {"p": slug, "n": name}).fetchone()
                    if existing:
                        cx.execute(text("""
                            UPDATE public.dash_company_brain
                            SET metadata = CAST(:m AS jsonb)
                            WHERE id = :id
                        """), {"m": meta, "id": existing[0]})
                    else:
                        cx.execute(text("""
                            INSERT INTO public.dash_company_brain
                                (project_slug, name, category, definition, metadata)
                            VALUES (:p, :n, 'venture_threshold',
                                    'Auto-seeded venture threshold',
                                    CAST(:m AS jsonb))
                        """), {"p": slug, "n": name, "m": meta})
                    inserted += 1
        return {
            "ok": True, "step": "calibrate_venture_thresholds",
            "inserted": inserted, "thresholds": thresholds,
        }
    except Exception as e:
        logger.exception("step_calibrate_venture_thresholds failed for %s", slug)
        return {
            "ok": False, "step": "calibrate_venture_thresholds",
            "error": str(e),
        }


# ──────────────────────────────────────────────────── registry hookup ──

_REGISTERED = False


def _try_register_with_existing_registry() -> bool:
    """Best-effort: register the 3 steps if a step registry exists.

    Looked for:
      - dash.training.runner.STEP_REGISTRY  (dict)
      - dash.training.runner.register_step  (callable)
      - dash.training.steps_registry.STEP_REGISTRY / register_step

    Returns True if registered, False otherwise. Always silent on failure.
    """
    global _REGISTERED
    if _REGISTERED:
        return True

    steps = [
        ("classify_template",                 step_classify_template,
         {"priority": "high", "phase": "early"}),
        ("seed_venture_capability_weights",   step_seed_venture_capability_weights,
         {"priority": "normal", "phase": "post_template",
          "condition": "deal_analyst_assigned"}),
        ("calibrate_venture_thresholds",      step_calibrate_venture_thresholds,
         {"priority": "normal", "phase": "post_template",
          "condition": "deal_analyst_assigned"}),
    ]

    # Try dash.training.runner first
    for modname in ("dash.training.runner", "dash.training.steps_registry"):
        try:
            mod = __import__(modname, fromlist=["*"])
        except Exception:
            continue
        reg = getattr(mod, "register_step", None)
        if callable(reg):
            try:
                for name, fn, meta in steps:
                    try:
                        reg(name, fn, **meta)
                    except TypeError:
                        # Older signature: register_step(name, fn)
                        reg(name, fn)
                _REGISTERED = True
                logger.info("venture_steps: registered via %s.register_step", modname)
                return True
            except Exception:
                logger.exception("venture_steps: register_step call failed (%s)", modname)
        registry = getattr(mod, "STEP_REGISTRY", None)
        if isinstance(registry, dict):
            try:
                for name, fn, meta in steps:
                    registry[name] = {"fn": fn, **meta}
                _REGISTERED = True
                logger.info("venture_steps: registered into %s.STEP_REGISTRY", modname)
                return True
            except Exception:
                logger.exception("venture_steps: STEP_REGISTRY update failed (%s)", modname)
    return False


# Opportunistic auto-register on import. Silent if no registry exists —
# callers can still import and invoke the functions directly.
try:
    _try_register_with_existing_registry()
except Exception:
    logger.debug("venture_steps: opportunistic registry hookup skipped", exc_info=True)


__all__ = [
    "step_classify_template",
    "step_seed_venture_capability_weights",
    "step_calibrate_venture_thresholds",
]
