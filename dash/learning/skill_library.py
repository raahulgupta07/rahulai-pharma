"""Skill Library — Voyager-style promotion + HippoRAG-style retrieval.

Promotes proven SQL patterns (used ≥3 times w/ judge score ≥4) from
dash_query_patterns into dash_skill_library as parameterized recipes with
NL descriptions and embeddings. Retrieval is cosine-similarity over
description embeddings — used at chat-time to suggest reusable SQL.

Public surface:
    promote_proven_patterns(project_slug, run_id) -> dict
    retrieve_skill_hints(project_slug, question, top_k=5) -> list[dict]
    record_skill_use(skill_id, succeeded, judge_score) -> None
    embed_pending_skills(project_slug) -> int

All functions are sync, never raise, log + return error dicts on failure.
Hard cap: 20 promotions per run.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from dash.settings import training_llm_call

logger = logging.getLogger(__name__)


# ── Hard caps ──────────────────────────────────────────────────────────────
_MAX_PROMOTIONS_PER_RUN = 20
_MIN_USAGE = 3
_MIN_JUDGE_SCORE = 4.0
_RETRIEVE_THRESHOLD = 0.6


def _engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Robust JSON parser (4-tier) ────────────────────────────────────────────
def _safe_parse_json(raw: str) -> Optional[Any]:
    if not raw:
        return None
    cleaned = raw.strip().strip("`").strip()
    if cleaned.lower().startswith("json"):
        cleaned = cleaned[4:].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    for opener, closer in [("{", "}"), ("[", "]")]:
        i = cleaned.find(opener)
        j = cleaned.rfind(closer)
        if i >= 0 and j > i:
            chunk = cleaned[i : j + 1]
            try:
                return json.loads(chunk)
            except Exception:
                continue
    fixed = re.sub(r",(\s*[\}\]])", r"\1", cleaned)
    try:
        return json.loads(fixed)
    except Exception:
        return None


# ── Embedding sync wrapper ─────────────────────────────────────────────────
def _embed_sync(text_val: str) -> Optional[List[float]]:
    try:
        from dash.tools.embeddings_helper import embed_text
        try:
            asyncio.get_running_loop()
            return None  # inside a loop, skip
        except RuntimeError:
            return asyncio.run(embed_text(text_val or ""))
    except Exception:
        logger.debug("skill_library: embed failed", exc_info=True)
        return None


def _vec_to_text(vec: List[float]) -> str:
    """Serialize vec to a compact string for the TEXT description_embedding column."""
    return json.dumps([float(x) for x in vec])


def _text_to_vec(s: Optional[str]) -> Optional[List[float]]:
    if not s:
        return None
    try:
        v = json.loads(s)
        if isinstance(v, list) and v:
            return [float(x) for x in v]
    except Exception:
        return None
    return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── SQL parameterization (lightweight regex) ───────────────────────────────
_DATE_RX = re.compile(r"'(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?'")
_NUM_RX = re.compile(r"(?<![\w\.])(\d+(?:\.\d+)?)(?![\w\.])")
_STR_RX = re.compile(r"'([^']{1,80})'")


def _parameterize_sql(sql: str) -> tuple[str, Dict[str, Dict[str, Any]]]:
    """Replace literals with {param} placeholders. Returns (sql, params_schema).

    Conservative: only one placeholder per kind to keep it simple in v1.
    """
    schema: Dict[str, Dict[str, Any]] = {}
    out = sql

    # Dates first (most specific)
    dates = _DATE_RX.findall(out)
    if dates:
        out = _DATE_RX.sub("{date}", out, count=1)
        schema["date"] = {
            "type": "date",
            "required": False,
            "default": dates[0],
        }

    # String literals (skip if too long — likely not a param)
    strings = _STR_RX.findall(out)
    if strings:
        # Replace only the first reasonable string literal
        for s in strings:
            if 1 <= len(s) <= 60 and not _DATE_RX.search(f"'{s}'"):
                out = out.replace(f"'{s}'", "{value}", 1)
                schema["value"] = {
                    "type": "string",
                    "required": False,
                    "default": s,
                }
                break

    # Numbers (only if not already a placeholder context)
    nums = _NUM_RX.findall(out)
    if nums:
        # Replace first numeric literal that isn't tiny (skip 0, 1)
        for n in nums:
            try:
                if float(n) >= 2:
                    out = re.sub(rf"(?<![\w\.])({re.escape(n)})(?![\w\.])",
                                 "{N}", out, count=1)
                    schema["N"] = {
                        "type": "number",
                        "required": False,
                        "default": n,
                    }
                    break
            except ValueError:
                continue

    return out, schema


# ── Description generation (LITE_MODEL) ────────────────────────────────────
_DESC_PROMPT = """Write a one-sentence natural-language description of what this SQL pattern answers.
Be concrete but generic (no project-specific values). 15-25 words.

QUESTION (original): {question}
SQL: {sql}

Output ONLY the sentence, no quotes, no preamble.
"""


def _generate_description(question: str, sql: str) -> str:
    try:
        raw = training_llm_call(
            _DESC_PROMPT.format(question=question[:300], sql=sql[:600]),
            "extraction",
        )
        if raw:
            return raw.strip().strip('"').strip()[:300]
    except Exception:
        logger.debug("skill_library: description gen failed", exc_info=True)
    return (question or "Reusable SQL recipe")[:300]


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.lower()).strip("_")
    return s[:60] or "skill"


# ── Promotion ──────────────────────────────────────────────────────────────
def _fetch_candidate_patterns(eng, project_slug: str) -> List[Dict[str, Any]]:
    """Pull patterns from dash_query_patterns that meet promotion thresholds.

    The legacy schema uses `uses` (instead of usage_count/success_count) and
    may not have avg_judge_score. We probe column existence at runtime and
    adapt. NOT-IN dash_skill_library filter is applied via subquery.
    """
    try:
        with eng.connect() as conn:
            # Probe columns
            col_rows = conn.execute(
                text(
                    """
                    SELECT column_name
                      FROM information_schema.columns
                     WHERE table_schema='public'
                       AND table_name='dash_query_patterns'
                    """
                )
            ).fetchall()
            cols = {r[0] for r in col_rows}

            use_col = (
                "usage_count" if "usage_count" in cols else
                "uses" if "uses" in cols else None
            )
            success_col = "success_count" if "success_count" in cols else use_col
            judge_col = "avg_judge_score" if "avg_judge_score" in cols else None

            if not use_col:
                return []

            judge_filter = (
                f"AND {judge_col} >= :min_judge"
                if judge_col else ""
            )
            judge_select = (
                f"{judge_col} AS avg_judge_score, "
                if judge_col else "NULL::numeric AS avg_judge_score, "
            )
            success_select = f"{success_col} AS success_count, "

            sql = f"""
                SELECT id, question, sql,
                       {use_col} AS usage_count,
                       {success_select}
                       {judge_select}
                       'ok' AS _ok
                  FROM public.dash_query_patterns
                 WHERE project_slug = :s
                   AND {use_col} >= :min_uses
                   {judge_filter}
                   AND id NOT IN (
                       SELECT COALESCE(source_query_pattern_id, -1)
                         FROM public.dash_skill_library
                        WHERE project_slug = :s
                          AND source_query_pattern_id IS NOT NULL
                   )
                 ORDER BY {use_col} DESC
                 LIMIT :lim
            """
            params: Dict[str, Any] = {
                "s": project_slug,
                "min_uses": _MIN_USAGE,
                "lim": _MAX_PROMOTIONS_PER_RUN,
            }
            if judge_col:
                params["min_judge"] = _MIN_JUDGE_SCORE
            rows = conn.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("skill_library: fetch candidate patterns failed")
        return []


def _insert_skill(
    eng,
    project_slug: str,
    name: str,
    description: str,
    sql_template: str,
    params_schema: Dict[str, Any],
    source_query_pattern_id: int,
    source_dream_run_id: int,
    usage_count: int,
    judge_score: Optional[float],
) -> Optional[int]:
    try:
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO public.dash_skill_library
                      (project_slug, name, description, sql_template,
                       params_schema, success_count, failure_count,
                       avg_judge_score, source_query_pattern_id,
                       source_dream_run_id, status, created_at)
                    VALUES
                      (:p, :n, :d, :sqlt,
                       CAST(:ps AS jsonb), :sc, 0,
                       :js, :sqpid,
                       :srid, 'active', now())
                    ON CONFLICT (project_slug, name) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "p": project_slug,
                    "n": name[:120],
                    "d": description[:500],
                    "sqlt": sql_template,
                    "ps": json.dumps(params_schema),
                    "sc": int(usage_count or 0),
                    "js": float(judge_score) if judge_score is not None else None,
                    "sqpid": int(source_query_pattern_id),
                    "srid": int(source_dream_run_id),
                },
            ).first()
            return int(row[0]) if row else None
    except Exception:
        logger.exception("skill_library: insert skill failed")
        return None


def _update_embedding(eng, skill_id: int, embedding: List[float]) -> bool:
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE public.dash_skill_library
                       SET description_embedding = :e
                     WHERE id = :id
                    """
                ),
                {"id": int(skill_id), "e": _vec_to_text(embedding)},
            )
        return True
    except Exception:
        logger.exception("skill_library: update embedding failed")
        return False


def _log_audit(
    eng,
    skill_name: str,
    project_slug: str,
    candidate_sql: str,
    audit_result: Dict[str, Any],
) -> None:
    """Best-effort: persist audit verdict to dash.dash_skill_audit_log."""
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_skill_audit_log
                      (skill_name, project_slug, candidate_sql,
                       audit_result, passed, created_at)
                    VALUES
                      (:n, :p, :sql, CAST(:r AS jsonb), :ok, now())
                    """
                ),
                {
                    "n":   (skill_name or "")[:200],
                    "p":   project_slug,
                    "sql": (candidate_sql or "")[:8000],
                    "r":   json.dumps(audit_result or {}),
                    "ok":  bool(audit_result.get("pass")),
                },
            )
    except Exception:
        logger.debug("skill_library: audit-log insert failed", exc_info=True)


def promote_proven_patterns(project_slug: str, run_id: int) -> dict:
    """Promote eligible query patterns into the skill library.

    Each candidate now passes through the 10-point audit gate
    (`dash.learning.skill_audit.audit_skill_candidate`). Failures are logged
    to `dash.dash_skill_audit_log` and skipped. Passing candidates have their
    `params_schema` auto-inferred from the project schema via
    `dash.learning.skill_schema_infer.infer_params_schema` before INSERT.
    """
    result: Dict[str, Any] = {
        "promoted": 0,
        "skipped_existing": 0,
        "skipped_audit": 0,
        "errors": 0,
        "embedded": 0,
    }
    try:
        eng = _engine()
    except Exception:
        logger.exception("skill_library: engine bootstrap failed")
        result["errors"] += 1
        return result

    candidates = _fetch_candidate_patterns(eng, project_slug)
    if not candidates:
        return result

    # Lazy imports — defensive against partial install
    try:
        from dash.learning.skill_audit import audit_skill_candidate
    except Exception:
        audit_skill_candidate = None  # type: ignore
        logger.warning("skill_library: skill_audit module unavailable — gate disabled")
    try:
        from dash.learning.skill_schema_infer import infer_params_schema
    except Exception:
        infer_params_schema = None  # type: ignore
        logger.warning("skill_library: skill_schema_infer module unavailable")

    for cand in candidates[:_MAX_PROMOTIONS_PER_RUN]:
        try:
            question = str(cand.get("question") or "")
            sql_raw = str(cand.get("sql") or "")
            if not sql_raw.strip():
                continue
            sql_template, params_schema = _parameterize_sql(sql_raw)
            description = _generate_description(question, sql_raw)
            # Derive a short slug name from the question
            name_base = re.sub(r"[^\w\s]+", " ", question).strip() or f"skill_{cand['id']}"
            name = _slugify(name_base)

            judge_val = cand.get("avg_judge_score")
            uses_val  = int(cand.get("usage_count") or 0)

            # ── Audit gate ─────────────────────────────────────────────
            if audit_skill_candidate is not None:
                audit = audit_skill_candidate(
                    name=name,
                    description=description,
                    sql_template=sql_template,
                    params_schema=params_schema,
                    judge_score=judge_val,
                    uses=uses_val,
                    project_slug=project_slug,
                )
                _log_audit(eng, name, project_slug, sql_template, audit)
                if not audit.get("pass"):
                    logger.info(
                        "skill_library: candidate %s blocked by audit (score=%s failures=%s)",
                        name, audit.get("score"), audit.get("failures"),
                    )
                    result["skipped_audit"] += 1
                    continue

            # ── Schema-aware params inference ──────────────────────────
            if infer_params_schema is not None:
                try:
                    inferred = infer_params_schema(sql_template, project_slug)
                    if inferred:
                        # Merge — inferred wins where keys overlap, but keep
                        # any defaults the regex parameterizer set.
                        merged = dict(params_schema or {})
                        for k, v in inferred.items():
                            if k in merged and isinstance(merged[k], dict):
                                merged[k] = {**merged[k], **v}
                            else:
                                merged[k] = v
                        params_schema = merged
                except Exception:
                    logger.debug("skill_library: params inference failed for %s",
                                 name, exc_info=True)

            # Insert
            skill_id = _insert_skill(
                eng,
                project_slug=project_slug,
                name=name,
                description=description,
                sql_template=sql_template,
                params_schema=params_schema,
                source_query_pattern_id=int(cand["id"]),
                source_dream_run_id=int(run_id),
                usage_count=int(cand.get("usage_count") or 0),
                judge_score=cand.get("avg_judge_score"),
            )
            if skill_id is None:
                result["skipped_existing"] += 1
                continue
            result["promoted"] += 1
            # File mirror removed — DB row (dash_skill_library) is the source of
            # truth; the .md disk mirror was a removable side effect.
        except Exception:
            logger.exception("skill_library: promote candidate failed")
            result["errors"] += 1

    # Fill embeddings for any newly inserted skills (best-effort)
    try:
        result["embedded"] = embed_pending_skills(project_slug)
    except Exception:
        logger.debug("skill_library: embed_pending_skills failed", exc_info=True)

    return result


# ── Embedding daemon helper ────────────────────────────────────────────────
def embed_pending_skills(project_slug: str, limit: int = 40) -> int:
    """Embed any skills lacking a description_embedding. Returns count embedded."""
    embedded = 0
    try:
        eng = _engine()
    except Exception:
        logger.exception("skill_library: engine bootstrap failed")
        return 0
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, description
                      FROM public.dash_skill_library
                     WHERE project_slug = :p
                       AND description_embedding IS NULL
                       AND status = 'active'
                     ORDER BY id DESC
                     LIMIT :lim
                    """
                ),
                {"p": project_slug, "lim": int(limit)},
            ).mappings().all()
    except Exception:
        logger.exception("skill_library: fetch pending embeddings failed")
        return 0

    for r in rows:
        try:
            vec = _embed_sync(str(r["description"] or ""))
            if not vec:
                continue
            if _update_embedding(eng, int(r["id"]), vec):
                embedded += 1
        except Exception:
            logger.debug("skill_library: per-row embed failed", exc_info=True)
            continue
    return embedded


# ── Retrieval (HippoRAG-style cosine similarity) ──────────────────────────
def retrieve_skill_hints(
    project_slug: str, question: str, top_k: int = 5
) -> List[Dict[str, Any]]:
    """Return up to top_k skills most semantically similar to the question."""
    if not question or not question.strip():
        return []
    try:
        eng = _engine()
    except Exception:
        logger.exception("skill_library: engine bootstrap failed")
        return []
    try:
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, name, description, sql_template, params_schema,
                           success_count, failure_count, avg_judge_score,
                           description_embedding
                      FROM public.dash_skill_library
                     WHERE project_slug = :p
                       AND status = 'active'
                       AND description_embedding IS NOT NULL
                     ORDER BY success_count DESC
                     LIMIT 100
                    """
                ),
                {"p": project_slug},
            ).mappings().all()
    except Exception:
        logger.exception("skill_library: fetch for retrieval failed")
        return []
    if not rows:
        return []

    q_vec = _embed_sync(question)
    if not q_vec:
        # Fallback: return top by success_count
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "sql_template": r["sql_template"],
                "params_schema": r["params_schema"],
                "success_count": r["success_count"],
                "avg_judge_score": r["avg_judge_score"],
                "score": 0.0,
            }
            for r in rows[:top_k]
        ]

    scored: List[Dict[str, Any]] = []
    for r in rows:
        emb = _text_to_vec(r.get("description_embedding"))
        if not emb:
            continue
        sim = _cosine(q_vec, emb)
        if sim < _RETRIEVE_THRESHOLD:
            continue
        scored.append({
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "sql_template": r["sql_template"],
            "params_schema": r["params_schema"],
            "success_count": r["success_count"],
            "avg_judge_score": r["avg_judge_score"],
            "score": sim,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: int(top_k)]


# ── Usage tracking ─────────────────────────────────────────────────────────
def record_skill_use(
    skill_id: int, succeeded: bool, judge_score: Optional[float] = None
) -> None:
    """Increment success/failure counter + update avg_judge_score + last_used_at."""
    try:
        eng = _engine()
    except Exception:
        logger.exception("skill_library: engine bootstrap failed")
        return
    try:
        with eng.begin() as conn:
            if succeeded:
                if judge_score is not None:
                    conn.execute(
                        text(
                            """
                            UPDATE public.dash_skill_library
                               SET success_count = success_count + 1,
                                   last_used_at = now(),
                                   avg_judge_score = COALESCE(
                                       (avg_judge_score * success_count + :js)
                                         / NULLIF(success_count + 1, 0),
                                       :js
                                   )
                             WHERE id = :id
                            """
                        ),
                        {"id": int(skill_id), "js": float(judge_score)},
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE public.dash_skill_library
                               SET success_count = success_count + 1,
                                   last_used_at = now()
                             WHERE id = :id
                            """
                        ),
                        {"id": int(skill_id)},
                    )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE public.dash_skill_library
                           SET failure_count = failure_count + 1,
                               last_used_at = now()
                         WHERE id = :id
                        """
                    ),
                    {"id": int(skill_id)},
                )
    except Exception:
        logger.exception("skill_library: record_skill_use failed for %s", skill_id)


__all__ = [
    "promote_proven_patterns",
    "retrieve_skill_hints",
    "record_skill_use",
    "embed_pending_skills",
]
