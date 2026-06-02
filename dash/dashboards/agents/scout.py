"""Scout — SQL + analysis specialist. Discovers findings via SQL exploration + detectors.

Calls LLM as expert analyst. Runs SQL via project read-only engine.
"""
from __future__ import annotations
import logging, json, time, re
from typing import Any
from .contracts import Finding
from . import packs

logger = logging.getLogger(__name__)

async def discover(project_slug: str, prompt: str, chat_context: dict | None,
                   persona: str = "", max_findings: int = 12) -> list[Finding]:
    """Survey project, run domain must-have SQLs + detectors, emit Finding[]."""
    pack = packs.detect_pack(project_slug, persona)
    real_tables = _real_tables(project_slug)

    # Phase G — bias toward findings users have kept before (non-critical)
    retained_hint = ""
    try:
        from . import memory_loop
        retained = memory_loop.get_retained_findings(project_slug, top_n=5)
        if retained:
            retained_hint = "Past findings users kept: " + " | ".join(
                r.get("headline", "")[:120] for r in retained
            ) + " — surface similar patterns when relevant."
    except Exception as e:
        logger.debug(f"retained-hint load failed: {e}")
    if retained_hint:
        chat_context = dict(chat_context or {})
        prior = chat_context.get("instructions") or ""
        chat_context["instructions"] = (prior + "\n" + retained_hint).strip()

    findings: list[Finding] = []

    # Step 1: domain must-have SQLs from pack
    for sql_template in pack.get("must_run_sqls", []):
        sql = _instantiate_sql(sql_template, real_tables)
        if not sql: continue
        net = _is_network_query(sql=sql, prompt=prompt)
        df = _run_sql(project_slug, sql, intent="network" if net else "private")
        if df is None or df.empty: continue
        f = _df_to_finding(sql, df, sql_template.get("headline_template",""),
                           tags=sql_template.get("tags",[]),
                           severity=sql_template.get("severity","medium"),
                           prompt=prompt)
        findings.append(f)
        if len(findings) >= max_findings: return findings

    # Step 2: LLM-driven exploration. Ask expert analyst what to query.
    expert_findings = await _llm_explore(project_slug, prompt, chat_context, persona, real_tables, pack, max_findings - len(findings))
    findings.extend(expert_findings)

    # Step 3: Run rule-based detectors on discovered data
    detector_findings = _run_detectors(findings, pack)
    findings.extend(detector_findings)

    final = findings[:max_findings]
    _surface_all(project_slug, final)
    return final


async def drill(project_slug: str, finding: Finding, persona: str = "") -> list[Finding]:
    """Given finding, run 3-5 sub-SQLs to drill cause."""
    pack = packs.detect_pack(project_slug, persona)
    real_tables = _real_tables(project_slug)

    prompt = f"""You are a senior {pack.get('domain','data')} analyst drilling into a finding.

PARENT FINDING:
- headline: {finding.headline}
- evidence_sql: {finding.sql}
- data sample: {json.dumps(finding.data[:3], default=str)[:500]}

REAL TABLES (use only these):
{_format_tables(real_tables)}

Output JSON array of 3-5 sub-investigations. Each:
{{"sql": "SELECT ...", "headline": "what we'll learn", "tags": [...], "severity": "high|medium|low"}}

CRITICAL RULES:
- SELECT only, schema-qualified table names
- Each SQL must reveal CAUSE/segment/timing of parent finding
- Return ONLY a JSON array. No fences, no preamble.
"""
    raw = _cheap_llm(prompt)
    plans = _parse_json_array(raw)

    findings: list[Finding] = []
    for p in plans:
        sql = p.get("sql","").strip()
        if not sql: continue
        net = _is_network_query(sql=sql)
        df = _run_sql(project_slug, sql, intent="network" if net else "private")
        if df is None or df.empty: continue
        extra_tags = p.get("tags",[]) + ["drill"]
        if net and "network" not in extra_tags:
            extra_tags.append("network")
        f = Finding(
            id=f"f_{int(time.time()*1000)}_{len(findings)}",
            headline=p.get("headline","")[:200],
            severity=p.get("severity","medium"),
            sql=sql,
            data=df.head(20).to_dict(orient="records"),
            domain_tags=extra_tags,
            sql_shape={"n_cols": len(df.columns), "n_rows": len(df)},
        )
        findings.append(f)
    _surface_all(project_slug, findings)
    return findings


async def critique(findings: list[Finding], persona: str = "", prompt: str = "") -> list[Finding]:
    """Identify 3-5 gaps. Return as 'wishlist' findings (sql to be filled)."""
    pack_domain = "generic"
    must_haves = []
    findings_summary = "\n".join([f"- {f.headline}" for f in findings[:15]])

    review_prompt = f"""You are a senior {pack_domain} analyst reviewing a dashboard.

USER QUESTION: {prompt[:300]}

CURRENT FINDINGS:
{findings_summary}

Identify 3-5 IMPORTANT gaps a senior analyst would notice. For each gap, write:
- headline: short statement of what's missing
- proposed_sql: SQL to fill the gap (or empty if exploratory)
- severity: high|medium|low

Output JSON array. No fences.
"""
    raw = _cheap_llm(review_prompt)
    plans = _parse_json_array(raw)

    gap_findings: list[Finding] = []
    for p in plans[:5]:
        gap_findings.append(Finding(
            id=f"gap_{int(time.time()*1000)}_{len(gap_findings)}",
            headline=p.get("headline","")[:200],
            severity=p.get("severity","medium"),
            sql=p.get("proposed_sql","").strip(),
            domain_tags=["gap"],
        ))
    return gap_findings


# ───── helpers ─────

def _surface_all(slug: str, findings: list[Finding]) -> None:
    # Phase G — record_surface + attach finding_hash for orchestrator/frontend correlation.
    try:
        from . import memory_loop
    except Exception:
        return
    for f in findings:
        try:
            h = memory_loop.record_surface(slug, f)
            if h:
                # Attach hash on the model so downstream can read it without rehashing.
                try:
                    object.__setattr__(f, "finding_hash", h)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"surface failed: {e}")

def _real_tables(slug: str) -> list[dict]:
    """Reuse from planner if available, else inline."""
    try:
        from dash.dashboards.planner import _real_tables as f
        return f(slug)
    except Exception:
        return []

def _format_tables(tables: list[dict]) -> str:
    out = []
    for t in tables[:15]:
        cols = ", ".join([f"{c[0]}:{c[1]}" for c in t.get("cols",[])[:8]])
        out.append(f'- {t.get("qualified", t.get("name",""))} cols=[{cols}]')
    return "\n".join(out)

_NETWORK_SQL_RE = re.compile(r'group\s+by\s+(scope_id|store_id|store_code|branch_id|site_id|location_id)', re.I)
# WHY: regex tolerates filler words ("all", "the", "every") between trigger phrase parts
_NETWORK_PROMPT_RE = re.compile(
    r'\b(?:across|per|by|each|every|which|where).{0,20}\b(stores?|branches?|sites?|outlets?|locations?|scopes?)\b',
    re.I
)
_NETWORK_PROMPT_HINTS = ("network", "cross-store", "cross store", "all stores", "all branches", "matrix")


def _is_network_query(sql: str = "", prompt: str = "") -> bool:
    if sql and _NETWORK_SQL_RE.search(sql):
        return True
    p = (prompt or "")
    if _NETWORK_PROMPT_RE.search(p):
        return True
    pl = p.lower()
    return any(h in pl for h in _NETWORK_PROMPT_HINTS)


def _run_sql(slug: str, sql: str, intent: str = "private"):
    # WHY: wrap exec in intent ContextVar so policy engine downstream can rewrite
    prev_intent = None
    try:
        from dash.tools.skill_refinery import get_query_intent, set_request_context
        prev_intent = get_query_intent()
        if intent and intent != prev_intent:
            set_request_context(query_intent=intent)
    except Exception:
        pass
    try:
        from db.session import get_project_readonly_engine
        from sqlalchemy import text
        import pandas as pd
        if not sql.strip().upper().startswith(("SELECT","WITH")): return None
        eng = get_project_readonly_engine(slug)
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:
        logger.debug(f"scout sql failed: {e}")
        return None
    finally:
        try:
            if prev_intent is not None:
                from dash.tools.skill_refinery import set_request_context
                set_request_context(query_intent=prev_intent)
        except Exception:
            pass

def _instantiate_sql(template: dict, real_tables: list[dict]) -> str | None:
    """Replace {table} placeholders in template SQL with real table names."""
    sql = template.get("sql","")
    needs = template.get("needs_table_with_cols", [])
    if needs:
        # Find a table that has all needed columns
        for t in real_tables:
            tcols = set(c[0].lower() for c in t.get("cols", []))
            if all(c.lower() in tcols for c in needs):
                qualified = t.get("qualified", t.get("name",""))
                sql = sql.replace("{table}", qualified)
                return sql
        return None  # no matching table
    return sql

def _df_to_finding(sql: str, df, headline_template: str, tags=None, severity="medium", prompt: str = "") -> Finding:
    headline = headline_template
    if df is not None and not df.empty:
        try:
            headline = headline_template.format(value=df.iloc[0,0], count=len(df))
        except Exception: pass
    final_tags = list(tags or [])
    if _is_network_query(sql=sql, prompt=prompt) and "network" not in final_tags:
        final_tags.append("network")
    headline_out = headline[:200] if headline else f"data: {len(df)} rows"
    cause = ""
    action = ""
    # WHY: sanitize narrative when active intent is non-private
    try:
        from dash.tools.skill_refinery import get_query_intent
        from .text_guard import sanitize_narrative
        intent = get_query_intent()
        if intent != "private":
            headline_out = sanitize_narrative(headline_out, "", intent)
    except Exception:
        pass
    return Finding(
        id=f"f_{int(time.time()*1000)}",
        headline=headline_out,
        severity=severity,
        sql=sql,
        data=df.head(20).to_dict(orient="records") if df is not None else [],
        cause_hypothesis=cause,
        suggested_action=action,
        domain_tags=final_tags,
        sql_shape={"n_cols": len(df.columns) if df is not None else 0,
                   "n_rows": len(df) if df is not None else 0},
    )

def _cheap_llm(prompt: str) -> str:
    try:
        from dash.settings import training_llm_call
        return training_llm_call(prompt, task="extraction") or ""
    except Exception as e:
        logger.warning(f"llm failed: {e}")
        return ""

def _parse_json_array(text: str) -> list[dict]:
    if not text: return []
    text = text.strip()
    # strip fences
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text).rstrip("`").strip()
    # extract array
    m = re.search(r'\[[\s\S]*\]', text)
    if m: text = m.group(0)
    try: return json.loads(text)
    except Exception:
        # repair common issues
        try:
            fixed = re.sub(r',(\s*[}\]])', r'\1', text)
            return json.loads(fixed)
        except Exception: return []

async def _llm_explore(project_slug, prompt, chat_context, persona, real_tables, pack, n_max):
    if n_max <= 0: return []
    cc = chat_context or {}
    questions = (cc.get("questions") or [])[:10]

    plan_prompt = f"""You are a senior {pack.get('domain','data')} analyst exploring a project's data.

USER PROMPT: {prompt[:300]}
USER QUESTIONS HISTORY: {json.dumps(questions[:5])}
PERSONA: {persona[:200]}

DOMAIN EXPERTISE: {pack.get('expert_prompt','')[:600]}

REAL TABLES:
{_format_tables(real_tables)}

Plan {n_max} high-impact analysis SQLs. Each:
{{"sql": "SELECT ...", "headline": "what we discover", "tags":[...], "severity": "high|medium|low"}}

Use only listed tables. SELECT only. Schema-qualified names. JSON array, no fences.
"""
    raw = _cheap_llm(plan_prompt)
    plans = _parse_json_array(raw)
    findings = []
    for p in plans[:n_max]:
        sql = p.get("sql","").strip()
        if not sql: continue
        net = _is_network_query(sql=sql, prompt=prompt)
        df = _run_sql(project_slug, sql, intent="network" if net else "private")
        if df is None or df.empty: continue
        findings.append(_df_to_finding(sql, df, p.get("headline","")[:200],
                                        tags=p.get("tags",[]),
                                        severity=p.get("severity","medium"),
                                        prompt=prompt))
    return findings

def _run_detectors(findings: list[Finding], pack: dict) -> list[Finding]:
    """Apply pack detectors to existing findings' data."""
    extra = []
    detectors = pack.get("detectors", [])
    for det in detectors:
        try:
            for f in findings[:8]:
                # detector signature: (df, context) -> list[dict]
                import pandas as pd
                df = pd.DataFrame(f.data) if f.data else None
                if df is None or df.empty: continue
                results = det(df, {"sql": f.sql, "headline": f.headline}) or []
                for r in results:
                    extra.append(Finding(
                        id=f"d_{int(time.time()*1000)}_{len(extra)}",
                        headline=r.get("finding","")[:200],
                        severity=r.get("severity","medium"),
                        sql=f.sql,
                        data=f.data[:5],
                        cause_hypothesis=r.get("cause",""),
                        suggested_action=r.get("action",""),
                        domain_tags=["detector"],
                    ))
        except Exception as e:
            logger.debug(f"detector failed: {e}")
    return extra
