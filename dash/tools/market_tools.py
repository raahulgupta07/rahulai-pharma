"""
Market Sentinel tools — external market intelligence (VentureDesk sprint 2).

Mirrors ops_tools.py pattern: module-level fns returning {ok: bool, ...}
+ create_market_tools(project_slug, user_id=None) factory.

DB rules:
  - get_write_engine() for INSERT/UPDATE on dash.* tables
  - get_sql_engine() for SELECTs
  - CAST(:m AS jsonb) — never :m::jsonb (PgBouncer collision)
  - Idempotent upserts via ON CONFLICT
  - Project-scoped on every query
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Engines (fail-soft) ──────────────────────────────────────────────────

def _write_engine():
    try:
        from db.session import get_write_engine
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine
        return get_sql_engine()


def _sql_engine():
    from db.session import get_sql_engine
    return get_sql_engine()


# ── Embedding helper (fail-soft) ────────────────────────────────────────

async def _embed_one(t: str) -> Optional[List[float]]:
    """Embed a single string via dash.tools.embeddings_helper.embed_text.
    Returns None on failure."""
    try:
        from dash.tools.embeddings_helper import embed_text
        vec = await embed_text(t or "")
        if isinstance(vec, list) and len(vec) == 1536:
            return vec
        return None
    except Exception as e:
        logger.debug("embed failed: %s", e)
        return None


def _embed_sync(t: str) -> Optional[List[float]]:
    """Sync wrapper. Returns vector or None."""
    try:
        # If an event loop is already running (inside agent), schedule on a thread.
        try:
            asyncio.get_running_loop()
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(asyncio.run, _embed_one(t))
                return fut.result(timeout=30)
        except RuntimeError:
            return asyncio.run(_embed_one(t))
    except Exception as e:
        logger.debug("embed_sync failed: %s", e)
        return None


def _vec_to_pg(v: Optional[List[float]]) -> Optional[str]:
    if not v:
        return None
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ── 1. ingest_market_signal ─────────────────────────────────────────────

def ingest_market_signal(project_slug: str, signal_type: str, source_url: str,
                         title: str, body: str = "",
                         sector: Optional[str] = None,
                         geography: Optional[str] = None,
                         published_at: Optional[str] = None) -> dict:
    """Embed + upsert a market signal. Idempotent on (project_slug, source_url,
    title) via SELECT-first then INSERT."""
    valid_types = {"news", "filing", "patent", "hire", "funding",
                   "product", "web_traffic"}
    st = (signal_type or "").lower().strip()
    if st not in valid_types:
        return {"ok": False,
                "error": f"signal_type must be one of {sorted(valid_types)}"}
    if not title or not str(title).strip():
        return {"ok": False, "error": "title required"}

    try:
        # Dedup: same project + url + title within 24h = no-op.
        with _sql_engine().connect() as cx:
            row = cx.execute(text("""
                SELECT id FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND COALESCE(source_url,'') = COALESCE(:u,'')
                  AND title = :t
                  AND ingested_at > now() - INTERVAL '24 hours'
                LIMIT 1
            """), {"p": project_slug, "u": source_url, "t": title}).fetchone()
        if row:
            return {"ok": True, "id": str(row[0]), "deduped": True}

        # Embed the title+body for vector search.
        text_for_embed = (title + "\n\n" + (body or ""))[:8000]
        vec = _embed_sync(text_for_embed)
        vec_pg = _vec_to_pg(vec)

        with _write_engine().begin() as cx:
            r = cx.execute(text(f"""
                INSERT INTO dash.dash_market_signals
                    (project_slug, sector, geography, signal_type, source_url,
                     title, body, embedding, published_at)
                VALUES (:p, :sec, :geo, :st, :u, :t, :b,
                        {'CAST(:emb AS vector)' if vec_pg else 'NULL'},
                        :pa)
                RETURNING id
            """), {
                "p": project_slug, "sec": sector, "geo": geography,
                "st": st, "u": source_url, "t": title, "b": body or "",
                "emb": vec_pg, "pa": published_at,
            }).fetchone()
        return {"ok": True, "id": str(r[0]), "embedded": bool(vec_pg)}
    except Exception as e:
        logger.exception("ingest_market_signal failed")
        return {"ok": False, "error": str(e)}


# ── 2. search_signals ───────────────────────────────────────────────────

def search_signals(project_slug: str, query: str, top_k: int = 10,
                   sector: Optional[str] = None) -> dict:
    """Vector cosine search over project's signals. Falls back to ILIKE on
    title+body if embedding fails."""
    k = max(1, min(int(top_k), 50))
    try:
        vec = _embed_sync(query or "")
        vec_pg = _vec_to_pg(vec)

        if vec_pg:
            q = """
                SELECT id, sector, geography, signal_type, source_url, title,
                       body, published_at, ingested_at,
                       1 - (embedding <=> CAST(:emb AS vector)) AS score
                FROM dash.dash_market_signals
                WHERE project_slug = :p AND embedding IS NOT NULL
            """
            params: dict[str, Any] = {"p": project_slug, "emb": vec_pg}
            if sector:
                q += " AND sector ILIKE :sec"
                params["sec"] = f"%{sector}%"
            q += " ORDER BY embedding <=> CAST(:emb AS vector) LIMIT :k"
            params["k"] = k
        else:
            # Lexical fallback.
            q = """
                SELECT id, sector, geography, signal_type, source_url, title,
                       body, published_at, ingested_at,
                       0.0 AS score
                FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND (title ILIKE :ql OR body ILIKE :ql)
            """
            params = {"p": project_slug, "ql": f"%{query[:200]}%"}
            if sector:
                q += " AND sector ILIKE :sec"
                params["sec"] = f"%{sector}%"
            q += " ORDER BY ingested_at DESC LIMIT :k"
            params["k"] = k

        with _sql_engine().connect() as cx:
            rows = cx.execute(text(q), params).fetchall()

        out = [
            {
                "id": str(r[0]), "sector": r[1], "geography": r[2],
                "signal_type": r[3], "source_url": r[4], "title": r[5],
                "body": (r[6] or "")[:500],
                "published_at": r[7].isoformat() if r[7] else None,
                "ingested_at": r[8].isoformat() if r[8] else None,
                "score": float(r[9]) if r[9] is not None else 0.0,
            } for r in rows
        ]
        return {"ok": True, "query": query, "results": out,
                "count": len(out),
                "mode": "vector" if vec_pg else "lexical"}
    except Exception as e:
        logger.exception("search_signals failed")
        return {"ok": False, "error": str(e)}


# ── 3. estimate_tam_sam ─────────────────────────────────────────────────

def estimate_tam_sam(project_slug: str, sector: str, geography: str,
                     deal_id: Optional[str] = None,
                     methodology: str = "bottom_up") -> dict:
    """LLM-prompted TAM/SAM/SOM estimate. Persists + returns numbers +
    assumptions. Confidence reflected in assumptions.confidence (0-1)."""
    valid_methods = {"top_down", "bottom_up", "value_theory", "hybrid"}
    m = methodology if methodology in valid_methods else "bottom_up"

    # Build evidence string from recent signals for context.
    try:
        with _sql_engine().connect() as cx:
            sig_rows = cx.execute(text("""
                SELECT title, signal_type, published_at
                FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND (sector ILIKE :sec OR geography ILIKE :geo)
                ORDER BY COALESCE(published_at, ingested_at) DESC
                LIMIT 15
            """), {"p": project_slug, "sec": f"%{sector}%",
                   "geo": f"%{geography}%"}).fetchall()
    except Exception:
        sig_rows = []

    evidence_lines = [f"- {r[1]}: {r[0]}" for r in sig_rows[:10]]
    evidence_block = "\n".join(evidence_lines) if evidence_lines else "(none available)"

    prompt = f"""You are a market sizing analyst. Estimate TAM/SAM/SOM in USD for the
following segment using the {m} methodology.

Sector: {sector}
Geography: {geography}

Recent market signals (use to inform the estimate, never fabricate beyond them):
{evidence_block}

Return a strict JSON object only:
{{
  "tam_usd": <number, total addressable market>,
  "sam_usd": <number, serviceable addressable market>,
  "som_usd": <number, serviceable obtainable market>,
  "assumptions": {{
    "method_explanation": "<1-2 sentences>",
    "key_drivers": ["...", "..."],
    "confidence": <0.0 to 1.0>,
    "currency": "USD"
  }}
}}
SAM must be ≤ TAM. SOM must be ≤ SAM. If data is insufficient, return
confidence < 0.4 and best-effort numbers."""

    try:
        from dash.settings import training_llm_call
        raw = training_llm_call(prompt, "extraction")
    except Exception as e:
        return {"ok": False, "error": f"LLM call failed: {e}"}

    if not raw:
        return {"ok": False, "error": "empty LLM response"}

    # Robust JSON parse: strip fences, extract first {...}.
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    mobj = re.search(r"\{[\s\S]*\}", s)
    if mobj:
        s = mobj.group(0)
    try:
        parsed = json.loads(s)
    except Exception as e:
        return {"ok": False, "error": f"JSON parse failed: {e}",
                "raw": raw[:500]}

    tam = parsed.get("tam_usd")
    sam = parsed.get("sam_usd")
    som = parsed.get("som_usd")
    assumptions = parsed.get("assumptions") or {}

    try:
        tam_f = float(tam) if tam is not None else None
        sam_f = float(sam) if sam is not None else None
        som_f = float(som) if som is not None else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "non-numeric TAM/SAM/SOM"}

    # Sanity clamp: SAM≤TAM, SOM≤SAM.
    if tam_f and sam_f and sam_f > tam_f:
        sam_f = tam_f
    if sam_f and som_f and som_f > sam_f:
        som_f = sam_f

    try:
        with _write_engine().begin() as cx:
            r = cx.execute(text("""
                INSERT INTO dash.dash_tam_sam_estimates
                    (project_slug, deal_id, sector, geography,
                     tam_usd, sam_usd, som_usd, methodology,
                     assumptions, computed_by)
                VALUES (:p, :d, :sec, :geo, :tam, :sam, :som, :m,
                        CAST(:a AS jsonb), 'market_sentinel')
                RETURNING id
            """), {
                "p": project_slug,
                "d": deal_id if deal_id else None,
                "sec": sector, "geo": geography,
                "tam": tam_f, "sam": sam_f, "som": som_f,
                "m": m,
                "a": json.dumps(assumptions),
            }).fetchone()
        return {"ok": True, "id": str(r[0]),
                "tam_usd": tam_f, "sam_usd": sam_f, "som_usd": som_f,
                "methodology": m, "assumptions": assumptions,
                "deal_id": deal_id,
                "evidence_count": len(sig_rows)}
    except Exception as e:
        logger.exception("estimate_tam_sam persist failed")
        return {"ok": False, "error": str(e)}


# ── 4. competitor_map ───────────────────────────────────────────────────

def competitor_map(project_slug: str, sector: str,
                   geography: Optional[str] = None) -> dict:
    """Return ranked competitors for the sector. Optional geography filter."""
    try:
        q = """
            SELECT id, name, geography, share_pct, evidence, last_updated
            FROM dash.dash_market_competitors
            WHERE project_slug = :p AND sector ILIKE :sec
        """
        params: dict[str, Any] = {"p": project_slug,
                                  "sec": f"%{sector}%" if sector else "%"}
        if geography:
            q += " AND geography ILIKE :geo"
            params["geo"] = f"%{geography}%"
        q += " ORDER BY share_pct DESC NULLS LAST, last_updated DESC LIMIT 50"
        with _sql_engine().connect() as cx:
            rows = cx.execute(text(q), params).fetchall()
        out = [
            {
                "id": str(r[0]), "name": r[1], "geography": r[2],
                "share_pct": float(r[3]) if r[3] is not None else None,
                "evidence": r[4] if isinstance(r[4], list) else
                            (json.loads(r[4]) if r[4] else []),
                "last_updated": r[5].isoformat() if r[5] else None,
            } for r in rows
        ]
        return {"ok": True, "sector": sector, "geography": geography,
                "competitors": out, "count": len(out)}
    except Exception as e:
        logger.exception("competitor_map failed")
        return {"ok": False, "error": str(e)}


# ── 5. trend_detect ─────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with",
    "by", "is", "are", "was", "were", "be", "this", "that", "these",
    "those", "as", "at", "from", "it", "its", "has", "have", "had",
    "but", "not", "if", "then", "than", "so", "we", "our", "you", "your",
    "he", "she", "they", "their", "his", "her", "i", "me", "my",
    "will", "would", "can", "could", "should", "may", "might", "do",
    "does", "did", "new", "more", "less", "all", "any", "some", "no",
    "one", "two", "three", "first", "second", "after", "before",
    "into", "out", "over", "under", "up", "down", "about",
}


def _tokenize(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (s or "").lower())
            if w not in _STOPWORDS]


def trend_detect(project_slug: str, sector: str,
                 lookback_days: int = 90) -> dict:
    """Cluster recent signals by shared keywords. Surface emerging themes
    via simple bigram + signal_type frequency. Cheap, no LLM."""
    days = max(7, min(int(lookback_days), 365))
    try:
        with _sql_engine().connect() as cx:
            rows = cx.execute(text("""
                SELECT title, body, signal_type, COALESCE(published_at, ingested_at) AS dt
                FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND (sector ILIKE :sec OR sector IS NULL)
                  AND COALESCE(published_at, ingested_at) > now() - (:d * INTERVAL '1 day')
                ORDER BY dt DESC
                LIMIT 500
            """), {"p": project_slug,
                   "sec": f"%{sector}%" if sector else "%",
                   "d": days}).fetchall()
        if not rows:
            return {"ok": True, "sector": sector, "days": days,
                    "trends": [], "signal_count": 0}

        # Unigram + bigram frequency.
        unigrams: Counter = Counter()
        bigrams: Counter = Counter()
        by_type: Counter = Counter()
        for r in rows:
            blob = f"{r[0] or ''} {r[1] or ''}"
            toks = _tokenize(blob)
            unigrams.update(toks)
            bigrams.update(zip(toks, toks[1:]))
            by_type[r[2]] += 1

        # Top emerging themes = bigrams with ≥3 occurrences, sorted by freq.
        top_bigrams = [(" ".join(bg), n) for bg, n in bigrams.most_common(30)
                       if n >= 3]
        top_unigrams = [(u, n) for u, n in unigrams.most_common(20) if n >= 5]

        # Sentiment heuristic (very rough).
        positive = {"growth", "raised", "launches", "wins", "expands",
                    "acquires", "partners", "scales", "profitable", "ipo"}
        negative = {"layoffs", "shutdown", "cuts", "decline", "lawsuit",
                    "fraud", "bankruptcy", "warning", "downturn", "loss"}
        sentiment_score = 0
        for w, n in unigrams.items():
            if w in positive:
                sentiment_score += n
            elif w in negative:
                sentiment_score -= n
        if sentiment_score > 5:
            mood = "positive"
        elif sentiment_score < -5:
            mood = "negative"
        else:
            mood = "neutral"

        trends = []
        for theme, n in top_bigrams[:10]:
            trends.append({
                "theme": theme,
                "signal_count": n,
                "sentiment": mood,
            })

        return {"ok": True, "sector": sector, "days": days,
                "trends": trends,
                "top_keywords": [{"term": u, "count": n} for u, n in top_unigrams],
                "by_signal_type": dict(by_type),
                "signal_count": len(rows),
                "mood": mood}
    except Exception as e:
        logger.exception("trend_detect failed")
        return {"ok": False, "error": str(e)}


# ── 6. link_signals_to_deal ─────────────────────────────────────────────

def link_signals_to_deal(deal_id: str, signal_ids: List[str]) -> dict:
    """Many-to-many link. Stores deal_id list inside signal.deal_ids JSONB."""
    if not deal_id or not signal_ids:
        return {"ok": False, "error": "deal_id and signal_ids required"}
    linked = 0
    try:
        with _write_engine().begin() as cx:
            for sid in signal_ids:
                try:
                    r = cx.execute(text("""
                        UPDATE dash.dash_market_signals
                        SET deal_ids = CASE
                            WHEN deal_ids @> CAST(:dj AS jsonb) THEN deal_ids
                            ELSE deal_ids || CAST(:dj AS jsonb)
                        END
                        WHERE id = :sid
                        RETURNING id
                    """), {"sid": sid, "dj": json.dumps([deal_id])}).fetchone()
                    if r:
                        linked += 1
                except Exception:
                    logger.debug("link skipped for %s", sid, exc_info=True)
        return {"ok": True, "deal_id": deal_id, "linked": linked,
                "requested": len(signal_ids)}
    except Exception as e:
        logger.exception("link_signals_to_deal failed")
        return {"ok": False, "error": str(e)}


# ── 7. summarize_market_for_memo ────────────────────────────────────────

def summarize_market_for_memo(deal_id: str) -> dict:
    """Return {tam, sam, som, top_competitors, key_signals, trends} for IC
    memo injection. Graceful empty result if no market data linked."""
    try:
        with _sql_engine().connect() as cx:
            # Pull deal context.
            deal_row = cx.execute(text("""
                SELECT project_slug, sector, geography
                FROM dash.dash_venture_deals WHERE id = :d
            """), {"d": deal_id}).fetchone()
            if not deal_row:
                return {"ok": False, "error": "deal not found",
                        "empty": True}
            slug, sector, geo = deal_row[0], deal_row[1] or "", deal_row[2] or ""

            # Latest TAM/SAM estimate for this deal (or sector match).
            est = cx.execute(text("""
                SELECT tam_usd, sam_usd, som_usd, methodology, assumptions,
                       computed_at
                FROM dash.dash_tam_sam_estimates
                WHERE (deal_id = :d)
                   OR (deal_id IS NULL AND project_slug = :p
                       AND sector ILIKE :sec)
                ORDER BY (deal_id = :d) DESC NULLS LAST, computed_at DESC
                LIMIT 1
            """), {"d": deal_id, "p": slug,
                   "sec": f"%{sector}%" if sector else "%"}).fetchone()

            # Top competitors for sector.
            comps = cx.execute(text("""
                SELECT name, share_pct, geography
                FROM dash.dash_market_competitors
                WHERE project_slug = :p AND sector ILIKE :sec
                ORDER BY share_pct DESC NULLS LAST, last_updated DESC
                LIMIT 3
            """), {"p": slug, "sec": f"%{sector}%" if sector else "%"}).fetchall()

            # Signals linked to deal OR top sector signals.
            sigs = cx.execute(text("""
                SELECT id, signal_type, title, source_url,
                       COALESCE(published_at, ingested_at) AS dt
                FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND (deal_ids @> CAST(:dj AS jsonb)
                       OR sector ILIKE :sec)
                ORDER BY dt DESC
                LIMIT 5
            """), {"p": slug, "dj": json.dumps([deal_id]),
                   "sec": f"%{sector}%" if sector else "%"}).fetchall()

        # Run trend_detect inline (cheap).
        trends_resp = trend_detect(slug, sector or "", lookback_days=90)
        trends = trends_resp.get("trends", [])[:3] if trends_resp.get("ok") else []

        result = {
            "ok": True,
            "deal_id": deal_id,
            "sector": sector,
            "geography": geo,
            "tam_usd": float(est[0]) if est and est[0] else None,
            "sam_usd": float(est[1]) if est and est[1] else None,
            "som_usd": float(est[2]) if est and est[2] else None,
            "methodology": est[3] if est else None,
            "assumptions": (est[4] if isinstance(est[4], dict)
                            else (json.loads(est[4]) if est and est[4] else {}))
                           if est else {},
            "estimated_at": est[5].isoformat() if est and est[5] else None,
            "top_competitors": [
                {"name": c[0],
                 "share_pct": float(c[1]) if c[1] is not None else None,
                 "geography": c[2]}
                for c in comps
            ],
            "key_signals": [
                {"id": str(s[0]), "signal_type": s[1], "title": s[2],
                 "source_url": s[3],
                 "date": s[4].isoformat() if s[4] else None}
                for s in sigs
            ],
            "trends": trends,
            "empty": (est is None and not comps and not sigs),
        }
        return result
    except Exception as e:
        logger.exception("summarize_market_for_memo failed")
        return {"ok": False, "error": str(e), "empty": True}


# ── 8. refresh_competitor_shares ────────────────────────────────────────

def refresh_competitor_shares(project_slug: str, sector: str) -> dict:
    """Recompute share_pct + last_updated from recent funding/product signals.
    Heuristic: count distinct mentions per name in last 180d; normalize to
    relative share. Names are extracted from signal titles via simple
    proper-noun heuristic when no existing competitor matches."""
    try:
        with _sql_engine().connect() as cx:
            sigs = cx.execute(text("""
                SELECT title, body, signal_type
                FROM dash.dash_market_signals
                WHERE project_slug = :p
                  AND sector ILIKE :sec
                  AND COALESCE(published_at, ingested_at) > now() - INTERVAL '180 days'
                LIMIT 500
            """), {"p": project_slug,
                   "sec": f"%{sector}%" if sector else "%"}).fetchall()
            existing = cx.execute(text("""
                SELECT name FROM dash.dash_market_competitors
                WHERE project_slug = :p AND sector ILIKE :sec
            """), {"p": project_slug,
                   "sec": f"%{sector}%" if sector else "%"}).fetchall()
        existing_names = [r[0] for r in existing]

        # Count mentions per existing competitor.
        counts: Counter = Counter()
        for r in sigs:
            blob = f"{r[0] or ''} {r[1] or ''}".lower()
            for name in existing_names:
                if name and name.lower() in blob:
                    counts[name] += 1

        if not counts:
            return {"ok": True, "sector": sector, "updated": 0,
                    "note": "no recent mentions"}

        total = sum(counts.values())
        updated = 0
        with _write_engine().begin() as cx:
            for name, n in counts.items():
                share = round(100.0 * n / total, 3) if total else 0.0
                cx.execute(text("""
                    UPDATE dash.dash_market_competitors
                    SET share_pct = :s,
                        evidence = COALESCE(evidence, '[]'::jsonb)
                                   || CAST(:ev AS jsonb),
                        last_updated = now()
                    WHERE project_slug = :p AND sector ILIKE :sec
                      AND name = :n
                """), {
                    "s": share, "p": project_slug,
                    "sec": f"%{sector}%" if sector else "%",
                    "n": name,
                    "ev": json.dumps([{
                        "ts": datetime.utcnow().isoformat(),
                        "mentions": n, "window_days": 180,
                    }]),
                })
                updated += 1
        return {"ok": True, "sector": sector, "updated": updated,
                "total_mentions": total,
                "scanned_signals": len(sigs)}
    except Exception as e:
        logger.exception("refresh_competitor_shares failed")
        return {"ok": False, "error": str(e)}


# ── @tool factory ───────────────────────────────────────────────────────

def create_market_tools(project_slug: str, user_id: Optional[int] = None):
    """Return Agno @tool wrappers for Market Sentinel."""
    from agno.tools import tool

    @tool(name="ingest_market_signal",
          description="Persist a market signal w/ vector embedding for later "
          "search. signal_type ∈ {news,filing,patent,hire,funding,product,"
          "web_traffic}. Idempotent on (url, title) within 24h.")
    def _ingest(signal_type: str, source_url: str, title: str,
                 body: str = "", sector: str = "", geography: str = "",
                 published_at: str = "") -> str:
        r = ingest_market_signal(project_slug, signal_type, source_url,
                                  title, body or "",
                                  sector or None, geography or None,
                                  published_at or None)
        return json.dumps(r, default=str)

    @tool(name="search_signals",
          description="Vector cosine search over project market signals. "
          "Falls back to ILIKE if embedding fails. Args: query, top_k "
          "(default 10), sector (optional).")
    def _search(query: str, top_k: int = 10, sector: str = "") -> str:
        r = search_signals(project_slug, query, top_k, sector or None)
        return json.dumps(r, default=str)

    @tool(name="estimate_tam_sam",
          description="LLM-prompted TAM/SAM/SOM estimate. Methods: top_down, "
          "bottom_up, value_theory, hybrid. Always returns "
          "assumptions.confidence (0-1). Persists row.")
    def _estimate(sector: str, geography: str, deal_id: str = "",
                   methodology: str = "bottom_up") -> str:
        r = estimate_tam_sam(project_slug, sector, geography,
                              deal_id or None, methodology)
        return json.dumps(r, default=str)

    @tool(name="competitor_map",
          description="Ranked competitors for sector (+ optional geography). "
          "Returns name, share_pct, evidence array, last_updated.")
    def _comps(sector: str, geography: str = "") -> str:
        r = competitor_map(project_slug, sector, geography or None)
        return json.dumps(r, default=str)

    @tool(name="trend_detect",
          description="Cluster recent signals (lookback_days, default 90) "
          "into emerging themes. Cheap (no LLM): bigram frequency + simple "
          "sentiment. Returns trends[] + top_keywords[] + signal_type "
          "distribution + overall mood.")
    def _trend(sector: str, lookback_days: int = 90) -> str:
        r = trend_detect(project_slug, sector, lookback_days)
        return json.dumps(r, default=str)

    @tool(name="link_signals_to_deal",
          description="Attach signal_ids[] to a deal so the IC memo can "
          "cite them. Stored in signal.deal_ids JSONB (many-to-many).")
    def _link(deal_id: str, signal_ids: List[str]) -> str:
        r = link_signals_to_deal(deal_id, signal_ids)
        return json.dumps(r, default=str)

    @tool(name="summarize_market_for_memo",
          description="Pull {tam, sam, som, top_competitors, key_signals, "
          "trends} for IC memo. Graceful empty result if no data linked.")
    def _summ(deal_id: str) -> str:
        r = summarize_market_for_memo(deal_id)
        return json.dumps(r, default=str)

    @tool(name="refresh_competitor_shares",
          description="Recompute competitor share_pct from last 180d signal "
          "mentions. Only updates existing dash_market_competitors rows.")
    def _refresh(sector: str) -> str:
        r = refresh_competitor_shares(project_slug, sector)
        return json.dumps(r, default=str)

    return [_ingest, _search, _estimate, _comps, _trend, _link, _summ,
            _refresh]
