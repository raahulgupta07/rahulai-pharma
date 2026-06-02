"""LLM-driven template generator.

Replaces the static PRESETS dict in `dash/feature_config.py` with an
LLM call that synthesizes a full agent-template tailored to the
project's actual schema profile + detected vertical.

Public API
----------
- generate_template(project_slug, profile, vertical) -> dict
- apply_generated_template(project_slug, generated)  -> dict

Caching
-------
Generated templates are cached on
`dash_projects.feature_config.scope._generated_template` (JSON) so the
LLM call only fires once per (slug, vertical, profile-fingerprint).

Apply
-----
`apply_generated_template` writes results into:
  - feature_config            (tabs/tools/agents/scope)
  - dash_company_brain        (KPIs + glossary defs)
  - dash_business_rules_db    (business rules)
  - dash_autonomous_workflows (workflow scaffolds)

Uses snapshot+revert pattern from `auto_apply.py`. Each step is wrapped
in try/except so partial failures are recorded but never abort the
whole apply.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _engine():
    # Use the UNGUARDED engine (not db.session.get_sql_engine, which installs a
    # _guard_public_schema listener that blocks writes to public.* — meant for
    # the Engineer agent's LLM-generated SQL, not trusted internal pipeline
    # writes). With the guarded engine, _store_cache's `UPDATE public.dash_projects`
    # and the brain/rules/workflow INSERTs into public.* were silently blocked,
    # so the generated template was NEVER cached → the ~60s DEEP_MODEL generation
    # re-ran on every retrain. This engine matches what feature_config writes use.
    from dash.tools.skill_refinery import _get_engine
    return _get_engine()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _step(name: str, ok: bool, details: str = "", error: str | None = None) -> dict:
    return {"step": name, "ok": bool(ok), "details": details, "error": error}


def _profile_fingerprint(profile: dict, vertical: str) -> str:
    """Stable hash of (vertical, table names + col names) so caches
    invalidate when schema changes but not when sample rows shift."""
    try:
        tables = profile.get("tables") or []
        sig = {
            "v": vertical,
            "t": sorted([
                {
                    "name": t.get("name"),
                    "cols": sorted([c.get("name") for c in (t.get("columns") or []) if c.get("name")]),
                }
                for t in tables if t.get("name")
            ], key=lambda x: x["name"] or ""),
        }
        return hashlib.sha256(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:16]
    except Exception:
        return "nofp"


def _sanitize_json_text(content: str) -> str:
    """Mirror the sanitization in app/upload.py get_knowledge_file_content:
    strip control chars + drop invalid backslash escapes."""
    if not content:
        return content
    # Strip ASCII control chars except \t \n \r
    sanitized = "".join(ch for ch in content if ch in "\t\n\r" or ord(ch) >= 32)
    # Drop invalid \X escapes (JSON allows " \ / b f n r t u)
    valid = set('"\\/bfnrtu')
    out: list[str] = []
    i = 0
    n = len(sanitized)
    while i < n:
        ch = sanitized[i]
        if ch == "\\" and i + 1 < n:
            nxt = sanitized[i + 1]
            if nxt in valid:
                out.append(ch); out.append(nxt); i += 2; continue
            out.append(nxt); i += 2; continue
        out.append(ch); i += 1
    return "".join(out)


def _parse_llm_json(content: str) -> dict | None:
    """Sanitize + parse. Handles markdown fences (already stripped by
    training_llm_call but be defensive) + control chars + bad escapes."""
    if not content:
        return None
    s = content.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        s = s[nl + 1:] if nl != -1 else s[3:]
    if s.endswith("```"):
        s = s[:-3]
    s = s.strip().strip("`").strip()
    if s.lower().startswith("json"):
        s = s[4:].strip()
    s = _sanitize_json_text(s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:
            return json.loads(s, strict=False)
        except Exception:
            logger.warning("template_generator: JSON parse failed (len=%d)", len(s))
            return None


# ─────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────
def _summarize_tables(profile: dict, col_truncate: int = 100) -> str:
    out: list[str] = []
    for t in (profile.get("tables") or [])[:40]:
        name = t.get("name") or "?"
        row_count = t.get("row_count")
        cols = t.get("columns") or []
        col_summaries: list[str] = []
        for c in cols[:30]:
            cn = (c.get("name") or "?")
            ct = (c.get("type") or "")
            sample = c.get("sample") or c.get("sample_values") or ""
            if isinstance(sample, list):
                sample = ", ".join(str(x) for x in sample[:3])
            blob = f"{cn} ({ct}) e.g. {sample}".strip()
            if len(blob) > col_truncate:
                blob = blob[:col_truncate].rstrip() + "…"
            col_summaries.append(blob)
        sample_rows = t.get("sample_rows")
        rows_blob = ""
        if sample_rows:
            try:
                rows_blob = " | sample_rows=" + json.dumps(sample_rows[:2])[:200]
            except Exception:
                rows_blob = ""
        out.append(f"- {name} (rows={row_count}): {'; '.join(col_summaries)}{rows_blob}")
    return "\n".join(out) if out else "(no tables)"


_PROMPT = """You are designing an agent template for a {vertical} project.

The project's actual schema:
{tables_summary}

Output a single JSON object (no prose, no markdown) with EXACTLY these keys:

{{
  "kpis": [
    {{"name": "...", "definition": "...", "sql_pattern": "SELECT ..."}}
  ],
  "question_seeds": ["20 short starter questions a user might ask"],
  "workflow_scaffolds": [
    {{"name": "...", "trigger": "cron or event", "sql": "SELECT ...", "action": "log|alert|post_insight|suggest"}}
  ],
  "lexicon_additions": {{"term": ["synonym1", "synonym2"]}},
  "business_rules": [
    {{"name": "...", "condition": "human-readable SQL or rule", "severity": "info|warn|critical"}}
  ],
  "tabs_recommended": {{
    "analysis": true, "data": true, "query": true, "chart": true, "sources": true
  }},
  "tools_recommended": {{
    "sql": true, "charts": true, "ml": false, "dashboards": true,
    "forecast": false, "anomaly": false, "auto_campaign_daemon": false
  }},
  "agents_recommended": {{
    "analyst": true, "engineer": true, "researcher": true, "data_scientist": false
  }}
}}

Constraints:
- Exactly 20 question_seeds.
- 4-10 KPIs.
- 3-8 workflow_scaffolds. SQL must reference REAL tables/columns from the schema above.
- 3-10 business_rules.
- lexicon_additions: 5-20 vertical-specific terms with synonyms.
- All boolean flags must be true/false (no nulls).
Return ONLY the JSON object."""


# ─────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────
def _load_cached(project_slug: str, fp: str) -> dict | None:
    eng = _engine()
    if eng is None:
        return None
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT feature_config FROM public.dash_projects WHERE slug=:s"
            ), {"s": project_slug}).first()
        if not row or not row[0]:
            return None
        fc = row[0]
        if isinstance(fc, str):
            try: fc = json.loads(fc)
            except Exception: return None
        gt = ((fc.get("scope") or {}).get("_generated_template")) or None
        if gt and gt.get("_fingerprint") == fp:
            return gt.get("payload")
    except Exception as e:
        logger.debug("template_generator: cache load failed: %s", e)
    return None


def _store_cache(project_slug: str, fp: str, vertical: str, payload: dict) -> None:
    eng = _engine()
    if eng is None:
        return
    try:
        with eng.begin() as conn:
            row = conn.execute(text(
                "SELECT feature_config FROM public.dash_projects WHERE slug=:s"
            ), {"s": project_slug}).first()
            fc: dict = {}
            if row and row[0]:
                raw = row[0]
                if isinstance(raw, str):
                    try: fc = json.loads(raw)
                    except Exception: fc = {}
                elif isinstance(raw, dict):
                    fc = dict(raw)
            scope = dict(fc.get("scope") or {})
            scope["_generated_template"] = {
                "_fingerprint": fp,
                "_vertical": vertical,
                "_generated_at": _now_iso(),
                "payload": payload,
            }
            fc["scope"] = scope
            conn.execute(text(
                "UPDATE public.dash_projects SET feature_config = CAST(:c AS jsonb) WHERE slug=:s"
            ), {"c": json.dumps(fc), "s": project_slug})
    except Exception as e:
        logger.warning("template_generator: cache store failed: %s", e)


def generate_template(project_slug: str, profile: dict, vertical: str) -> dict:
    """Generate a tailored agent-template via DEEP_MODEL → LITE_MODEL fallback.

    Args:
        project_slug: tenant id.
        profile: {tables: [{name, columns, sample_rows, row_count}], ...}
        vertical: detected vertical key (e.g. 'pharmacy').

    Returns:
        dict with keys: kpis, question_seeds, workflow_scaffolds,
        lexicon_additions, business_rules, tabs_recommended,
        tools_recommended, agents_recommended.
    """
    vertical = (vertical or "generic").strip().lower()
    profile = profile or {}

    fp = _profile_fingerprint(profile, vertical)
    cached = _load_cached(project_slug, fp)
    if cached:
        logger.info("template_generator: cache hit slug=%s vertical=%s fp=%s", project_slug, vertical, fp)
        return cached

    from dash.settings import training_llm_call, set_llm_project

    prompt = _PROMPT.format(
        vertical=vertical,
        tables_summary=_summarize_tables(profile),
    )

    # Scope the LLM cost to this project's daily cap.
    try:
        set_llm_project(project_slug)
    except Exception:
        pass

    # Primary: DEEP_MODEL (gpt-5.4-mini via deep_analysis task).
    # training_llm_call already auto-retries with LITE_MODEL on empty.
    raw = training_llm_call(prompt, task="deep_analysis")
    parsed = _parse_llm_json(raw) if raw else None

    if not parsed:
        # Explicit LITE retry as defense-in-depth.
        logger.warning("template_generator: deep_analysis empty/unparseable, retrying with extraction task")
        raw = training_llm_call(prompt, task="extraction")
        parsed = _parse_llm_json(raw) if raw else None

    if not parsed:
        logger.error("template_generator: both DEEP and LITE failed for slug=%s vertical=%s", project_slug, vertical)
        # Return a minimal empty shell so callers don't crash.
        parsed = {
            "kpis": [], "question_seeds": [], "workflow_scaffolds": [],
            "lexicon_additions": {}, "business_rules": [],
            "tabs_recommended": {"analysis": True, "data": True, "query": True, "chart": True, "sources": True},
            "tools_recommended": {"sql": True, "charts": True, "ml": False, "dashboards": True,
                                  "forecast": False, "anomaly": False, "auto_campaign_daemon": False},
            "agents_recommended": {"analyst": True, "engineer": True, "researcher": True, "data_scientist": False},
            "_degraded": True,
        }

    # Normalize: ensure all expected keys exist.
    for k, default in (
        ("kpis", []), ("question_seeds", []), ("workflow_scaffolds", []),
        ("lexicon_additions", {}), ("business_rules", []),
        ("tabs_recommended", {}), ("tools_recommended", {}), ("agents_recommended", {}),
    ):
        parsed.setdefault(k, default)

    _store_cache(project_slug, fp, vertical, parsed)
    return parsed


# ─────────────────────────────────────────────────────────────────────
# Apply — snapshot + per-step try/except
# ─────────────────────────────────────────────────────────────────────
def _snapshot(slug: str) -> dict:
    snap: dict[str, Any] = {"captured_at": _now_iso(), "feature_config": None,
                            "brain_max_id": 0, "workflows_max_id": 0, "rules_max_id": 0}
    eng = _engine()
    if eng is None:
        return snap
    try:
        with eng.connect() as conn:
            try:
                r = conn.execute(text("SELECT feature_config FROM public.dash_projects WHERE slug=:s"),
                                 {"s": slug}).first()
                if r and r[0]:
                    fc = r[0]
                    if isinstance(fc, str):
                        try: fc = json.loads(fc)
                        except Exception: fc = {}
                    snap["feature_config"] = fc
            except Exception:
                pass
            for col, table in (("brain_max_id", "public.dash_company_brain"),
                               ("workflows_max_id", "dash_autonomous_workflows"),
                               ("rules_max_id", "public.dash_business_rules_db")):
                try:
                    r = conn.execute(text(
                        f"SELECT COALESCE(MAX(id),0) FROM {table} WHERE project_slug=:s"
                    ), {"s": slug}).first()
                    if r:
                        snap[col] = int(r[0] or 0)
                except Exception:
                    pass
    except Exception as e:
        logger.debug("template_generator snapshot connect failed: %s", e)
    return snap


def _revert(slug: str, snap: dict) -> None:
    eng = _engine()
    if eng is None:
        return
    try:
        with eng.begin() as conn:
            # Restore feature_config
            fc = snap.get("feature_config")
            if fc is None:
                conn.execute(text("UPDATE public.dash_projects SET feature_config=NULL WHERE slug=:s"),
                             {"s": slug})
            else:
                conn.execute(text(
                    "UPDATE public.dash_projects SET feature_config=CAST(:c AS jsonb) WHERE slug=:s"
                ), {"s": slug, "c": json.dumps(fc)})
            # Delete new rows (id > snapshot max)
            for col, table in (("brain_max_id", "public.dash_company_brain"),
                               ("workflows_max_id", "dash_autonomous_workflows"),
                               ("rules_max_id", "public.dash_business_rules_db")):
                try:
                    conn.execute(text(
                        f"DELETE FROM {table} WHERE project_slug=:s AND id > :mid"
                    ), {"s": slug, "mid": int(snap.get(col) or 0)})
                except Exception as e:
                    logger.debug("revert delete %s failed: %s", table, e)
    except Exception as e:
        logger.warning("template_generator: revert failed: %s", e)


def _apply_feature_config(slug: str, generated: dict) -> dict:
    try:
        from dash.feature_config import set_feature_config, get_feature_config
        cur = get_feature_config(slug)
        new = {
            "tabs": {**cur.get("tabs", {}), **(generated.get("tabs_recommended") or {})},
            "tools": {**cur.get("tools", {}), **(generated.get("tools_recommended") or {})},
            "agents": {**cur.get("agents", {}), **(generated.get("agents_recommended") or {})},
            "scope": cur.get("scope") or {},
        }
        # Stamp lexicon additions into scope for guardrail/context use.
        scope = dict(new["scope"])
        lex = generated.get("lexicon_additions") or {}
        if lex:
            existing = dict(scope.get("lexicon") or {})
            existing.update({str(k): list(v) if isinstance(v, list) else [str(v)] for k, v in lex.items()})
            scope["lexicon"] = existing
        new["scope"] = scope
        set_feature_config(slug, new)
        return _step("feature_config", True, details=f"lexicon+={len(lex)}")
    except Exception as e:
        return _step("feature_config", False, error=str(e))


def _apply_brain(slug: str, generated: dict) -> dict:
    eng = _engine()
    if eng is None:
        return _step("brain", False, error="no db engine")
    inserted = 0
    try:
        with eng.begin() as conn:
            for kpi in (generated.get("kpis") or []):
                name = (kpi.get("name") or "").strip()
                defn = (kpi.get("definition") or "").strip()
                if not name or not defn:
                    continue
                meta = {"sql_pattern": kpi.get("sql_pattern") or "", "source": "template_generator"}
                try:
                    r = conn.execute(text(
                        "INSERT INTO public.dash_company_brain "
                        "(category, name, definition, metadata, project_slug, created_by) "
                        "VALUES ('kpi', :name, :defn, CAST(:meta AS jsonb), :slug, 'template_generator') "
                        "ON CONFLICT DO NOTHING"
                    ), {"name": name, "defn": defn, "meta": json.dumps(meta), "slug": slug})
                    if r.rowcount: inserted += 1
                except Exception as e:
                    logger.debug("brain kpi insert skip (%s): %s", name, e)
        return _step("brain", True, details=f"kpis+={inserted}")
    except Exception as e:
        return _step("brain", False, error=str(e))


def _apply_rules(slug: str, generated: dict) -> dict:
    eng = _engine()
    if eng is None:
        return _step("rules", False, error="no db engine")
    inserted = 0
    try:
        with eng.begin() as conn:
            for rule in (generated.get("business_rules") or []):
                name = (rule.get("name") or "").strip()
                cond = (rule.get("condition") or "").strip()
                sev = (rule.get("severity") or "info").strip().lower()
                if not name or not cond:
                    continue
                try:
                    r = conn.execute(text(
                        "INSERT INTO public.dash_business_rules_db "
                        "(project_slug, name, condition, severity, source, created_at) "
                        "VALUES (:slug, :name, :cond, :sev, 'template_generator', now()) "
                        "ON CONFLICT DO NOTHING"
                    ), {"slug": slug, "name": name, "cond": cond, "sev": sev})
                    if r.rowcount: inserted += 1
                except Exception as e:
                    logger.debug("rule insert skip (%s): %s", name, e)
        return _step("rules", True, details=f"rules+={inserted}")
    except Exception as e:
        return _step("rules", False, error=str(e))


def _apply_workflows(slug: str, generated: dict) -> dict:
    eng = _engine()
    if eng is None:
        return _step("workflows", False, error="no db engine")
    inserted = 0
    try:
        with eng.begin() as conn:
            for wf in (generated.get("workflow_scaffolds") or []):
                name = (wf.get("name") or "").strip()
                if not name:
                    continue
                try:
                    conn.execute(text(
                        "INSERT INTO dash_autonomous_workflows "
                        "(project_slug, template_name, name, description, schedule, "
                        " query_template, expected_entity, expected_columns, action, status) "
                        "VALUES (:slug, 'generated', :name, :desc, :sched, :qt, '', "
                        " CAST(:ec AS jsonb), :act, 'pending') "
                        "ON CONFLICT DO NOTHING"
                    ), {
                        "slug": slug, "name": name,
                        "desc": wf.get("description") or "",
                        "sched": wf.get("trigger") or wf.get("schedule") or "",
                        "qt": wf.get("sql") or wf.get("query_template") or "",
                        "ec": json.dumps([]),
                        "act": wf.get("action") or "log",
                    })
                    inserted += 1
                except Exception as e:
                    logger.debug("workflow insert skip (%s): %s", name, e)
        return _step("workflows", True, details=f"workflows+={inserted}")
    except Exception as e:
        return _step("workflows", False, error=str(e))


def apply_generated_template(slug: str, generated: dict) -> dict:
    """Atomically apply a generated template. Snapshot first; on majority
    step failure, revert."""
    if not slug or not isinstance(generated, dict):
        return {"ok": False, "error": "missing slug or generated payload", "steps": []}

    snap = _snapshot(slug)
    steps: list[dict] = [_step("snapshot", True, details=f"brain_max={snap.get('brain_max_id')}")]

    steps.append(_apply_feature_config(slug, generated))
    steps.append(_apply_brain(slug, generated))
    steps.append(_apply_rules(slug, generated))
    steps.append(_apply_workflows(slug, generated))

    failed = sum(1 for s in steps if not s.get("ok"))
    total = len(steps)
    ok = failed * 2 <= total  # majority succeeded

    if not ok:
        logger.warning("apply_generated_template: %d/%d failed, reverting slug=%s", failed, total, slug)
        _revert(slug, snap)
        return {"ok": False, "reverted": True, "steps": steps,
                "errors": [s.get("error") for s in steps if s.get("error")]}

    return {"ok": True, "steps": steps,
            "errors": [s.get("error") for s in steps if s.get("error")]}
