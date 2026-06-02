"""SkillRefinery nightly cycle.

For each project (or one when slug given):
  1. Re-score tools.
  2. For tools with score < SCORE_THRESHOLD that are NOT in cooldown:
     a. Call SkillRefiner -> draft patch (dash_tool_patches, applied=false).
     b. Run shadow validation -> shadow_pass_rate.
     c. If pass_rate >= SHADOW_THRESHOLD, auto-apply.
  3. Cap at MAX_PATCHES_PER_DAY per project.
  4. Skip tools patched within COOLDOWN_DAYS.

Designed to be called from a daily K8s CronJob.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

SCORE_THRESHOLD = 60.0
SHADOW_THRESHOLD = 60
MAX_PATCHES_PER_DAY = 5
COOLDOWN_DAYS = 7
MIN_CALLS = 3  # require minimum invocations before patching

# Phase 8 — A/B revert
AB_AGING_HOURS = 24
AB_REGRESSION_THRESHOLD = 10.0  # revert if score_after < score_before - 10


def _engine():
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _list_projects() -> list[str]:
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT slug FROM public.dash_projects WHERE COALESCE(archived,false)=false ORDER BY slug"
        )).fetchall()
    return [r[0] for r in rows]


def _todays_patch_count(slug: str) -> int:
    eng = _engine()
    with eng.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_tool_patches "
            "WHERE project_slug = :s AND created_at >= NOW() - INTERVAL '1 day'"
        ), {"s": slug}).scalar()
    return int(n or 0)


def _in_cooldown(slug: str, tool_name: str) -> bool:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT 1 FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND project_slug = :s "
            "  AND created_at >= NOW() - INTERVAL ':d days'".replace(":d", str(int(COOLDOWN_DAYS))) +
            " LIMIT 1"
        ), {"t": tool_name, "s": slug}).first()
    return bool(row)


def _candidate_tools(slug: str) -> list[dict]:
    """Tools that could benefit from a patch right now."""
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT tool_name, score, success_rate, calls, fails, latency_p50_ms, last_error "
            "FROM public.dash_tool_scores "
            "WHERE (project_slug = :s OR project_slug IS NULL) "
            "  AND score < :th AND calls >= :min "
            "ORDER BY score ASC"
        ), {"s": slug, "th": SCORE_THRESHOLD, "min": MIN_CALLS}).mappings().all()
    return [dict(r) for r in rows]


def _recent_failures(slug: str, tool_name: str, limit: int = 10) -> list[dict]:
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT error_class, error_message, args_hash, latency_ms "
            "FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = FALSE "
            "ORDER BY ts DESC LIMIT :n"
        ), {"s": slug, "t": tool_name, "n": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


def _recent_successes(slug: str, tool_name: str, limit: int = 3) -> list[dict]:
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT args_hash, latency_ms FROM dash.dash_tool_utility_scores "
            "WHERE project_slug = :s AND tool_name = :t AND success = TRUE "
            "ORDER BY ts DESC LIMIT :n"
        ), {"s": slug, "t": tool_name, "n": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


def _active_description(slug: str, tool_name: str) -> str:
    eng = _engine()
    with eng.connect() as conn:
        r = conn.execute(text(
            "SELECT new_description FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL) "
            "  AND applied = TRUE AND reverted = FALSE "
            "ORDER BY version DESC LIMIT 1"
        ), {"t": tool_name, "s": slug}).first()
    return (r[0] if r else "") or ""


def _next_version(slug: str, tool_name: str) -> int:
    eng = _engine()
    with eng.connect() as conn:
        r = conn.execute(text(
            "SELECT COALESCE(MAX(version),0)+1 FROM public.dash_tool_patches "
            "WHERE tool_name = :t AND (project_slug = :s OR project_slug IS NULL)"
        ), {"t": tool_name, "s": slug}).scalar()
    return int(r or 1)


def run_for_project(slug: str) -> dict[str, Any]:
    """Process one project's candidates. Returns summary dict."""
    from dash.tools.skill_refinery import compute_utility_scores, invalidate_patch_cache
    from dash.agents.skill_refiner import propose_patch, shadow_validate

    summary: dict[str, Any] = {
        "project_slug": slug,
        "rescored": 0,
        "candidates": 0,
        "drafted": 0,
        "applied": 0,
        "skipped_cooldown": 0,
        "skipped_cap": 0,
        "shadow_blocked": 0,
        "errors": [],
        "tools": [],
    }

    try:
        scored = compute_utility_scores(project_slug=slug)
        summary["rescored"] = len(scored)
    except Exception as e:
        summary["errors"].append(f"rescore: {e}")
        return summary

    candidates = _candidate_tools(slug)
    summary["candidates"] = len(candidates)

    for cand in candidates:
        if _todays_patch_count(slug) >= MAX_PATCHES_PER_DAY:
            summary["skipped_cap"] += 1
            break

        tool_name = cand["tool_name"]
        if _in_cooldown(slug, tool_name):
            summary["skipped_cooldown"] += 1
            summary["tools"].append({"tool": tool_name, "status": "cooldown"})
            continue

        try:
            failures = _recent_failures(slug, tool_name)
            successes = _recent_successes(slug, tool_name)
            old_desc = _active_description(slug, tool_name)
            patch = propose_patch(
                tool_name=tool_name,
                old_description=old_desc,
                score=float(cand["score"]),
                success_rate=float(cand["success_rate"] or 0),
                calls=int(cand["calls"]),
                fails=int(cand["fails"]),
                p50_ms=int(cand["latency_p50_ms"] or 0),
                failures=failures,
            )
        except Exception as e:
            summary["errors"].append(f"propose {tool_name}: {e}")
            summary["tools"].append({"tool": tool_name, "status": "propose_error", "error": str(e)[:200]})
            continue

        # Constraint gates — reject malformed patches before burning shadow $$.
        # Fail-soft: gate errors do NOT block the patch (defense-in-depth, not
        # primary safety). Real safety still rides on shadow_validate.
        try:
            from dash.learning.skill_patch_constraints import validate_patch as _vp, is_disabled as _gate_off
            if not _gate_off():
                _vr = _vp(old_desc, patch.get("new_description", ""), kind="tool")
                if not _vr.ok:
                    summary.setdefault("constraint_rejected", 0)
                    summary["constraint_rejected"] += 1
                    summary["tools"].append({
                        "tool": tool_name, "status": "constraint_rejected",
                        "issues": _vr.issues, "stats": _vr.stats,
                    })
                    continue
        except Exception as _ge:
            # Gate error → log + continue (don't block patch on gate bug)
            summary["errors"].append(f"constraint_gate {tool_name}: {str(_ge)[:200]}")

        # Shadow validate immediately.
        try:
            verdict = shadow_validate(
                tool_name, old_desc, patch["new_description"],
                patch["default_args"], failures, successes,
            )
            pr = int(verdict.get("pass_rate") or 0)
        except Exception as e:
            summary["errors"].append(f"shadow {tool_name}: {e}")
            pr = 0
            verdict = {"pass_rate": 0, "summary": f"shadow error: {e}", "verdicts": []}

        # Insert draft + (maybe) apply atomically.
        eng = _engine()
        with eng.begin() as conn:
            ver = _next_version(slug, tool_name)
            patch_id = conn.execute(text(
                "INSERT INTO public.dash_tool_patches "
                "(tool_name, project_slug, version, old_description, new_description, "
                " default_args, reason, failure_samples, score_before, shadow_pass_rate, "
                " source, applied, applied_at) "
                "VALUES (:t, :s, :v, :old, :new, :args, :reason, :fails, :score, :pr, "
                " 'auto', :applied, CASE WHEN :applied THEN NOW() ELSE NULL END) "
                "RETURNING id"
            ), {
                "t": tool_name, "s": slug, "v": ver,
                "old": old_desc, "new": patch["new_description"],
                "args": json.dumps(patch["default_args"]),
                "reason": (patch.get("reason") or "")[:500],
                "fails": json.dumps(failures),
                "score": float(cand["score"]),
                "pr": pr,
                "applied": pr >= SHADOW_THRESHOLD,
            }).scalar()

            if pr >= SHADOW_THRESHOLD:
                # Demote previously-active patches for same tool/project.
                conn.execute(text(
                    "UPDATE public.dash_tool_patches SET applied = FALSE "
                    "WHERE tool_name = :t AND (project_slug IS NOT DISTINCT FROM :s) "
                    "  AND id <> :id"
                ), {"t": tool_name, "s": slug, "id": patch_id})

        summary["drafted"] += 1
        if pr >= SHADOW_THRESHOLD:
            summary["applied"] += 1
            summary["tools"].append({
                "tool": tool_name, "status": "applied", "patch_id": patch_id,
                "version": ver, "pass_rate": pr,
            })
        else:
            summary["shadow_blocked"] += 1
            summary["tools"].append({
                "tool": tool_name, "status": "shadow_blocked", "patch_id": patch_id,
                "version": ver, "pass_rate": pr,
            })

    invalidate_patch_cache()
    return summary


def run_for_all_projects() -> list[dict]:
    out = []
    for slug in _list_projects():
        try:
            out.append(run_for_project(slug))
        except Exception as e:
            logger.warning("skill_refinery cycle %s failed: %s", slug, e)
            out.append({"project_slug": slug, "errors": [str(e)]})
    # Phase 8 — A/B aging check across all projects.
    try:
        ab = ab_revert_check()
        out.append({"project_slug": "_ab_check", **ab})
    except Exception as e:
        logger.warning("ab_revert_check failed: %s", e)
    # Phase G — cross-tenant finding promotion.
    try:
        from dash.dashboards.agents.promotion import run_promotion_cycle
        promo = run_promotion_cycle()
        out.append({"project_slug": "_finding_promotion", **promo})
    except Exception as e:
        logger.warning("finding promotion failed: %s", e)
    # Phase: dashboard skill scoring (reward-driven refinery for dash skill bundles).
    try:
        dash_skills = score_dashboard_skills()
        out.append({"project_slug": "_dashboard_skills", "skills": dash_skills})
    except Exception as e:
        logger.warning("score_dashboard_skills failed: %s", e)
    return out


# ── Phase 8 — A/B revert ──────────────────────────────────────────────
def ab_revert_check() -> dict:
    """For patches applied ≥AB_AGING_HOURS ago without score_after:
       recompute current score, persist as score_after, revert if regression."""
    from dash.tools.skill_refinery import compute_utility_scores, invalidate_patch_cache
    eng = _engine()
    summary = {"checked": 0, "reverted": 0, "kept": 0, "details": []}

    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, tool_name, project_slug, score_before "
            "FROM public.dash_tool_patches "
            "WHERE applied = TRUE AND reverted = FALSE "
            "  AND score_after IS NULL "
            "  AND applied_at IS NOT NULL "
            "  AND applied_at <= NOW() - INTERVAL ':h hours'".replace(":h", str(int(AB_AGING_HOURS)))
        )).mappings().all()
    rows = [dict(r) for r in rows]

    # Group rescore by project to avoid recomputing N times.
    by_project: dict[str, list[dict]] = {}
    for r in rows:
        by_project.setdefault(r["project_slug"] or "", []).append(r)

    for slug, patches in by_project.items():
        try:
            scored = compute_utility_scores(project_slug=slug or None)
        except Exception as e:
            summary["details"].append({"slug": slug, "error": f"rescore failed: {e}"})
            continue
        score_map = {s["tool_name"]: float(s["score"]) for s in scored}

        for p in patches:
            tool = p["tool_name"]
            before = float(p["score_before"] or 0)
            after = score_map.get(tool)
            summary["checked"] += 1

            if after is None:
                # No telemetry since apply — skip; will recheck next run.
                summary["details"].append({"id": p["id"], "tool": tool, "status": "no_data"})
                continue

            regressed = after < (before - AB_REGRESSION_THRESHOLD)
            with eng.begin() as conn:
                conn.execute(text(
                    "UPDATE public.dash_tool_patches "
                    "SET score_after = :sa "
                    + (", reverted = TRUE, reverted_at = NOW(), "
                       " applied = FALSE, "
                       " revert_reason = :rr"
                       if regressed else "") +
                    " WHERE id = :id"
                ), {"sa": after, "id": p["id"],
                    "rr": f"score regressed: {before:.1f} -> {after:.1f}" if regressed else None})

            if regressed:
                summary["reverted"] += 1
                summary["details"].append({
                    "id": p["id"], "tool": tool, "slug": slug,
                    "status": "reverted", "before": before, "after": after,
                })
            else:
                summary["kept"] += 1
                summary["details"].append({
                    "id": p["id"], "tool": tool, "slug": slug,
                    "status": "kept", "before": before, "after": after,
                })

    if summary["reverted"]:
        invalidate_patch_cache()
    return summary


# ── Phase: Dashboard-skill refinery ───────────────────────────────────
# Monitors the dashboard skill bundle (skl_dashboard_intent, skl_dashboard_narrator,
# skl_dashboard_refiner, skl_panel_announcer, skl_narrative_{investor,ops,customer,exec},
# skl_layout_*, skl_dash_* bundles). Reward formula combines verified-cell-rate,
# judge score, and inverse latency. Low-scoring skills with sufficient sample
# size get drafted patches via the LLM-drafter pathway (or stub-logged).

DASH_SKILL_SCORE_THRESHOLD = 60.0       # below this → draft a patch candidate
DASH_SKILL_MIN_RUNS = 5                 # need at least N runs to avoid noise
DASH_SKILL_ROLLING_DAYS = 7
DASH_SKILL_LATENCY_NORM_MS = 60000      # 60s → normalized latency of 1.0


def _normalize_latency(mean_latency_ms: float) -> float:
    """Clamp mean latency to [0,1] using DASH_SKILL_LATENCY_NORM_MS as ceiling."""
    if mean_latency_ms is None or mean_latency_ms <= 0:
        return 0.0
    return min(1.0, float(mean_latency_ms) / float(DASH_SKILL_LATENCY_NORM_MS))


def _compute_dashboard_skill_score(
    mean_verified: float,
    mean_panels: float,
    mean_judge: float,
    mean_latency_ms: float,
) -> float:
    """Reward formula:
       0.5 * (verified_cell_count / panel_count)
     + 0.3 * (judge_score / 100)
     + 0.2 * (1 - normalized_latency)
    Returns 0-100. Higher = better.
    """
    verified_rate = 0.0
    if mean_panels and mean_panels > 0:
        verified_rate = min(1.0, float(mean_verified or 0) / float(mean_panels))
    judge_norm = max(0.0, min(1.0, float(mean_judge or 0) / 100.0))
    latency_norm = _normalize_latency(mean_latency_ms or 0)
    score = (0.5 * verified_rate) + (0.3 * judge_norm) + (0.2 * (1.0 - latency_norm))
    return round(score * 100.0, 2)


def _draft_patch_for_skill(skill_id: str, context: dict | None = None) -> dict:
    """Stub LLM-drafter for a single skill. If an existing implementation lives
    elsewhere in this file in the future, replace this stub. For now, just log
    the candidate so SkillRefinery's nightly pipeline records intent without
    needing a real propose-patch wired."""
    payload = {"skill_id": skill_id, "drafted": False, "reason": "stub_logged", "context": context or {}}
    logger.info("dashboard skill patch candidate (stub): %s", payload)
    return payload


# ── Dashboard-skill LLM patch drafter ─────────────────────────────────
DASH_SKILL_PATCH_CAP_PER_DAY = 5          # max draft patches per cycle (global cap)
DASH_SKILL_PATCH_COOLDOWN_DAYS = 7        # per-skill cooldown


def _first_object(text_blob: str) -> dict | None:
    """Balanced-brace scanner — extract the first JSON object from arbitrary
    LLM output (handles ```fences, prose, trailing junk). Returns dict or None."""
    if not text_blob:
        return None
    # Strip common ```json fences
    s = text_blob.strip()
    if s.startswith("```"):
        # drop first line + trailing fence
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    # Find first '{'
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = s[start:i + 1]
                try:
                    return json.loads(blob)
                except Exception:
                    # Trailing comma repair (best-effort)
                    try:
                        import re as _re
                        repaired = _re.sub(r",(\s*[}\]])", r"\1", blob)
                        return json.loads(repaired)
                    except Exception:
                        return None
    return None


def _dashboard_skill_in_cooldown(skill_id: str) -> bool:
    """Return True if this skill received a draft patch within COOLDOWN_DAYS."""
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT 1 FROM public.dash_tool_patches "
                "WHERE tool_name = :s "
                "  AND created_at >= NOW() - INTERVAL '"
                + str(int(DASH_SKILL_PATCH_COOLDOWN_DAYS)) + " days' "
                "LIMIT 1"
            ), {"s": skill_id}).first()
        return bool(row)
    except Exception as e:
        logger.warning("_dashboard_skill_in_cooldown(%s) failed: %s", skill_id, e)
        return False


def _dashboard_patches_today_count() -> int:
    """Count draft patches created today across all dashboard skills."""
    eng = _engine()
    try:
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_tool_patches "
                "WHERE created_at >= NOW() - INTERVAL '1 day' "
                "  AND tool_name LIKE 'skl_%'"
            )).scalar()
        return int(n or 0)
    except Exception:
        return 0


def _load_failing_runs(skill_id: str, limit: int = 5) -> list[dict]:
    """Pull last N failing runs (judge_score < 70 OR verified < panel*0.5)."""
    eng = _engine()
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT panel_count, verified_cell_count, judge_score, "
                "       stage, ran_at, latency_ms "
                "FROM public.dash_dashboard_skill_runs "
                "WHERE skill_id = :s "
                "  AND (judge_score < 70 "
                "       OR verified_cell_count < (COALESCE(panel_count,0) * 0.5)) "
                "ORDER BY ran_at DESC LIMIT :n"
            ), {"s": skill_id, "n": int(limit)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("_load_failing_runs(%s) failed: %s", skill_id, e)
        return []


def _next_dashboard_skill_version(skill_id: str) -> int:
    """Compute next version for a dashboard-skill patch (tool_name=skill_id)."""
    eng = _engine()
    try:
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT COALESCE(MAX(version),0)+1 FROM public.dash_tool_patches "
                "WHERE tool_name = :s"
            ), {"s": skill_id}).scalar()
        return int(r or 1)
    except Exception:
        return 1


def _persist_dashboard_skill_patch(
    skill_id: str,
    proposed_instructions: str,
    rationale: str,
    expected_improvement: str,
    failing_examples: list[dict],
    current_instructions: str,
) -> int | None:
    """INSERT draft patch into dash_tool_patches. Returns patch id or None."""
    try:
        from db.session import get_write_engine
    except Exception as e:
        logger.warning("_persist_dashboard_skill_patch: get_write_engine import failed: %s", e)
        return None
    try:
        eng = get_write_engine()
        ver = _next_dashboard_skill_version(skill_id)
        reason_blob = (rationale or "")
        if expected_improvement:
            reason_blob = (reason_blob + " | expected: " + expected_improvement)[:500]
        with eng.begin() as conn:
            pid = conn.execute(text(
                "INSERT INTO public.dash_tool_patches "
                "(tool_name, project_slug, version, "
                " old_description, new_description, "
                " default_args, reason, failure_samples, "
                " source, applied) "
                "VALUES (:t, NULL, :v, :old, :new, "
                "        CAST(:args AS jsonb), :reason, CAST(:fails AS jsonb), "
                "        'auto', FALSE) "
                "RETURNING id"
            ), {
                "t": skill_id,
                "v": ver,
                "old": (current_instructions or "")[:8000],
                "new": (proposed_instructions or "")[:16000],
                "args": json.dumps({}),
                "reason": reason_blob,
                "fails": json.dumps(failing_examples or []),
            }).scalar()
        return int(pid) if pid is not None else None
    except Exception as e:
        logger.warning("_persist_dashboard_skill_patch(%s) failed: %s", skill_id, e)
        return None


def _draft_patch_for_dashboard_skill(skill_id: str, context: dict) -> dict | None:
    """LLM-driven patch drafter for a dashboard pipeline skill.

    Loads current skill instructions, samples recent FAILING runs, asks the
    DEEP_MODEL for revised instructions, parses JSON, persists draft to
    dash_tool_patches. Returns metadata dict on success, None on failure.
    """
    # 1. Load current skill instructions
    try:
        from dash.skills.registry import load_skill
        skill = load_skill(skill_id, agent_name="skill_refinery", audit=False)
        if not skill or not skill.get("ok", True) is False and skill.get("error"):
            # load_skill returns {ok:False,error:...} on miss
            pass
        if skill.get("error"):
            logger.info("draft_patch_for_dashboard_skill: skill not found: %s", skill_id)
            return None
        current_instructions = skill.get("instructions") or ""
    except Exception as e:
        logger.warning("load_skill(%s) failed: %s", skill_id, e)
        return None

    if not current_instructions:
        logger.info("draft_patch_for_dashboard_skill: skill %s has empty instructions", skill_id)
        return None

    # 2. Load failing runs
    failing = _load_failing_runs(skill_id, limit=5)
    if not failing:
        logger.info("draft_patch_for_dashboard_skill: no failing runs for %s", skill_id)
        return None

    # 3. Build prompt
    failing_lines = []
    for r in failing:
        js = r.get("judge_score")
        vc = r.get("verified_cell_count")
        pc = r.get("panel_count")
        st = r.get("stage") or "?"
        failing_lines.append(f"- stage={st} judge={js} verified={vc}/{pc}")
    failing_block = "\n".join(failing_lines) if failing_lines else "(no examples)"

    prompt = (
        f"You are a senior prompt engineer improving a dashboard pipeline skill named `{skill_id}`.\n\n"
        f"CURRENT SKILL INSTRUCTIONS:\n{current_instructions[:6000]}\n\n"
        f"RECENT FAILING RUNS (judge_score / verified_cell_count / panel_count):\n"
        f"{failing_block}\n\n"
        f"The skill is failing because: judge scores are low and/or verified cells fall below "
        f"half the panel count. This suggests the generated output is inaccurate, ungrounded, "
        f"or fails downstream checks.\n\n"
        f"Propose a REVISED instructions text that addresses the failure patterns. Keep the "
        f"same output schema and tone. Output STRICT JSON ONLY:\n"
        '{\n'
        '  "revised_instructions": "...",\n'
        '  "rationale": "1-2 sentences on what changed and why",\n'
        '  "expected_improvement": "what failure mode this fixes"\n'
        '}\n'
    )

    # 4. Call DEEP_MODEL
    try:
        from dash.settings import training_llm_call
        resp = training_llm_call(prompt, "deep_analysis")
    except Exception as e:
        logger.warning("training_llm_call failed for %s: %s", skill_id, e)
        return None

    if not resp:
        logger.info("draft_patch_for_dashboard_skill: empty LLM response for %s", skill_id)
        return None

    # 5. Parse JSON
    parsed = _first_object(resp)
    if not parsed or not isinstance(parsed, dict):
        logger.warning("draft_patch_for_dashboard_skill: JSON parse failed for %s", skill_id)
        return None

    revised = (parsed.get("revised_instructions") or "").strip()
    rationale = (parsed.get("rationale") or "").strip()
    expected = (parsed.get("expected_improvement") or "").strip()
    if not revised:
        logger.info("draft_patch_for_dashboard_skill: empty revised_instructions for %s", skill_id)
        return None

    # Constraint gates — reject malformed dashboard-skill patches before persist.
    # Fail-soft: gate import error never blocks the patch path.
    try:
        from dash.learning.skill_patch_constraints import validate_patch as _vp, is_disabled as _gate_off
        if not _gate_off():
            _vr = _vp(current_instructions or "", revised, kind="skill")
            if not _vr.ok:
                logger.info(
                    "draft_patch_for_dashboard_skill(%s) rejected by constraints: %s",
                    skill_id, _vr.issues
                )
                return None
    except Exception as _ge:
        logger.debug("constraint gate skipped for %s: %s", skill_id, _ge)

    # 6. Persist
    current_version = _next_dashboard_skill_version(skill_id) - 1  # patch we're about to write is current+1
    patch_id = _persist_dashboard_skill_patch(
        skill_id=skill_id,
        proposed_instructions=revised,
        rationale=rationale,
        expected_improvement=expected,
        failing_examples=failing,
        current_instructions=current_instructions,
    )
    if patch_id is None:
        return None

    return {
        "skill_id": skill_id,
        "patch_id": patch_id,
        "current_version": current_version,
        "proposed_instructions": revised[:500] + ("..." if len(revised) > 500 else ""),
        "rationale": rationale,
        "expected_improvement": expected,
        "source_rows_count": len(failing),
    }


def apply_dashboard_skill_patch(patch_id: int) -> dict:
    """Apply a draft dashboard-skill patch: optionally shadow-validate, then
    write proposed instructions back to dash_skills. Marks patch row applied.

    Returns {ok, applied, skill_id, version, reason?}.
    """
    try:
        from db.session import get_write_engine
    except Exception as e:
        return {"ok": False, "error": f"get_write_engine import failed: {e}"}

    eng = get_write_engine()
    # Load patch row
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT id, tool_name, version, new_description, applied, reverted "
                "FROM public.dash_tool_patches WHERE id = :id"
            ), {"id": int(patch_id)}).mappings().first()
    except Exception as e:
        return {"ok": False, "error": f"load patch failed: {e}"}

    if not row:
        return {"ok": False, "error": "patch_not_found"}
    if row["applied"]:
        return {"ok": False, "error": "already_applied", "skill_id": row["tool_name"]}
    if row["reverted"]:
        return {"ok": False, "error": "patch_reverted", "skill_id": row["tool_name"]}

    skill_id = row["tool_name"]
    new_instructions = row["new_description"] or ""
    if not new_instructions:
        return {"ok": False, "error": "empty_instructions", "skill_id": skill_id}

    # Optional shadow validation (use existing shadow_validate if importable)
    shadow_pass = None
    try:
        from dash.agents.skill_refiner import shadow_validate as _sv
        verdict = _sv(
            skill_id,
            "",                       # old_desc (n/a for skills)
            new_instructions,
            {},                       # default_args
            [],                       # failures (already considered in drafting)
            [],                       # successes
        )
        shadow_pass = int(verdict.get("pass_rate") or 0) if isinstance(verdict, dict) else None
        if shadow_pass is not None and shadow_pass < 60:
            return {
                "ok": False,
                "error": "shadow_validation_failed",
                "skill_id": skill_id,
                "shadow_pass_rate": shadow_pass,
            }
    except Exception as e:
        # Shadow validator not available / errored — proceed (fail-soft as spec).
        logger.info("apply_dashboard_skill_patch: shadow validate skipped (%s)", e)

    # Apply: write instructions back to dash_skills + mark patch applied
    try:
        with eng.begin() as conn:
            updated = conn.execute(text(
                "UPDATE dash.dash_skills "
                "SET instructions = :ins, updated_at = NOW() "
                "WHERE id = :sid"
            ), {"ins": new_instructions, "sid": skill_id}).rowcount
            if not updated:
                # Skill row missing — abort, don't mark patch applied
                return {"ok": False, "error": "skill_row_missing", "skill_id": skill_id}
            conn.execute(text(
                "UPDATE public.dash_tool_patches "
                "SET applied = TRUE, applied_at = NOW(), "
                "    shadow_pass_rate = :sp "
                "WHERE id = :id"
            ), {"id": int(patch_id), "sp": shadow_pass})
    except Exception as e:
        return {"ok": False, "error": f"apply write failed: {e}", "skill_id": skill_id}

    return {
        "ok": True,
        "applied": True,
        "skill_id": skill_id,
        "version": int(row["version"]),
        "patch_id": int(patch_id),
        "shadow_pass_rate": shadow_pass,
    }


def score_dashboard_skills() -> list[dict]:
    """Score all dashboard skills using rolling DASH_SKILL_ROLLING_DAYS window
    of dash_dashboard_skill_runs. Draft candidate patches for skills below
    threshold with sufficient sample size. Returns per-skill summaries.
    """
    eng = _engine()
    out: list[dict] = []
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT skill_id, "
                "       COUNT(*)                       AS runs, "
                "       AVG(NULLIF(panel_count,0))     AS mean_panels, "
                "       AVG(verified_cell_count)       AS mean_verified, "
                "       AVG(judge_score)               AS mean_judge, "
                "       AVG(latency_ms)                AS mean_latency_ms "
                "FROM public.dash_dashboard_skill_runs "
                "WHERE ran_at >= NOW() - INTERVAL ':d days'".replace(":d", str(int(DASH_SKILL_ROLLING_DAYS))) +
                " GROUP BY skill_id"
            )).mappings().all()
    except Exception as e:
        logger.warning("score_dashboard_skills query failed: %s", e)
        return out

    for r in rows:
        skill_id = r["skill_id"]
        runs = int(r["runs"] or 0)
        mean_panels = float(r["mean_panels"] or 0)
        mean_verified = float(r["mean_verified"] or 0)
        mean_judge = float(r["mean_judge"] or 0)
        mean_latency_ms = float(r["mean_latency_ms"] or 0)
        score = _compute_dashboard_skill_score(mean_verified, mean_panels, mean_judge, mean_latency_ms)

        entry = {
            "skill_id": skill_id,
            "runs": runs,
            "score": score,
            "mean_verified": mean_verified,
            "mean_panels": mean_panels,
            "mean_judge": mean_judge,
            "mean_latency_ms": mean_latency_ms,
            "drafted": False,
        }

        if score < DASH_SKILL_SCORE_THRESHOLD and runs >= DASH_SKILL_MIN_RUNS:
            # Global per-cycle cap on draft patches (per spec: max 5/project/day,
            # here applied as a per-cycle dashboard-skill cap to bound LLM cost).
            if _dashboard_patches_today_count() >= DASH_SKILL_PATCH_CAP_PER_DAY:
                entry["draft_skipped"] = "daily_cap_reached"
                out.append(entry)
                continue
            # Per-skill cooldown
            if _dashboard_skill_in_cooldown(skill_id):
                entry["draft_skipped"] = "cooldown"
                out.append(entry)
                continue
            try:
                draft = _draft_patch_for_dashboard_skill(skill_id, context=entry)
                if draft:
                    entry["drafted"] = True
                    entry["draft_result"] = draft
                else:
                    entry["drafted"] = False
                    entry["draft_skipped"] = "drafter_returned_none"
            except Exception as e:
                logger.warning("_draft_patch_for_dashboard_skill %s failed: %s", skill_id, e)
                entry["draft_error"] = str(e)[:200]
        out.append(entry)
    return out


def _get_skill_override(project_slug: str, skill_id: str) -> str | None:
    """Return the project-scoped override instructions for a skill, if any.
    Used by runtime _skill_prefix() to let a per-tenant override beat the
    global skill registry entry. Returns None when no override exists."""
    if not project_slug or not skill_id:
        return None
    eng = _engine()
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT instructions FROM public.dash_skill_overrides "
                "WHERE project_slug = :p AND skill_id = :s "
                "LIMIT 1"
            ), {"p": project_slug, "s": skill_id}).first()
        return (row[0] if row else None)
    except Exception as e:
        logger.warning("_get_skill_override(%s, %s) failed: %s", project_slug, skill_id, e)
        return None
