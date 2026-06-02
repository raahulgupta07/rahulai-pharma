"""AgentTemplate Pydantic models. Lenient (extra=ignore) for forward-compat."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ExpectedColumn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    aliases: list[str] = Field(default_factory=list)
    dtype_hint: str | None = None  # numeric | text | date | bool
    required: bool = False
    description: str = ""


class ExpectedEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str  # logical entity name, e.g., "drug", "store"
    aliases: list[str] = Field(default_factory=list)  # alt table names: meds, products
    columns: list[ExpectedColumn] = Field(default_factory=list)
    description: str = ""


class EntityRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")
    from_entity: str
    relation: str  # owns | belongs_to | references | contains
    to_entity: str


class KPISpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    formula: str  # uses {entity.column} placeholders
    expected_entity: str
    expected_columns: list[str] = Field(default_factory=list)
    freq: str = "daily"  # hourly | daily | weekly | monthly
    description: str = ""


class AutonomousWorkflow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    schedule: str = "daily"  # cron-like or hourly|daily|weekly
    query_template: str = ""  # SQL with {entity.column} placeholders
    expected_entity: str = ""
    expected_columns: list[str] = Field(default_factory=list)
    trigger_threshold: str = "any rows"
    action: str = "post_insight"  # post_insight | alert | suggest | log
    fallback_action: str = "log_unbound"


class FeatureToggles(BaseModel):
    model_config = ConfigDict(extra="ignore")
    visibility: bool = False
    rls: bool = False
    embed: bool = False
    ml: bool = True
    dashboards: bool = True
    research: bool = True
    autonomous: bool = False


class AgentTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # ── identity ──
    name: str  # unique slug e.g. "pharmacy_network"
    label: str
    icon: str = "📦"
    category: str = "general"  # analytics | multi_tenant | customer_facing | research | data_eng | starter
    description: str = ""

    # ── ontology (schema-independent on apply) ──
    entities: list[ExpectedEntity] = Field(default_factory=list)
    relationships: list[EntityRelationship] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)  # term → definition
    aliases: dict[str, list[str]] = Field(default_factory=dict)  # canonical → alt names
    formulas: dict[str, str] = Field(default_factory=dict)  # name → text formula

    # ── pending until reconcile ──
    kpis: list[KPISpec] = Field(default_factory=list)
    autonomous_workflows: list[AutonomousWorkflow] = Field(default_factory=list)

    # ── persona + UX ──
    default_persona: str = ""
    learning_focus: list[str] = Field(default_factory=list)
    proactive_questions: list[str] = Field(default_factory=list)
    sample_evals: list[dict] = Field(default_factory=list)

    # ── feature config ──
    features: FeatureToggles = Field(default_factory=FeatureToggles)
    visibility_template: str | None = None  # ref to dash.policy.templates name
    scope_keyword: str = ""  # store | property | branch | unit
    feature_config_preset: str | None = None  # ref to feature_config preset
    # Deep-merge into dash_projects.feature_config on apply (e.g. enable
    # vertical-specific agent gates like {"agents":{"investment":true}}).
    feature_config_override: dict | None = None
    # If detected_confidence >= this threshold, upload pipeline silently
    # auto-applies the template. None = manual suggestion only.
    auto_apply_above_confidence: float | None = None  # 0.0-1.0, None = manual only

    # ── starter content ──
    suggested_roles: list[dict] = Field(default_factory=list)
    sample_dashboard: dict | None = None
