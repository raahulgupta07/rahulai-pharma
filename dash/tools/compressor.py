"""Dash-OS Phase 2D — Web research CompressionManager.

When Researcher does multi-hop web search, raw results balloon context
(~10K tokens × N hops). Compress each result to ~1K tokens via LITE_MODEL
+ sha256 dedup of seen URLs. Saves ~60% cost on deep research.

Behind EXPERIMENTAL_AGI=1. Otherwise pass-through.

Example wiring (Phase 6+, NOT in this phase):
    from dash.tools.compressor import CompressionManager
    mgr = CompressionManager()
    raw = await exa_search(query)
    compressed = await mgr.compress_search_results(raw, query_intent=query)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("EXPERIMENTAL_AGI") == "1"


def _get_engine():
    try:
        from db.session import get_sql_engine
        return get_sql_engine()
    except Exception:
        try:
            from db import get_sql_engine  # type: ignore
            return get_sql_engine()
        except Exception:
            return None


def _ctx() -> Dict[str, Any]:
    try:
        from dash.agentic.hooks import (
            current_project_slug, current_user_id, current_run_id,
        )
        return {
            "project_slug": current_project_slug.get(),
            "user_id": current_user_id.get(),
            "run_id": current_run_id.get(),
        }
    except Exception:
        return {"project_slug": None, "user_id": None, "run_id": None}


def _norm_url(url: str) -> str:
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    except Exception:
        return url


def _cache_key(url: str, query_intent: str) -> str:
    return hashlib.sha256((url + (query_intent or "")[:200]).encode("utf-8")).hexdigest()


def _dedup_key(url: str, body: str) -> str:
    return hashlib.sha256((_norm_url(url) + body[:200]).encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    eng = _get_engine()
    if eng is None:
        return None
    try:
        from sqlalchemy import text
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT compressed_text, original_chars, compressed_chars, model_used "
                    "FROM dash.dash_compression_cache WHERE cache_key=:k"
                ),
                {"k": key},
            ).mappings().first()
        if row:
            with eng.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE dash.dash_compression_cache "
                        "SET hit_count = hit_count + 1, last_hit_at = now() "
                        "WHERE cache_key = :k"
                    ),
                    {"k": key},
                )
            return dict(row)
    except Exception as e:
        logger.warning("cache_get failed: %s", e)
    return None


def _cache_put(
    key: str, url: str, query_intent: str, original_chars: int,
    compressed: str, model_used: str, cost_usd: float = 0.0,
) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_compression_cache
                      (cache_key, url, original_chars, compressed_chars, query_intent,
                       compressed_text, model_used, cost_usd)
                    VALUES (:k, :u, :oc, :cc, :qi, :ct, :m, :cu)
                    ON CONFLICT (cache_key)
                      DO UPDATE SET hit_count = dash.dash_compression_cache.hit_count + 1,
                                    last_hit_at = now()
                    """
                ),
                {
                    "k": key, "u": url, "oc": original_chars,
                    "cc": len(compressed), "qi": (query_intent or "")[:500],
                    "ct": compressed, "m": model_used, "cu": cost_usd,
                },
            )
    except Exception as e:
        logger.warning("cache_put failed: %s", e)


def _log_stats(
    query: str, raw_chars: int, compressed_chars: int, results_in: int,
    results_out: int, dedup_skipped: int, cost_usd: float, latency_ms: int,
) -> None:
    eng = _get_engine()
    if eng is None:
        return
    try:
        from sqlalchemy import text
        c = _ctx()
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_compression_stats
                      (project_slug, user_id, run_id, query, raw_chars, compressed_chars,
                       results_in, results_out, dedup_skipped, cost_usd, latency_ms)
                    VALUES (:ps, :uid, :rid, :q, :rc, :cc, :ri, :ro, :ds, :cu, :lm)
                    """
                ),
                {
                    "ps": c["project_slug"], "uid": c["user_id"], "rid": c["run_id"],
                    "q": (query or "")[:500], "rc": raw_chars, "cc": compressed_chars,
                    "ri": results_in, "ro": results_out, "ds": dedup_skipped,
                    "cu": cost_usd, "lm": latency_ms,
                },
            )
    except Exception as e:
        logger.warning("stats log failed: %s", e)


def _llm_compress(text: str, query_intent: str, target_chars: int) -> tuple[str, str, float]:
    """Returns (compressed_text, model_used, cost_usd). Fail-soft to truncate."""
    try:
        from dash.settings import training_llm_call, LITE_MODEL  # type: ignore
        prompt = (
            f"Compress the following web page to {target_chars} chars or less while "
            f"preserving facts, numbers, dates, names relevant to this query: "
            f"{query_intent}\n\nPAGE:\n{text[:8000]}\n\nCOMPRESSED:"
        )
        result = training_llm_call(prompt, "extraction")
        if isinstance(result, str) and result.strip():
            return result.strip()[:target_chars], LITE_MODEL or "unknown", 0.0
    except Exception as e:
        logger.warning("llm compress failed: %s", e)
    # fail-soft truncation
    return text[:target_chars], "truncate", 0.0


class CompressionManager:
    def __init__(self, target_chars_per_doc: int = 4000, dedup: bool = True, cache_ttl_days: int = 7):
        self.target_chars = target_chars_per_doc
        self.dedup_on = dedup
        self.cache_ttl_days = cache_ttl_days
        self._seen: set = set()
        self._counters = {
            "results_in": 0, "results_out": 0, "dedup_skipped": 0,
            "cache_hits": 0, "cache_misses": 0,
        }

    def add_seen(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def stats(self) -> Dict[str, Any]:
        return dict(self._counters)

    async def compress_search_results(
        self, results: List[Dict[str, Any]], query_intent: str = "",
    ) -> List[Dict[str, Any]]:
        if not _enabled():
            return results

        started = time.time()
        raw_total = 0
        comp_total = 0
        out: List[Dict[str, Any]] = []

        for r in results:
            self._counters["results_in"] += 1
            url = r.get("url") or ""
            body = r.get("body") or r.get("raw_text") or r.get("snippet") or ""
            raw_total += len(body)

            if self.dedup_on:
                dkey = _dedup_key(url, body)
                if not self.add_seen(dkey):
                    self._counters["dedup_skipped"] += 1
                    continue

            ckey = _cache_key(url, query_intent)
            cached = _cache_get(ckey)
            if cached:
                self._counters["cache_hits"] += 1
                compressed = cached["compressed_text"]
            else:
                self._counters["cache_misses"] += 1
                compressed, model_used, cost = _llm_compress(body, query_intent, self.target_chars)
                _cache_put(ckey, url, query_intent, len(body), compressed, model_used, cost)

            comp_total += len(compressed)
            new_r = dict(r)
            new_r["body"] = compressed
            new_r["raw_text"] = compressed
            new_r["compressed"] = True
            new_r["original_chars"] = len(body)
            new_r["compressed_chars"] = len(compressed)
            out.append(new_r)
            self._counters["results_out"] += 1

        latency_ms = int((time.time() - started) * 1000)
        _log_stats(
            query=query_intent, raw_chars=raw_total, compressed_chars=comp_total,
            results_in=self._counters["results_in"], results_out=self._counters["results_out"],
            dedup_skipped=self._counters["dedup_skipped"], cost_usd=0.0,
            latency_ms=latency_ms,
        )
        return out


def evict_stale(days: int = 7) -> int:
    eng = _get_engine()
    if eng is None:
        return 0
    try:
        from sqlalchemy import text
        with eng.begin() as conn:
            r = conn.execute(
                text(
                    "DELETE FROM dash.dash_compression_cache "
                    "WHERE created_at < now() - (:d || ' days')::interval"
                ),
                {"d": days},
            )
            return r.rowcount or 0
    except Exception as e:
        logger.warning("evict_stale failed: %s", e)
        return 0
