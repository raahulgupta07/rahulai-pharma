"""API-gateway store scope — request-scoped ContextVar read by data tools.

The OpenAI-compatible gateway (`app/api_gateway.py`) authenticates a request via
an API key (`dash-key-*`). When that key is bound to a store, the gateway sets
API_STORE_SCOPE around the agent run. Data tools (`stock_check`,
`find_substitutes`, `alternatives_for_indication`, raw-SQL guard) read it to
enforce the three-tier access rule:

  Tier 1 — own store  (row.site_code == scope.store_id) → full data (qty, cost)
  Tier 2 — other store (row.site_code != scope.store_id) → product/availability
           only; stock_qty + price/cost columns are stripped
  Tier 3 — reference/global rows (no site_code)          → unrestricted

scope.mode:
  'store'  — enforce Tier-2 masking + disable raw SQL
  'global' — no mask (internal / super-admin keys, human UI sessions)

Fail-soft: when unset (e.g. the human web UI, where scoping is handled by the
existing SHOP CONTEXT injection), tools see `None` and apply no extra masking.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class StoreScope:
    store_id: str                       # primary bound store (first of the set)
    stores: Tuple[str, ...] = ()        # full owned-store SET (multi-outlet keys)
    mode: str = "store"                 # 'store' | 'global'

    @property
    def enforced(self) -> bool:
        """True when Tier-2 masking + raw-SQL lockdown should apply."""
        return self.mode == "store" and bool(self.store_id or self.stores)


# None = no API scope in effect (human UI session or unauthenticated path).
API_STORE_SCOPE: ContextVar[Optional[StoreScope]] = ContextVar(
    "API_STORE_SCOPE", default=None
)


def current_scope() -> Optional[StoreScope]:
    """Return the active store scope, or None when unset. Never raises."""
    try:
        return API_STORE_SCOPE.get()
    except Exception:
        return None


def is_store_locked() -> bool:
    """True when the active scope enforces Tier-2 masking / raw-SQL lockdown."""
    s = current_scope()
    return bool(s and s.enforced)


def owns(site_code: str | None) -> bool:
    """True if the given row's site_code belongs to the bound store SET (Tier 1),
    OR no scope is enforced (human UI). A null/empty site_code = Tier-3 global
    reference row → always visible (returns True)."""
    s = current_scope()
    if not (s and s.enforced):
        return True
    if not site_code:
        return True
    sc = str(site_code)
    if s.stores:
        return sc in s.stores
    return sc == str(s.store_id)


def bound_store() -> Optional[str]:
    """Primary bound store_id when masking is active, else None. Back-compat —
    multi-outlet callers should use bound_stores()."""
    s = current_scope()
    return s.store_id if (s and s.enforced) else None


def bound_stores() -> list[str]:
    """Full owned-store SET when masking is active, else []. For multi-outlet
    keys this is every store the key may see fully (Tier 1)."""
    s = current_scope()
    if not (s and s.enforced):
        return []
    if s.stores:
        return list(s.stores)
    return [s.store_id] if s.store_id else []


# Columns stripped from Tier-2 (other-branch) rows: quantities, cost, price,
# and any sales-value derived figures. Tier-1 own-branch + Tier-3 global keep them.
_SENSITIVE_KEYS = (
    "stock_qty", "your_stock", "qty", "weighted_cost_price", "cost",
    "price", "unit_price", "mrp", "retail_price", "sales_value", "value",
    "amount", "revenue", "total_stock_qty", "total_inventory_value",
)


def mask_row(row: dict, site_code: str | None) -> dict:
    """Belt-and-suspenders Tier-2 sanitizer. When store-locked and the row's
    site_code is NOT the bound store (and not a Tier-3 global row), null out
    quantity / cost / price / sales-value fields — existence/availability only.

    No-op when scope is unenforced (human UI) or the row is owned/global.
    Mutates + returns the dict for convenience."""
    if owns(site_code):
        return row
    for k in _SENSITIVE_KEYS:
        if k in row and row[k] is not None:
            row[k] = None
    return row
