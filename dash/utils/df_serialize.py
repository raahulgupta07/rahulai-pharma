"""DataFrame / SQL-row row-to-JSON-safe-dict serializer.

Single helper used by `_run_rows` in verified_reward.py, future dashboard
renderers, and exports. Coerces every cell into a JSON-safe scalar so the
caller never has to think about Decimal/datetime/bytes/NaN/Inf.

USAGE:
    from dash.utils.df_serialize import df_rows_to_jsonable
    out = df_rows_to_jsonable(all_rows[:limit], cols)
    # out: list[dict] — JSON-safe; bad cells repr()'d, never raises.

Coercion rules (per cell):
    - Decimal → int if integral, else float
    - datetime / date / time → isoformat() string
    - bytes / bytearray → utf-8 decode (errors='replace')
    - float NaN / Inf → None (JSON-spec / FastAPI reject NaN)
    - anything else → passthrough (None, int, float, str, bool, list, dict)

Fail-soft: a bad cell (raises during coercion) → repr(cell). Function
NEVER raises on a row; whole-row exception → returns row with all values
as repr() so caller still gets a usable dict. Bad column-list lengths
are tolerated (zip-stop on shortest).
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterable, Sequence


def _coerce_cell(cell: Any) -> Any:
    """Single cell → JSON-safe scalar. Never raises (returns repr on error)."""
    try:
        if cell is None:
            return None

        # Decimal — PG SUM/AVG/numeric reads, most common.
        if isinstance(cell, Decimal):
            try:
                if cell == cell.to_integral_value():
                    return int(cell)
            except Exception:
                pass
            return float(cell)

        # datetime / date / time — PG TIMESTAMP/DATE/TIME columns.
        if isinstance(cell, (datetime, date, time)):
            return cell.isoformat()

        # bytes — PG bytea columns, file payloads.
        if isinstance(cell, (bytes, bytearray)):
            return cell.decode("utf-8", errors="replace")

        # float NaN / Inf — must become None (FastAPI/JSON spec).
        if isinstance(cell, float):
            if math.isnan(cell) or math.isinf(cell):
                return None
            return cell

        # int / bool / str — already JSON-safe.
        if isinstance(cell, (int, bool, str)):
            return cell

        # numpy scalars (np.int64, np.float64) — .item() gives native type.
        if hasattr(cell, "item") and callable(cell.item):
            try:
                v = cell.item()
                # Re-check NaN/Inf on the unwrapped float.
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return None
                return v
            except Exception:
                pass

        # Anything with isoformat (pendulum/arrow/etc).
        if hasattr(cell, "isoformat") and callable(cell.isoformat):
            try:
                return cell.isoformat()
            except Exception:
                pass

        # Last resort — repr(). Don't raise.
        return repr(cell)
    except Exception:  # noqa: BLE001 — fail-soft
        try:
            return repr(cell)
        except Exception:
            return None


def df_rows_to_jsonable(
    rows: Iterable[Sequence[Any]] | None,
    cols: Sequence[str] | None,
) -> list[dict]:
    """Convert SQL row tuples (or pandas itertuples) into a list of JSON-safe dicts.

    Args:
        rows: iterable of row-like sequences (SQL Row, tuple, list, pandas tuple).
        cols: column names (same order as row cells).

    Returns:
        list[dict] — never raises. Bad cell → repr(cell). Empty input → [].

    Behavior:
        - If rows or cols is None / empty → returns [].
        - zip(row, cols) stops at shortest — extra cells dropped, missing cells
          omitted from dict (caller can handle absence).
    """
    if not rows or not cols:
        return []
    out: list[dict] = []
    cols_list = list(cols)
    for row in rows:
        try:
            d: dict = {}
            for col_name, cell in zip(cols_list, row):
                d[col_name] = _coerce_cell(cell)
            out.append(d)
        except Exception:  # noqa: BLE001 — whole-row failure, still emit something
            try:
                d = {c: repr(getattr(row, c, None)) for c in cols_list}
            except Exception:
                d = {c: None for c in cols_list}
            out.append(d)
    return out
