"""Leader-driven answer-cache curation. [P3]

The "leader agent decides what to cache" loop. It pulls the project's most
FREQUENT questions (question clusters), asks an LLM acting as the lead
pharmacy-data analyst to judge which questions are STABLE + SAFE to pre-cache,
generates ONE canonical read-only SQL for each keeper, verifies that SQL returns
real data, renders a compact AnswerCard string, and promotes it into the answer
cache (`dash.dash_answer_cache`, via `promote_answer`, `promoted_by='leader'`).

It must NEVER cache volatile / personal / time-relative questions — the leader
judge refuses those (`cacheable=false`) and they are skipped. The cached answer
is the rendered card produced from a verified result, so the numbers were
correct at promote time; the schema-drift guard on the serve path
(`answer_cache.try_answer_cache`) retires a card if the source schema moves.

Fail-soft everywhere: a single bad cluster never aborts the loop (it lands in
`skipped` with a reason); any error → safe no-op, never raises.

`cluster_questions` (dash/learning/question_clusters) is imported LAZILY inside
each function so a load-order issue (it is authored in parallel) can never crash
this module's import.
"""
from __future__ import annotations

import json as _json
import logging
import re

logger = logging.getLogger(__name__)

# Work cap — judge/verify at most this many clusters per run (cost guard).
_MAX_CLUSTERS = 25

# Anything matching these = a write / DDL → reject (read-only gate).
_FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT|TRUNCATE|MERGE|REPLACE|COPY)\b",
    re.I,
)


def _norm(s: str) -> str:
    """Same normalization as answer_cache._norm (kept local to avoid a hard dep)."""
    return re.sub(r"\s+", " ", (s or "").lower().strip().rstrip(".?!"))


def _is_cached(project_slug: str, question: str) -> bool:
    """True if a LIVE answer-cache row already exists for this question_norm."""
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        norm = _norm(question)
        if not norm:
            return False
        with get_sql_engine().connect() as conn:
            r = conn.execute(_text(
                "SELECT 1 FROM dash.dash_answer_cache "
                "WHERE project_slug = :s AND question_norm = :n AND status = 'live' "
                "LIMIT 1"
            ), {"s": project_slug, "n": norm}).fetchone()
        return bool(r)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator._is_cached failed for %s: %s", project_slug, exc)
        return False


def _is_read_only(sql: str) -> bool:
    """Read-only gate: must START with SELECT or WITH, contain no DML/DDL keyword,
    and no second statement after a `;`."""
    if not sql:
        return False
    s = sql.strip().rstrip(";").strip()
    if not s:
        return False
    head = s.upper().lstrip("(").lstrip()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return False
    if ";" in s:                       # a `;` mid-statement = stacked statements
        return False
    if _FORBIDDEN_SQL.search(s):
        return False
    return True


def _parse_judge(raw: str) -> dict:
    """Fail-soft parse of the leader's strict-JSON judgment.

    Mirrors the ```json-fence + first-`{`..last-`}` extraction used in
    app/projects.py (metric_shortcut enrich block).
    """
    out = {"cacheable": False, "reason": "", "canonical_sql": ""}
    try:
        s = (raw or "").strip()
        if s.startswith("```"):
            s = s.split("```")[1] if "```" in s[3:] else s[3:]
            if s.startswith("json"):
                s = s[4:]
        a, b = s.find("{"), s.rfind("}")
        if a < 0 or b <= a:
            return out
        obj = _json.loads(s[a:b + 1])
        if not isinstance(obj, dict):
            return out
        out["cacheable"] = bool(obj.get("cacheable"))
        out["reason"] = str(obj.get("reason") or "").strip()[:300]
        out["canonical_sql"] = str(obj.get("canonical_sql") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator._parse_judge failed: %s", exc)
    return out


def _schema_context(project_slug: str) -> str:
    """Compact real-schema listing the leader must write SQL against, so it
    never invents table names. Returns 'table(col, col, ...)' lines (capped).

    Fail-soft: "" on any error (judge then falls back to question-only — which
    tends to hallucinate tables, so we prefer a real listing whenever possible).
    """
    try:
        from sqlalchemy import text as _text
        from dash.tools.metric_compiler import resolve_engine
        engine, schema = resolve_engine(project_slug)
        with engine.connect() as conn:
            rows = conn.execute(_text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = :sch ORDER BY table_name, ordinal_position"
            ), {"sch": schema}).fetchall()
        if not rows:
            return ""
        by_tbl: dict[str, list[str]] = {}
        for tname, cname in rows:
            by_tbl.setdefault(str(tname), []).append(str(cname))
        lines = []
        for tname in sorted(by_tbl):
            cols = by_tbl[tname][:24]
            lines.append(f"{tname}({', '.join(cols)})")
        return "\n".join(lines)[:3000]
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator._schema_context failed for %s: %s", project_slug, exc)
        return ""


def _judge_cluster(project_slug: str, question: str) -> dict:
    """Ask the LLM (as lead pharmacy-data analyst) whether `question` is
    STABLE + SAFE to pre-cache, and to produce ONE read-only canonical SQL.

    Returns {"cacheable": bool, "reason": str, "canonical_sql": str}. Fail-soft.
    """
    try:
        from dash.settings import training_llm_call
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator: training_llm_call import failed: %s", exc)
        return {"cacheable": False, "reason": "llm unavailable", "canonical_sql": ""}

    schema_ctx = _schema_context(project_slug)
    prompt = (
        "You are the LEAD PHARMACY-DATA ANALYST deciding what to pre-cache for a "
        "pharmacy analytics product. A pre-cached answer is served VERBATIM, with "
        "NO live model and NO re-run, until the underlying data is reloaded.\n\n"
        "Decide if this question's answer is STABLE and SAFE to pre-cache.\n"
        "STABLE = the answer only changes when the catalog/stock data is reloaded: "
        "counts, totals, distinct counts, category/segment splits, catalog facts, "
        "averages over the whole dataset.\n"
        "You MUST set cacheable=false for anything that is:\n"
        "  - time-relative ('today', 'now', 'this week', 'latest', 'recent', "
        "'yesterday', 'currently', 'right now'),\n"
        "  - personal / per-asker / per-customer ('my', 'for me', 'this patient'),\n"
        "  - dependent on who is asking or on the current moment,\n"
        "  - advice / clinical recommendation / a greeting / chit-chat.\n\n"
        "If (and only if) cacheable, produce ONE read-only SQL that answers it "
        "against this project's pharmacy data. The SQL MUST be a single statement "
        "starting with SELECT or WITH (no INSERT/UPDATE/DELETE/DDL, no semicolons, "
        "no parameters). Aggregate in SQL (COUNT/SUM/COUNT(DISTINCT)/GROUP BY) — "
        "never imply summing rows in your head.\n\n"
        "You MUST write the SQL against ONLY the real tables and columns listed "
        "below — never invent table or column names. Use bare table names (no "
        "schema prefix). If the question CANNOT be answered from these tables, set "
        "cacheable=false with reason 'not answerable from schema'.\n\n"
        f"AVAILABLE TABLES (table(columns…)):\n{schema_ctx or '(schema unavailable)'}\n\n"
        "Return STRICT JSON ONLY, no prose:\n"
        '{"cacheable": true|false, "reason": "<short reason>", '
        '"canonical_sql": "<single read-only SELECT/WITH, or empty if not cacheable>"}\n\n'
        f"Question: {question[:400]}\n"
    )
    try:
        raw = training_llm_call(prompt, "extraction") or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator: judge llm call failed for %s: %s", project_slug, exc)
        return {"cacheable": False, "reason": "llm error", "canonical_sql": ""}
    return _parse_judge(raw)


def _fmt_value(v) -> str:
    """Compact human number: 1234567 -> '1,234,567'; floats trimmed."""
    try:
        f = float(v)
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except Exception:
        return str(v)


def _build_card(question: str, value, rows: list, cols: list,
                sql: str, tables: list[str], row_count: int | None = None) -> str:
    """Render a compact, valid AnswerCard tag-string from a verified result.

    Tag vocabulary (matches frontend/src/lib/chat/AnswerCard.svelte +
    dash/instructions.py KPI examples):
      [ACTION_TITLE: <takeaway>]       — one sentence
      [KPI: <value>|<label> 🟢|—|<sublabel>]   — headline number (status emoji
                                                  at END of label)
      [KPI: <rows>|Rows returned|—|matching records]  — optional row-count KPI
      [CONFIDENCE:HIGH]
      [FRESHNESS:<table>|NULL]         — one per source table
      <short markdown sentence>

    HEADLINE RULE: `value` = first numeric cell of the FIRST row (verified_reward
    ._run_rows). That is the right headline only for a SINGLE-ROW scalar answer
    ("how many SKUs" → 4892). For a MULTI-ROW breakdown (GROUP BY: categories +
    count) the first-row scalar is just the TOP group's number, NOT the answer —
    the meaningful headline is the ROW COUNT (101 categories). So multi-row
    results headline the count and demote the top-row scalar to a sublabel.
    `row_count` is the TRUE total (rows[] is capped at the serve limit, so
    len(rows) under-reports — always pass the real count through).
    """
    label = (cols[0] if cols else "Result")
    label = re.sub(r"[_\s]+", " ", str(label)).strip().title()[:48] or "Result"
    rc = int(row_count if row_count is not None else len(rows or []))
    q_short = (question or "").strip().rstrip("?.!")[:90]

    parts: list[str] = []
    if rc > 1:
        # Multi-row breakdown → headline the COUNT, not the first scalar.
        group = re.sub(r"[_\s]+", " ", str(cols[0] if cols else "row")).strip().lower() or "row"
        if group.endswith("s"):
            group_plural = group
        elif re.search(r"[^aeiou]y$", group):   # category -> categories
            group_plural = group[:-1] + "ies"
        else:
            group_plural = group + "s"
        rc_disp = _fmt_value(rc)
        parts.append(f"[ACTION_TITLE: {rc_disp} {group_plural} — {q_short}]")
        parts.append(f"[KPI: {rc_disp}|{group_plural.title()} 🟢|—|distinct rows returned]")
        if value is not None:
            parts.append(f"[KPI: {_fmt_value(value)}|Top {group} 🟢|—|highest value]")
        parts.append("[CONFIDENCE:HIGH]")
        for t in (tables or []):
            if t:
                parts.append(f"[FRESHNESS:{t}|NULL]")
        parts.append(
            f"The verified answer is a breakdown of **{rc_disp} {group_plural}** "
            f"from the current pharmacy data (see the data table for every row)."
        )
        return "\n".join(parts)

    # Single-row scalar answer → the first numeric cell IS the answer.
    val = _fmt_value(value)
    parts.append(f"[ACTION_TITLE: {val} — {q_short}]")
    parts.append(f"[KPI: {val}|{label} 🟢|—|verified result]")
    parts.append("[CONFIDENCE:HIGH]")
    for t in (tables or []):
        if t:
            parts.append(f"[FRESHNESS:{t}|NULL]")
    parts.append(
        f"The verified answer is **{val}** ({label.lower()}). "
        f"Computed from {rc} row(s) of the current pharmacy data."
    )
    return "\n".join(parts)


async def curate_one(project_slug: str, question: str, *, dry_run: bool = False) -> dict:
    """Judge → verify → (promote) ONE question. Reused by run_curator's loop and
    by the per-row 'Cache this' endpoint.

    Returns {"question", "outcome": "promoted"|"candidate"|"skipped",
             "reason", "sql", "value", "id", "source_tables"}. Fail-soft.
    """
    from dash.learning.schema_guard import sql_source_tables
    rep = (question or "").strip()
    out = {"question": rep, "outcome": "skipped", "reason": "", "sql": "",
           "value": None, "id": None, "source_tables": []}
    if not rep:
        out["reason"] = "empty question"
        return out
    try:
        if _is_cached(project_slug, rep):
            out["reason"] = "already cached"
            return out

        judgment = _judge_cluster(project_slug, rep)
        if not judgment.get("cacheable"):
            out["reason"] = judgment.get("reason") or "leader: not cacheable"
            return out

        sql = (judgment.get("canonical_sql") or "").strip()
        if not _is_read_only(sql):
            out["reason"] = "sql not read-only / unsafe"
            return out
        out["sql"] = sql

        from dash.learning import verified_reward as _vr
        run = _vr._run_rows(project_slug, sql, limit=20)
        if not run or run.get("value") is None:
            out["reason"] = "no data"
            return out

        value = run.get("value")
        rows = run.get("rows") or []
        cols = run.get("columns") or []
        tables = sql_source_tables(sql)
        out["value"] = value
        out["source_tables"] = tables

        if dry_run:
            out["outcome"] = "candidate"
            out["reason"] = "would promote"
            return out

        card = _build_card(rep, value, rows, cols, sql, tables,
                           row_count=run.get("row_count"))
        from dash.learning.answer_cache import promote_answer
        promo = await promote_answer(
            project_slug, question=rep, content=card, canonical_sql=sql,
            source_tables=tables, confidence=0.9, promoted_by="leader",
        )
        if promo.get("ok"):
            out["outcome"] = "promoted"
            out["id"] = promo.get("id")
            out["reason"] = "promoted"
        else:
            out["reason"] = f"promote failed: {promo.get('error')}"
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"error: {exc}"
        return out


def list_cached(project_slug: str, *, limit: int = 200) -> list[dict]:
    """List cache rows (newest first) with a live schema-freshness flag.
    Read-only. Fail-soft → []."""
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        from dash.learning.schema_guard import live_schema_hash
        with get_sql_engine().connect() as conn:
            rows = conn.execute(_text(
                "SELECT id, question, hit_count, status, source_tables, schema_hash, "
                "       promoted_by, confidence, created_at, last_served_at "
                "FROM dash.dash_answer_cache WHERE project_slug = :s "
                "AND status <> 'demoted' "
                "ORDER BY created_at DESC LIMIT :l"
            ), {"s": project_slug, "l": int(limit)}).fetchall()
        out = []
        for r in rows:
            tables = list(r[4] or [])
            stored = r[5]
            fresh = True
            if stored:
                try:
                    live = live_schema_hash(project_slug, tables)
                    fresh = (not live) or (live == stored)
                except Exception:
                    fresh = True
            from dash.privacy import redact as _r, keywords as _kw
            out.append({
                # privacy: dashboards show keyword chips, never the raw question
                "id": r[0], "question": _r(r[1]), "keywords": _kw(r[1]),
                "hit_count": int(r[2] or 0),
                "status": r[3], "source_tables": tables,
                "promoted_by": r[6], "confidence": float(r[7] or 0),
                "created_at": r[8].isoformat() + "Z" if r[8] else None,
                "last_served_at": r[9].isoformat() + "Z" if r[9] else None,
                "schema_fresh": fresh,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator.list_cached failed for %s: %s", project_slug, exc)
        return []


async def run_curator(
    project_slug: str,
    *,
    dry_run: bool = False,
    max_promote: int = 10,
    days: int = 30,
    min_count: int = 3,
) -> dict:
    """Leader-driven cache curation. Returns
       {"candidates": [...], "promoted": [...], "skipped": [...], "dry_run": bool}.
    """
    result = {"candidates": [], "promoted": [], "skipped": [], "dry_run": bool(dry_run)}
    try:
        from dash.learning.question_clusters import cluster_questions
        from dash.learning.schema_guard import sql_source_tables
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_curator: dependency import failed for %s: %s", project_slug, exc)
        return result

    try:
        clusters = cluster_questions(project_slug, days=days, min_count=min_count, limit=50)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache_curator: cluster_questions failed for %s: %s", project_slug, exc)
        return result

    if not clusters:
        logger.info("cache_curator: no clusters for %s (days=%d min_count=%d)",
                    project_slug, days, min_count)
        return result

    promoted_n = 0
    for cluster in clusters[:_MAX_CLUSTERS]:
        try:
            rep = ((cluster or {}).get("representative") or "").strip()
            if not rep:
                continue
            count = int((cluster or {}).get("count") or 0)

            one = await curate_one(project_slug, rep, dry_run=dry_run)
            outcome = one.get("outcome")
            if outcome == "candidate":
                result["candidates"].append({
                    "question": rep, "count": count, "sql": one.get("sql"),
                    "value": one.get("value"), "would_promote": True,
                    "source_tables": one.get("source_tables") or [],
                })
            elif outcome == "promoted":
                result["promoted"].append({
                    "question": rep, "count": count, "sql": one.get("sql"),
                    "value": one.get("value"), "id": one.get("id"),
                })
                promoted_n += 1
                if promoted_n >= max_promote:
                    break
            else:
                result["skipped"].append({
                    "question": rep, "count": count,
                    "reason": one.get("reason") or "skipped",
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("cache_curator: cluster failed for %s: %s", project_slug, exc)
            try:
                result["skipped"].append({
                    "question": (cluster or {}).get("representative") or "",
                    "reason": f"error: {exc}",
                })
            except Exception:
                pass
            continue

    logger.info(
        "cache_curator %s: clusters=%d candidates=%d promoted=%d skipped=%d dry_run=%s",
        project_slug, len(clusters), len(result["candidates"]),
        len(result["promoted"]), len(result["skipped"]), dry_run,
    )
    return result


def curator_stats(project_slug: str) -> dict:
    """Read-only cache inspection: counts by status + promoted_by, total hits."""
    out = {
        "by_status": {},
        "by_promoted_by": {},
        "total_hit_count": 0,
        "total": 0,
    }
    try:
        from sqlalchemy import text as _text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            for status, n in conn.execute(_text(
                "SELECT status, COUNT(*) FROM dash.dash_answer_cache "
                "WHERE project_slug = :s GROUP BY status"
            ), {"s": project_slug}).fetchall():
                out["by_status"][str(status)] = int(n)
            for by, n in conn.execute(_text(
                "SELECT promoted_by, COUNT(*) FROM dash.dash_answer_cache "
                "WHERE project_slug = :s GROUP BY promoted_by"
            ), {"s": project_slug}).fetchall():
                out["by_promoted_by"][str(by)] = int(n)
            row = conn.execute(_text(
                "SELECT COALESCE(SUM(hit_count), 0), COUNT(*) "
                "FROM dash.dash_answer_cache WHERE project_slug = :s"
            ), {"s": project_slug}).fetchone()
        if row:
            out["total_hit_count"] = int(row[0] or 0)
            out["total"] = int(row[1] or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache_curator.curator_stats failed for %s: %s", project_slug, exc)
    return out
