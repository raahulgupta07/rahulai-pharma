"""Shared 3-state article resolver for the CityPharma pharma tools.

shop_flat is a FULL OUTER join of catalog × stock with a `link_status` column:
  'both'         — article in catalog AND has a stock row (brand/generic real,
                   stock_qty real, linked=true, site_code = a real site).
  'catalog_only' — article in catalog but NO stock anywhere (the COMMON case:
                   frozen / advance-entry / supplier-out). ONE row per article,
                   site_code='__none__', stock_qty=0, brand/generic POPULATED.
  'stock_only'   — stock row with NO catalog match (rare: delayed catalog
                   update). brand/generic NULL, linked=false, real site_code.

`resolve_article` is the SINGLE SOURCE of the "Not Found" wording so every tool
answers the product owner's rule the same way: "found → return info; not found →
Not Found." Catalog and stock are maintained by different ERP processes and do
NOT match 100% — that is EXPECTED, never an error. An article that is merely
`catalog_only` is IN the catalog with 0 stock; it is NOT "out of stock
everywhere".

Catalog presence is WHOLE-CHAIN: a store-locked key still sees that an article
EXISTS in the company catalog (only the stock QUANTITY is store-scoped/masked).
So when establishing catalog presence we INCLUDE the `__none__` placeholder rows;
when listing STOCK/availability we EXCLUDE them.

Dependency-light: takes an already-open psycopg cursor + schema name, mirroring
the connection style of `pharma_shop_tool` / `find_nearby_stock` (the caller owns
the connection). No new DB connection pattern.
"""
from __future__ import annotations

import re

# site_code placeholder used by catalog_only rows in shop_flat (no real site).
NONE_SITE = "__none__"

# Message strings — the SINGLE source of the "Not Found" wording.
MSG_CATALOG_ONLY = "In catalog but not currently stocked (0 on hand)."
MSG_STOCK_ONLY = "Stock found, but catalog details are missing for this code."
MSG_NOT_FOUND = "Not Found."

# A "bare code" query = mostly digits (article codes are numeric strings; Excel
# may mangle them but they're still digit-dominant). Used to also surface
# stock_only rows, which have a NULL brand so an ILIKE-by-name never finds them.
_CODE_RE = re.compile(r"^[\s\d.\-eE+]+$")


def _looks_like_code(query: str) -> bool:
    """True when `query` is a bare article code (digit-dominant), not a name."""
    q = (query or "").strip()
    if not q:
        return False
    if not _CODE_RE.match(q):
        return False
    # require at least 3 digits so a stray '5' or '-' isn't treated as a code
    return sum(c.isdigit() for c in q) >= 3


def resolve_article(cur, schema: str, query: str, *, sites=None) -> dict:
    """Resolve a free-text brand/generic OR a bare article code to a 3-state result.

    cur    — an OPEN psycopg cursor (caller owns the connection; search_path is
             expected to already include `schema`, as the pharma tools set it).
    schema — the data schema (e.g. 'citypharma').
    query  — brand name, generic/salt, partial name, OR a bare article code.
    sites  — optional list of site_codes to restrict the STOCK view to (e.g. a
             store-locked key's owned set). Catalog PRESENCE is always
             whole-chain (sites only narrows which real sites count as stocked).

    Returns:
      {"state": "both"|"catalog_only"|"stock_only"|"not_found",
       "art_keys": [...], "rows": [...], "message": <human string>}

    State precedence over all matched rows: any 'both' → 'both'; elif only
    'catalog_only' → 'catalog_only'; elif only 'stock_only' → 'stock_only'; else
    'not_found'. Fail-soft: a missing/NULL link_status (older pre-build row) is
    treated as 'both' so nothing breaks before the build rewrite lands.
    """
    q = (query or "").strip()
    if not q:
        return {"state": "not_found", "art_keys": [], "rows": [],
                "message": MSG_NOT_FOUND}

    flat = f'"{schema}".shop_flat'
    like = f"%{q}%"
    is_code = _looks_like_code(q)

    # Match by name (brand/generic ILIKE) and — when the query looks like a code —
    # by an exact art_key match too. The exact-code arm is what surfaces
    # stock_only rows (NULL brand → an ILIKE-by-name can never reach them).
    where = ["brand ILIKE %s", "generic ILIKE %s"]
    params: list = [like, like]
    if is_code:
        where.append("art_key = %s")
        params.append(q)
    where_sql = " OR ".join(where)

    # COALESCE so an older row with NULL link_status is treated as 'both'
    # (fail-soft, pre-build). One row per (art_key, site_code) in shop_flat.
    cur.execute(
        f"""SELECT art_key, site_code, brand, generic, composition, category,
                   stock_qty, cost, is_in_stock, linked,
                   COALESCE(NULLIF(link_status, ''), 'both') AS link_status
            FROM {flat}
            WHERE {where_sql}
            ORDER BY art_key, site_code""",
        tuple(params),
    )
    cols = ("art_key", "site_code", "brand", "generic", "composition",
            "category", "stock_qty", "cost", "is_in_stock", "linked",
            "link_status")
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    if not rows:
        return {"state": "not_found", "art_keys": [], "rows": [],
                "message": MSG_NOT_FOUND}

    # If a stock-site filter is supplied, keep catalog_only placeholder rows
    # (whole-chain catalog presence) but restrict REAL-site stock rows to the
    # owned set, so an out-of-set stock row doesn't masquerade as availability.
    if sites:
        allowed = set(str(s) for s in sites)
        rows = [
            r for r in rows
            if r["site_code"] == NONE_SITE or str(r["site_code"]) in allowed
        ]
        if not rows:
            return {"state": "not_found", "art_keys": [], "rows": [],
                    "message": MSG_NOT_FOUND}

    statuses = {r["link_status"] for r in rows}
    if "both" in statuses:
        state = "both"
    elif statuses == {"catalog_only"}:
        state = "catalog_only"
    elif statuses == {"stock_only"}:
        state = "stock_only"
    elif "catalog_only" in statuses:
        # mix of catalog_only + stock_only (different rows) but no 'both' — the
        # article IS in the catalog, so lead with catalog presence.
        state = "catalog_only"
    elif "stock_only" in statuses:
        state = "stock_only"
    else:
        state = "not_found"

    messages = {
        "both": "",
        "catalog_only": MSG_CATALOG_ONLY,
        "stock_only": MSG_STOCK_ONLY,
        "not_found": MSG_NOT_FOUND,
    }

    art_keys = list(dict.fromkeys(r["art_key"] for r in rows))  # dedup, ordered
    return {
        "state": state,
        "art_keys": art_keys,
        "rows": rows,
        "message": messages[state],
    }
