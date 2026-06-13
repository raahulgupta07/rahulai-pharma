"""Human-friendly shop labels — site_code → "Shop N".

Counter staff and consumers don't know `20063-CCBRBKMY`; they think in "Shop 1,
Shop 2". This maps every live outlet code to a stable display number by sorting
the distinct site_codes in `citypharma.shop_flat` and numbering them 1..N.

Auto-numbered (zero config). The order is alphabetical by site_code, so the
numbering is STABLE as long as the outlet set is stable; adding an outlet that
sorts in the middle can shift later numbers — acceptable for a display label
(the site_code stays the canonical id everywhere internal).

Cached for SHOP_LABEL_TTL seconds (default 300) so we don't re-query per call.
Own short read-only connection, same pattern as the other pharma tools.
"""
from __future__ import annotations

import os
import time
import logging

log = logging.getLogger("dash.shop_labels")

SCHEMA = "citypharma"
FLAT = f'"{SCHEMA}"."shop_flat"'
_TTL = int(os.getenv("SHOP_LABEL_TTL", "300"))

# module cache: {"at": epoch, "map": {site_code: "Shop N"}}
_CACHE: dict = {"at": 0.0, "map": {}}


def _conn():
    from dash.tools._direct_db import direct_connect
    c = direct_connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '10s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _build_map() -> dict:
    """Sort distinct live site_codes → {site_code: 'Shop N'}. Fail-soft to {}."""
    try:
        c, cur = _conn()
        try:
            cur.execute(
                f"SELECT DISTINCT site_code FROM {FLAT} "
                f"WHERE site_code IS NOT NULL AND site_code <> '' ORDER BY site_code")
            codes = [r[0] for r in cur.fetchall()]
            return {sc: f"Shop {i}" for i, sc in enumerate(codes, start=1)}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"shop label map build failed: {e}")
        return {}


def _map() -> dict:
    now = time.time()
    if (now - _CACHE["at"]) > _TTL or not _CACHE["map"]:
        m = _build_map()
        if m:                      # only refresh the cache on a non-empty build
            _CACHE["map"] = m
            _CACHE["at"] = now
    return _CACHE["map"]


def shop_label(site_code: str | None) -> str:
    """'Shop N' for a site_code, or the raw code if unknown/empty."""
    if not site_code:
        return ""
    return _map().get(str(site_code), str(site_code))


def label_entries(entries: list[dict], key: str = "site") -> list[dict]:
    """Add a 'shop' display label to each {site/site_code: ...} entry in a list.
    Mutates + returns the list. No-op on non-list / missing keys."""
    if not isinstance(entries, list):
        return entries
    for e in entries:
        if isinstance(e, dict):
            sc = e.get(key) or e.get("site") or e.get("site_code")
            if sc:
                e["shop"] = shop_label(sc)
    return entries
