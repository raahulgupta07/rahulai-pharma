"""Universal JSON serializer — handles every database/numeric/datetime type
that breaks raw `json.dumps()`. Single source of truth.

Eliminates entire bug class: "TypeError: Object of type X is not JSON
serializable" where X ∈ {Decimal, UUID, datetime, date, bytes, set, numpy types}.

USAGE:
    from dash.utils import safe_dumps
    payload = safe_dumps({"value": Decimal("1.5"), "ts": datetime.now()})

PR RULE: every new SSE generator, every external API response, every JSONB
write to dash_* tables MUST use safe_dumps. Lint rule will reject raw
json.dumps(...) in app/ + dash/ over time.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import Any


def _default(obj: Any) -> Any:
    """Single dispatcher for non-stdlib-json types. Order matters: most-common
    first (Decimal from PG sums, datetime from PG timestamps)."""
    # PostgreSQL Decimal (SUM/AVG/numeric column reads) — most common offender.
    if isinstance(obj, Decimal):
        # Keep integers as int (cleaner KPI rendering), else float.
        try:
            if obj == obj.to_integral_value():
                return int(obj)
        except Exception:
            pass
        return float(obj)

    # datetime / date / time — PG TIMESTAMP/DATE columns.
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    # UUID — PG uuid columns, agno session IDs.
    if isinstance(obj, UUID):
        return str(obj)

    # bytes/bytearray — bytea columns, file uploads.
    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return repr(obj)

    # set/frozenset — Python sets aren't JSON.
    if isinstance(obj, (set, frozenset)):
        return list(obj)

    # numpy scalars (np.int64, np.float64, etc) — pandas-derived rows.
    # .item() returns native Python type. hasattr check avoids hard numpy dep.
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return obj.item()
        except Exception:
            pass

    # numpy arrays / pandas Series → list (best-effort).
    if hasattr(obj, "tolist") and callable(obj.tolist):
        try:
            return obj.tolist()
        except Exception:
            pass

    # Anything with isoformat() (pendulum, arrow, custom datetime subclasses).
    if hasattr(obj, "isoformat") and callable(obj.isoformat):
        try:
            return obj.isoformat()
        except Exception:
            pass

    # Final fallback — str(obj) NEVER raises but loses fidelity. Log it.
    import logging
    logging.getLogger(__name__).debug(
        "safe_json fallback to str() for type %s: %r", type(obj).__name__, obj
    )
    return str(obj)


def _sanitize_floats(obj: Any) -> Any:
    """Recursively replace NaN/Inf with None. FastAPI/JSON-spec reject them.
    Walks dict/list/tuple; leaves scalars alone unless they're problem floats.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_floats(v) for v in obj]
    return obj


def safe_dumps(obj: Any, *, sanitize_nan: bool = True, **kwargs) -> str:
    """JSON-encode any object. Handles Decimal/UUID/datetime/numpy/bytes/set.

    Args:
        obj: anything JSON-encodable after type coercion.
        sanitize_nan: replace NaN/Inf with None (default True). FastAPI default
                      json serializer rejects NaN, so safer to strip.
        **kwargs: passed to json.dumps (e.g. indent=2).

    Returns:
        JSON string. NEVER raises TypeError on serialization. Falls back to
        str(obj) for unknown types as last resort.
    """
    if sanitize_nan:
        obj = _sanitize_floats(obj)
    return json.dumps(obj, default=_default, **kwargs)


def safe_loads(s: str | bytes) -> Any:
    """Pass-through json.loads w/ same name for symmetry. NaN/Inf parsing left
    to caller; PostgreSQL JSONB doesn't store NaN so usually moot."""
    return json.loads(s)


# Lightweight self-test: importing this module shouldn't import numpy/pandas.
# Verified by `hasattr(obj, 'item')` checks (no isinstance against numpy types).
