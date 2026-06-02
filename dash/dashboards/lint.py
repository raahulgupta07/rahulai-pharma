"""Lint rules for DashboardSpec."""
from __future__ import annotations

from .spec import DashboardSpec

PALETTE = ["#2e7d32", "#e65100", "#1976d2", "#c62828", "#666", "#1a1a1a"]


def _overlap(a: list[int], b: list[int]) -> bool:
    ax, ay, aw, ah = a[0], a[1], a[2], a[3]
    bx, by, bw, bh = b[0], b[1], b[2], b[3]
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def lint_spec(spec: DashboardSpec) -> list[str]:
    warnings: list[str] = []

    if not spec.cells:
        warnings.append("REJECT: empty cells list")
        return warnings

    if len(spec.cells) > 20:
        warnings.append(f"WARN: {len(spec.cells)} cells (>20 max)")

    kpi_count = sum(1 for c in spec.cells if c.type == "kpi")
    if kpi_count > 6:
        warnings.append(f"WARN: {kpi_count} KPI cells (>6 is too many)")

    for c in spec.cells:
        cfg = c.config or {}
        chart_type = cfg.get("chart_type") or cfg.get("type")
        if chart_type == "pie":
            slices = cfg.get("slices") or cfg.get("data") or []
            if isinstance(slices, list) and len(slices) > 7:
                warnings.append(f"REJECT: pie chart '{c.id}' has {len(slices)} slices (>7)")
        if cfg.get("dual_axis") or chart_type == "dual_axis":
            warnings.append(f"WARN: cell '{c.id}' uses dual-axis chart")
        for col in cfg.get("colors", []) or []:
            if col not in PALETTE:
                warnings.append(f"WARN: cell '{c.id}' color {col} not in palette")

    cells = spec.cells
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            if _overlap(cells[i].grid, cells[j].grid):
                warnings.append(f"WARN: cells '{cells[i].id}' and '{cells[j].id}' overlap")

    return warnings
