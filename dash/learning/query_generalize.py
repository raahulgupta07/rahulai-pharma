"""Query generalization (P5) — turn many similar learned queries into ONE
parameterized template, so a single proven pattern covers a whole family of
questions ("stock value of Antibiotics" + "...Painkillers" -> "...of {category}").

Approach (no new serve path): cluster proven chat patterns that share the same
source table + SQL shape, ask the LLM to emit ONE parameterized SQL + a templated
question, and write it back into the bank as a NEW pattern (source='chat',
status='pending') — so it flows through the SAME review gate. Once approved it
becomes a Mode-2 recall hint; the agent fills the slot by ADAPTING the hint (the
LLM is already the slot-filler — no template engine needed).

LLM is used only to GENERALIZE (offline, admin-triggered). It never executes SQL.
Fail-soft. Admin-triggered via app/query_bank_api.py; not on any daemon.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("dash.query_generalize")


def _shape(sql: str) -> str:
    """A coarse SQL fingerprint: lowercased, literals/numbers blanked, collapsed.
    Two queries with the same shape differ only in filter values."""
    s = (sql or "").lower()
    s = re.sub(r"'[^']*'", "'?'", s)                 # string literals
    s = re.sub(r"\b\d+(\.\d+)?\b", "?", s)            # numbers
    s = re.sub(r"\bas\s+[a-z_][a-z0-9_]*", "as ?", s)  # column aliases (total_qty vs total_quantity)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cluster(patterns: list[dict]) -> list[list[dict]]:
    """Group patterns by (first table, SQL shape). Only clusters of >= 2 matter."""
    buckets: dict[str, list[dict]] = {}
    for p in patterns:
        tbl = (p.get("tables") or "").split(",")[0].strip()
        key = f"{tbl}::{_shape(p.get('sql') or '')}"
        buckets.setdefault(key, []).append(p)
    return [grp for grp in buckets.values() if len(grp) >= 2]


def propose_generalizations(slug: str, *, max_clusters: int = 10,
                            dry_run: bool = True) -> dict:
    """Find clusters of similar proven chat patterns and propose ONE parameterized
    template per cluster. dry_run=True returns proposals without writing; False
    writes each as a pending pattern (review-gated). Fail-soft."""
    out = {"clusters": 0, "proposed": 0, "written": 0, "proposals": []}
    try:
        from dash.learning.query_curator import list_patterns
        proven = list_patterns(slug, source="chat", status="proven", limit=500)
        clusters = _cluster(proven)
        out["clusters"] = len(clusters)
        if not clusters:
            return out

        from dash.settings import training_llm_call as _tllm
        import json as _json

        for grp in clusters[:max_clusters]:
            examples = [{"q": p["question"], "sql": p["sql"]} for p in grp[:6]]
            prompt = (
                "These chat questions were all answered with the SAME SQL shape, "
                "differing only in a filter value. Produce ONE generalized version:\n"
                "- a templated QUESTION using a {slot} placeholder, and\n"
                "- a parameterized SQL using the SAME slot name as a placeholder "
                "(keep it valid PostgreSQL; do not invent columns).\n"
                "Return ONLY JSON: {\"slot\":\"<name>\",\"question\":\"...{slot}...\","
                "\"sql\":\"...{slot}...\"}.\n\n"
                f"Examples:\n{_json.dumps(examples, ensure_ascii=False)}\n"
            )
            try:
                raw = (_tllm(prompt, "extraction") or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
                    if raw.startswith("json"):
                        raw = raw[4:]
                a = raw.find("{"); b = raw.rfind("}")
                if a < 0 or b <= a:
                    continue
                gen = _json.loads(raw[a:b + 1])
                tq = (gen.get("question") or "").strip()
                tsql = (gen.get("sql") or "").strip()
                if not tq or not tsql or "{" not in tq:
                    continue
                proposal = {"slot": gen.get("slot"), "question": tq, "sql": tsql,
                            "from_count": len(grp),
                            "example_ids": [p["id"] for p in grp[:6]]}
                out["proposed"] += 1
                out["proposals"].append(proposal)
                if not dry_run:
                    _written = _write_template(slug, tq, tsql)
                    if _written:
                        out["written"] += 1
                        proposal["written_id"] = _written
            except Exception as ge:  # noqa: BLE001
                logger.debug("generalize cluster failed: %s", ge)
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        return out


def _write_template(slug: str, question: str, sql: str) -> int | None:
    """Write a generalized template as a pending chat pattern + embed it for
    recall. Reuses query_capture's writer indirectly via a direct INSERT."""
    from sqlalchemy import text as _text
    from db.session import get_write_engine
    import re as _re
    norm = _re.sub(r"\s+", " ", question.strip().lower())
    try:
        from dash.learning.schema_guard import sql_source_tables, live_schema_hash
        tables = sql_source_tables(sql)
        sh = live_schema_hash(slug, tables) if tables else None
        tu = ",".join(tables) if tables else None
    except Exception:
        sh, tu = None, None
    try:
        eng = get_write_engine()
        with eng.begin() as conn:
            pid = conn.execute(_text(
                "INSERT INTO public.dash_query_patterns "
                "  (project_slug, question, question_norm, sql, source, status, "
                "   schema_hash, tables_used, uses, last_used, created_at) "
                "VALUES (:s, :q, :n, :sql, 'chat', 'pending', :sh, :tu, 0, now(), now()) "
                "ON CONFLICT (project_slug, question_norm) WHERE question_norm IS NOT NULL "
                "DO UPDATE SET sql = EXCLUDED.sql, schema_hash = EXCLUDED.schema_hash "
                "RETURNING id"
            ), {"s": slug, "q": question, "n": norm, "sql": sql, "sh": sh, "tu": tu}).scalar()
        if pid is None:
            return None
        # Embed the templated question so recall can surface it.
        try:
            import asyncio
            from dash.tools.embeddings_helper import embed_text
            emb = asyncio.run(embed_text(question))
            if emb:
                import hashlib
                vec = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"
                with eng.begin() as conn:
                    conn.execute(_text(
                        "INSERT INTO dash.dash_vectors "
                        "  (project_slug, namespace, source_id, text, text_hash, embedding, metadata) "
                        "VALUES (:s, 'qbank', :sid, :t, :h, CAST(:v AS vector), '{}'::jsonb) "
                        "ON CONFLICT (project_slug, namespace, source_id) DO UPDATE SET "
                        "  text = EXCLUDED.text, embedding = EXCLUDED.embedding, updated_at = now()"
                    ), {"s": slug, "sid": str(pid), "t": question,
                        "h": hashlib.sha256(question.encode()).hexdigest(), "v": vec})
        except Exception:
            pass
        return int(pid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_write_template failed: %s", exc)
        return None
