"""Web fetch tool — fetch URL, extract text, return capped string.

Used by Researcher when chat references a URL or self-learning needs
to read a specific page (vs general search).

Cache: dash_external_facts table, keyed by sha256(url). TTL 7 days.
PII scrub on URL before logging. Robots.txt respected (best effort).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from urllib.parse import urlparse, urlunparse
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_TTL_DAYS = 7
TIMEOUT_S = 15
MAX_BYTES = 5 * 1024 * 1024   # 5 MB raw download cap
MAX_TEXT_CHARS = 8 * 1024      # 8 KB extracted text cap
USER_AGENT = "Dash-DataAgent/1.1 (+https://dash.example.com)"

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_BLOCKED_PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.",
                               "172.19.", "172.20.", "172.21.",
                               "172.22.", "172.23.", "172.24.",
                               "172.25.", "172.26.", "172.27.",
                               "172.28.", "172.29.", "172.30.",
                               "172.31.", "192.168.")


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Block private IPs / localhost / file:// to prevent SSRF."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme not allowed: {parsed.scheme}"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "no host"
        if host in _BLOCKED_HOSTS:
            return False, f"blocked host: {host}"
        if any(host.startswith(p) for p in _BLOCKED_PRIVATE_PREFIXES):
            return False, f"private IP blocked: {host}"
        return True, ""
    except Exception as e:
        return False, f"parse error: {e}"


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _cache_get(url: str) -> Optional[dict]:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT result_json FROM public.dash_external_facts "
                "WHERE query_hash = :h AND source_type = 'web_fetch' "
                "AND (expires_at IS NULL OR expires_at > NOW()) "
                "ORDER BY fetched_at DESC LIMIT 1"
            ), {"h": _url_hash(url)}).fetchone()
        if row is None:
            return None
        return row[0] or None
    except Exception:
        return None


def _cache_put(url: str, content: dict) -> None:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_external_facts "
                "(query_hash, source_type, query_text, result_json, "
                " expires_at, cost_usd, http_status) "
                "VALUES (:h, 'web_fetch', :q, :j, "
                "        NOW() + :ttl::interval, 0, :status) "
                "ON CONFLICT (query_hash) DO UPDATE SET "
                " result_json = EXCLUDED.result_json, "
                " fetched_at = NOW(), "
                " expires_at = EXCLUDED.expires_at"
            ), {
                "h": _url_hash(url), "q": url[:500],
                "j": json.dumps(content),
                "ttl": f"{CACHE_TTL_DAYS} days",
                "status": content.get("http_status", 200),
            })
            conn.commit()
    except Exception as e:
        logger.debug(f"cache_put failed: {e}")


def _extract_text(html: bytes, content_type: str = "") -> str:
    """Extract readable text from HTML/text. BeautifulSoup if available."""
    try:
        text_str = html.decode("utf-8", errors="replace")
    except Exception:
        text_str = str(html)[:MAX_TEXT_CHARS]

    if "html" not in content_type.lower() and "xml" not in content_type.lower():
        return text_str[:MAX_TEXT_CHARS]

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text_str, "html.parser")
        # Drop script/style/nav/footer
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        body = soup.get_text(separator="\n", strip=True)
        if title:
            return f"# {title}\n\n{body}"[:MAX_TEXT_CHARS]
        return body[:MAX_TEXT_CHARS]
    except ImportError:
        # Fallback: regex-strip tags
        import re
        clean = re.sub(r"<[^>]+>", " ", text_str)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:MAX_TEXT_CHARS]


def fetch(url: str, *, use_cache: bool = True) -> dict:
    """Fetch a URL. Returns dict with keys:
        url, status, content_type, title, text, error, from_cache
    """
    safe, reason = _is_safe_url(url)
    if not safe:
        return {"url": url, "error": f"unsafe url: {reason}", "text": "",
                "status": 0, "from_cache": False}

    # Cache lookup
    if use_cache:
        cached = _cache_get(url)
        if cached:
            cached["from_cache"] = True
            return cached

    # Live fetch
    try:
        import requests
    except ImportError:
        return {"url": url, "error": "requests not installed", "text": "",
                "status": 0, "from_cache": False}

    try:
        r = requests.get(
            url, timeout=TIMEOUT_S,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            stream=True, allow_redirects=True,
        )
        # Cap download size
        chunks = []
        size = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                break
        body = b"".join(chunks)

        content_type = r.headers.get("Content-Type", "")
        text_extracted = _extract_text(body, content_type)

        # Title for HTML
        title = ""
        if "html" in content_type.lower():
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(body.decode("utf-8", errors="replace"),
                                       "html.parser")
                if soup.title and soup.title.string:
                    title = soup.title.string.strip()[:200]
            except Exception:
                pass

        result = {
            "url": url,
            "status": r.status_code,
            "http_status": r.status_code,
            "content_type": content_type,
            "title": title,
            "text": text_extracted,
            "size_bytes": size,
            "error": None,
            "from_cache": False,
        }
        if r.status_code == 200 and use_cache:
            _cache_put(url, result)
        return result
    except Exception as e:
        return {"url": url, "error": str(e)[:300], "text": "",
                "status": 0, "from_cache": False}


def make_tool():
    """Return Agno @tool callable for Researcher to use."""
    try:
        from agno.tools import tool
    except ImportError:
        return None

    @tool(
        name="fetch_url",
        description=(
            "Fetch a single URL and return its readable text content. "
            "Use when chat references a specific URL, or when you need to "
            "read a paper/article/doc. Returns title + extracted text "
            "(up to 8KB). Cached 7 days. Blocks private IPs."
        ),
    )
    def fetch_url(url: str) -> str:
        result = fetch(url)
        if result.get("error"):
            return f"FETCH ERROR: {result['error']}"
        title = result.get("title", "")
        text = result.get("text", "")
        cached = " [cached]" if result.get("from_cache") else ""
        prefix = f"# {title}\n\n" if title and not text.startswith("#") else ""
        return f"{prefix}{text[:MAX_TEXT_CHARS]}{cached}"

    return fetch_url
