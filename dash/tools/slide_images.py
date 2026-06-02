"""Pexels hero image fetcher for slide cards.

Free Pexels API: 200 req/hour, no cost. Requires PEXELS_API_KEY env.
Returns a hero photo URL matching the slide topic.

Fallback if PEXELS_API_KEY missing OR API fails: returns None and slides
render with theme-color background (no broken image).

Cache by query hash to avoid re-fetching for repeated topics.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_PEXELS_KEY = os.getenv("PEXELS_API_KEY", "").strip()
_PEXELS_BASE = "https://api.pexels.com/v1/search"

# Simple in-memory cache: query_hash -> image_url
_CACHE: dict[str, str] = {}
_CACHE_MAX = 500


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _extract_topic(slide_title: str) -> str:
    """Strip filler so query matches Pexels stock library well.

    "Bakery drives 69% of revenue with 28% YoY growth"
    → "bakery revenue growth"
    """
    if not slide_title:
        return ""
    t = slide_title.lower()
    # drop numbers, percent, common filler
    t = re.sub(r"\b\d+(\.\d+)?%?\b", " ", t)
    t = re.sub(r"\b(yoy|qoq|mom|wow|vs|versus|across|the|of|and|with|in|on|by)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # keep top 4 tokens
    tokens = t.split()[:4]
    return " ".join(tokens)


def fetch_hero(slide_title: str, orientation: str = "landscape",
               size: str = "medium") -> Optional[Tuple[str, str]]:
    """Search Pexels for hero photo matching slide topic.

    Returns (image_url, photographer_credit) or None.
    """
    if not _PEXELS_KEY:
        return None
    query = _extract_topic(slide_title)
    if not query:
        return None
    cache_key = _hash(f"{query}:{orientation}:{size}")
    if cache_key in _CACHE:
        return _CACHE[cache_key], "cached"

    try:
        import httpx
        with httpx.Client(timeout=10) as client:
            r = client.get(
                _PEXELS_BASE,
                headers={"Authorization": _PEXELS_KEY},
                params={
                    "query": query,
                    "per_page": 1,
                    "orientation": orientation,
                    "size": size,
                },
            )
            if r.status_code != 200:
                logger.warning("pexels search %s -> %s", query, r.status_code)
                return None
            data = r.json()
            photos = data.get("photos") or []
            if not photos:
                return None
            photo = photos[0]
            src = photo.get("src") or {}
            url = src.get("large2x") or src.get("large") or src.get("original")
            if not url:
                return None
            credit = photo.get("photographer", "")
            # Cache (cap)
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[cache_key] = url
            return url, credit
    except Exception as e:
        logger.warning("pexels fetch failed (%s): %s", query, e)
        return None


def enrich_slide_with_hero(slide_spec: dict) -> dict:
    """Mutate slide spec, add hero_image_url + hero_credit. Returns spec."""
    title = slide_spec.get("title") or ""
    result = fetch_hero(title)
    if result:
        url, credit = result
        slide_spec["hero_image_url"] = url
        slide_spec["hero_credit"] = credit
    return slide_spec
