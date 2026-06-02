"""Web search provider — Tavily/Brave/Perplexity API w/ cache + PII scrub.

Fallback chain: try TAVILY_API_KEY → BRAVE_API_KEY → PERPLEXITY_API_KEY
→ skip if none configured.

PII scrub: regex-strip email/phone/SSN/credit-card BEFORE sending.

Cache: hash(query+source) → dash_external_facts. TTL default 7 days.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

# PII patterns to redact before sending out
_PII_PATTERNS = [
    (re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+'), '<EMAIL>'),
    (re.compile(r'\+?\d{10,15}'), '<PHONE>'),
    (re.compile(r'\d{3}-?\d{2}-?\d{4}'), '<SSN>'),
    (re.compile(r'\b4\d{12}(\d{3})?\b'), '<CC>'),
    (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), '<APIKEY>'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), '<AWSKEY>'),
]

CACHE_TTL_DAYS = 7
TIMEOUT_S = 15

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 0.5
    source: str = ""

@dataclass
class SearchResponse:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    summary: str = ""             # if provider returns aggregate (Perplexity)
    source_type: str = ""          # 'tavily'|'brave'|'perplexity'|'cache'|'none'
    cost_usd: float = 0.0
    error: Optional[str] = None
    from_cache: bool = False


def _scrub(query: str) -> str:
    """Remove PII before external send."""
    for pattern, replacement in _PII_PATTERNS:
        query = pattern.sub(replacement, query)
    return query[:500]  # also length cap


def _query_hash(query: str, source: str) -> str:
    return hashlib.sha256(f"{source}:{query.lower().strip()}".encode()).hexdigest()


def _cache_get(query: str, source: str) -> Optional[SearchResponse]:
    """Check dash_external_facts for unexpired hit."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT result_json, result_summary, source_type, cost_usd "
                "FROM public.dash_external_facts "
                "WHERE query_hash = :h AND (expires_at IS NULL OR expires_at > NOW()) "
                "ORDER BY fetched_at DESC LIMIT 1"
            ), {"h": _query_hash(query, source)}).fetchone()
        if row is None:
            return None
        data = row[0] or {}
        results = [SearchResult(**r) for r in data.get("results", [])]
        return SearchResponse(
            query=query, results=results, summary=row[1] or "",
            source_type=row[2], cost_usd=float(row[3] or 0), from_cache=True,
        )
    except Exception as e:
        logger.debug(f"cache_get failed: {e}")
        return None


def _cache_put(query: str, source: str, resp: SearchResponse, ttl_days: int = CACHE_TTL_DAYS):
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        payload = {
            "results": [asdict(r) for r in resp.results],
            "summary": resp.summary,
        }
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_external_facts "
                "(query_hash, source_type, query_text, result_json, result_summary, "
                " expires_at, cost_usd, http_status) "
                "VALUES (:h, :s, :q, :j, :sum, NOW() + :ttl::interval, :c, 200) "
                "ON CONFLICT (query_hash) DO UPDATE SET "
                " result_json = EXCLUDED.result_json, "
                " result_summary = EXCLUDED.result_summary, "
                " fetched_at = NOW(), "
                " expires_at = EXCLUDED.expires_at"
            ), {
                "h": _query_hash(query, source), "s": source, "q": query,
                "j": json.dumps(payload), "sum": resp.summary,
                "ttl": f"{ttl_days} days", "c": resp.cost_usd,
            })
            conn.commit()
    except Exception as e:
        logger.debug(f"cache_put failed: {e}")


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _tavily_search(query: str) -> SearchResponse:
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_tavily"):
            return SearchResponse(query=query, source_type="tavily",
                                   error="disabled by admin setting")
    except Exception:
        pass
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return SearchResponse(query=query, source_type="tavily", error="no api key")

    try:
        import requests
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key, "query": query,
                "search_depth": "basic", "max_results": 5,
                "include_answer": True,
            },
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return SearchResponse(query=query, source_type="tavily",
                                   error=f"http {r.status_code}: {r.text[:200]}")
        data = r.json()
        results = [
            SearchResult(
                title=x.get("title", ""), url=x.get("url", ""),
                snippet=x.get("content", "")[:500],
                score=float(x.get("score", 0.5)),
                source="tavily",
            )
            for x in data.get("results", [])
        ]
        return SearchResponse(
            query=query, results=results,
            summary=data.get("answer", "") or "",
            source_type="tavily", cost_usd=0.005,
        )
    except Exception as e:
        return SearchResponse(query=query, source_type="tavily", error=str(e)[:200])


def _brave_search(query: str) -> SearchResponse:
    """Brave Search API. Use https://api.search.brave.com/res/v1/web/search"""
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_brave_search"):
            return SearchResponse(query=query, source_type="brave",
                                   error="disabled by admin setting")
    except Exception:
        pass
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return SearchResponse(query=query, source_type="brave", error="no api key")
    try:
        import requests
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params={"q": query, "count": 5},
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return SearchResponse(query=query, source_type="brave",
                                   error=f"http {r.status_code}")
        data = r.json()
        web = data.get("web", {}).get("results", [])
        results = [
            SearchResult(
                title=x.get("title", ""), url=x.get("url", ""),
                snippet=(x.get("description") or "")[:500],
                source="brave",
            )
            for x in web
        ]
        return SearchResponse(
            query=query, results=results, source_type="brave", cost_usd=0.001,
        )
    except Exception as e:
        return SearchResponse(query=query, source_type="brave", error=str(e)[:200])


def _perplexity_search(query: str) -> SearchResponse:
    """Perplexity API (chat completion w/ search)."""
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_perplexity"):
            return SearchResponse(query=query, source_type="perplexity",
                                   error="disabled by admin setting")
    except Exception:
        pass
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return SearchResponse(query=query, source_type="perplexity", error="no api key")
    try:
        import requests
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "llama-3-sonar-small-online",
                "messages": [{"role": "user", "content": query}],
            },
            timeout=TIMEOUT_S,
        )
        if r.status_code != 200:
            return SearchResponse(query=query, source_type="perplexity",
                                   error=f"http {r.status_code}")
        data = r.json()
        summary = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return SearchResponse(
            query=query, results=[], summary=summary[:2000],
            source_type="perplexity", cost_usd=0.005,
        )
    except Exception as e:
        return SearchResponse(query=query, source_type="perplexity", error=str(e)[:200])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(query: str, *, max_results: int = 5, use_cache: bool = True,
           prefer: Optional[str] = None) -> SearchResponse:
    """Run web search w/ PII scrub + cache + fallback chain.

    Returns SearchResponse. error field set if all providers fail.
    """
    scrubbed = _scrub(query)

    # 1. Cache lookup (any source)
    if use_cache:
        for src in (prefer or "tavily", "tavily", "brave", "perplexity"):
            cached = _cache_get(scrubbed, src)
            if cached and cached.results or (cached and cached.summary):
                return cached

    # 2. Provider chain
    chain = [prefer] if prefer else []
    chain.extend(["tavily", "brave", "perplexity"])
    chain = [c for c in chain if c]  # remove None
    seen = set()

    for provider in chain:
        if provider in seen:
            continue
        seen.add(provider)
        if provider == "tavily":
            resp = _tavily_search(scrubbed)
        elif provider == "brave":
            resp = _brave_search(scrubbed)
        elif provider == "perplexity":
            resp = _perplexity_search(scrubbed)
        else:
            continue

        if resp.error is None and (resp.results or resp.summary):
            if use_cache:
                _cache_put(scrubbed, provider, resp)
            return resp

    return SearchResponse(query=query, source_type="none",
                           error="all providers failed or none configured")


def is_configured() -> dict:
    """Check which providers have API keys set."""
    return {
        "tavily": bool(os.environ.get("TAVILY_API_KEY")),
        "brave": bool(os.environ.get("BRAVE_API_KEY")),
        "perplexity": bool(os.environ.get("PERPLEXITY_API_KEY")),
    }
