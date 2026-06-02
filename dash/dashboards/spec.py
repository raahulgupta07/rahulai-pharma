"""Dashboard spec models.

Phase-0 (legacy): Cell / Filter / DashboardSpec — backwards-compat.

Phase-1 (Deep Dash 9-stage): DashboardIntent / SchemaContext / PanelPlan /
PanelSQL / PanelData / EChartsPanelSpec / Critique / DeepDashSpec / JsonPatchOp.
Pydantic typed contract — validated at every stage. Different-model judge.
JSON Patch for panel-level iteration.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Cell(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    type: str = "kpi"
    grid: list[int] = Field(default_factory=lambda: [0, 0, 3, 2])
    title: str = ""
    config: dict = Field(default_factory=dict)


class Filter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    col: str = ""
    type: str = "single"
    default: Any = None


class DashboardSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    title: str = "Dashboard"
    project_slug: str = ""
    persona: str = ""
    filters: list[Filter] = Field(default_factory=list)
    cells: list[Cell] = Field(default_factory=list)
    theme: str = "light"
    template: str = "executive"
    insights: list[dict] = Field(default_factory=list)
    is_public: bool = False
    share_token: str = ""
    refresh_cron: str = ""
    compare_to: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# Phase-1: Deep Dash 9-stage models
# ============================================================

PanelType = Literal["kpi", "chart", "table", "insight", "narrative"]
ChartType = Literal[
    "bar", "line", "pie", "scatter", "area", "grouped_bar",
    "stacked_bar", "histogram", "heatmap", "gauge", "sankey",
    "treemap", "funnel", "boxplot", "radar", "candlestick",
]
Severity = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


# Stage 1 — intent
class DashboardIntent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str = ""
    audience: Literal["executive", "analyst", "operator", "general"] = "executive"
    n_panels_target: int = Field(default=8, ge=1, le=20)
    time_window: str = ""           # "last 90 days", "Q3 2026"
    domain_hints: list[str] = Field(default_factory=list)
    is_edit: bool = False           # True → JSON Patch path, not full rebuild
    target_panel_id: str | None = None  # set when is_edit


# Stage 2 — schema RAG (pgvector top-k tables + sample rows + MDL)
class TableContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    purpose: str = ""
    columns: list[dict] = Field(default_factory=list)  # [{name, dtype, semantic}]
    sample_rows: list[dict] = Field(default_factory=list)
    row_count: int | None = None


class SchemaContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tables: list[TableContext] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)


# Stage 3 — panel plan (per-panel intent + chart-type recommendation)
class PanelPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    question: str                    # natural-language sub-question
    panel_type: PanelType = "chart"
    chart_type: ChartType | None = None  # recommendation, refined at stage 7
    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    tables_used: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=0, le=100)


# Stage 4 — SQL per panel
class PanelSQL(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panel_id: str
    sql: str
    explain_cost: float | None = None  # set after EXPLAIN gate
    explain_passed: bool = False
    explain_error: str | None = None


# Stage 6 — executed data + profile
class PanelData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panel_id: str
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    columns: list[dict] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)  # cardinality, nulls, dtypes
    exec_ms: int = 0


# Stage 7 — chart spec (ECharts options JSON, validated subset)
class EChartsPanelSpec(BaseModel):
    """ECharts options envelope. extra='allow' for full option flexibility,
    but core fields typed for validator. Use ECharts 5.5+ option keys only."""
    model_config = ConfigDict(extra="allow")
    panel_id: str
    chart_type: ChartType
    title: str = ""
    # ECharts options pass-through (validated externally via JSON schema if needed)
    options: dict = Field(default_factory=dict)
    grid: list[int] = Field(default_factory=lambda: [0, 0, 6, 3])  # [x,y,w,h] in 12-col grid
    narrative: str = ""
    confidence: Confidence = "medium"
    sources: list[str] = Field(default_factory=list)  # table names


# Stage 8 — critique (different-model judge)
class CritiqueIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    panel_id: str
    severity: Severity
    kind: Literal[
        "chart_type_mismatch", "axis_sanity", "color_a11y",
        "redundancy", "missing_label", "encoding_dtype_mismatch",
        "low_signal", "misleading",
    ]
    detail: str = ""
    suggested_patch: list["JsonPatchOp"] = Field(default_factory=list)


class Critique(BaseModel):
    model_config = ConfigDict(extra="ignore")
    issues: list[CritiqueIssue] = Field(default_factory=list)
    overall_score: int = Field(default=50, ge=0, le=100)
    judge_model: str = ""
    gen_model: str = ""


# JSON Patch (RFC 6902) — universal edit protocol
class JsonPatchOp(BaseModel):
    model_config = ConfigDict(extra="ignore")
    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str                        # e.g. /panels/3/options/title/text
    value: Any = None
    from_: str | None = Field(default=None, alias="from")


# Stage 9 — final dashboard spec (deep dash output)
class DeepDashSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    project_slug: str = ""
    title: str = "Dashboard"
    intent: DashboardIntent
    panels: list[EChartsPanelSpec] = Field(default_factory=list)
    layout: Literal["executive", "operational", "comparison", "narrative"] = "executive"
    grid_cols: int = 12
    filters: list[Filter] = Field(default_factory=list)
    persona: str = ""
    audience: str = "executive"
    refresh_cron: str | None = None
    judge_score: int | None = None
    spec_version: int = 1                # bumps on each JSON Patch apply
    created_at: datetime = Field(default_factory=datetime.utcnow)


CritiqueIssue.model_rebuild()
