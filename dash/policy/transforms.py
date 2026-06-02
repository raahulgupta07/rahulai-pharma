from __future__ import annotations


def _sql_str(val: str) -> str:
    # single-quote escape for SQL string literal
    return "'" + str(val).replace("'", "''") + "'"


def band_expr(col: str, bands: list[dict]) -> str:
    # Build CASE chain. Each band: {"name": str, "max": number}. Bands with no max
    # become the ELSE bucket. Sorted ascending by max so chain is deterministic.
    if not bands:
        return f"{col} AS {col}"
    bounded = [b for b in bands if b.get("max") is not None]
    unbounded = [b for b in bands if b.get("max") is None]
    bounded_sorted = sorted(bounded, key=lambda b: b["max"])
    parts = ["CASE"]
    for b in bounded_sorted:
        name = b.get("name", "band")
        parts.append(f"WHEN {col} <= {b['max']} THEN {_sql_str(name)}")
    else_name = unbounded[0].get("name", "high") if unbounded else "high"
    parts.append(f"ELSE {_sql_str(else_name)} END AS {col}")
    return " ".join(parts)


def mask_expr(col: str, with_val: str) -> str:
    return f"{_sql_str(with_val)} AS {col}"


def hide_expr(col: str) -> None:
    return None
