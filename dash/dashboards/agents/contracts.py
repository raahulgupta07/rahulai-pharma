from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Any

class Finding(BaseModel):
    """Output of Scout. One discovered insight."""
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    headline: str = ""           # "Stockouts up 34% WoW"
    severity: Literal["high","medium","low"] = "medium"
    sql: str = ""                # evidence query
    data: list[dict] = Field(default_factory=list)  # query result rows
    cause_hypothesis: str = ""
    suggested_action: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    sql_shape: dict = Field(default_factory=dict)  # {x_col, y_col, n_cols, n_rows}

class DesignDecision(BaseModel):
    """Output of Designer. Maps Finding → cell."""
    model_config = ConfigDict(extra="ignore")
    finding_id: str = ""
    cell_type: Literal["kpi","chart","table","insight","network_grid"] = "kpi"
    chart_type: str | None = None  # line|bar|pie|scatter|area
    grid: list[int] = Field(default_factory=lambda: [0,0,3,2])
    palette_role: Literal["danger","warning","good","neutral","info"] = "neutral"
    title: str = ""
    headline_text: str = ""
    drill_into: list[str] = Field(default_factory=list)  # finding_ids
    config: dict = Field(default_factory=dict)  # x_col, y_col, sql, etc

class AgentEvent(BaseModel):
    """SSE event between orchestrator and frontend."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["scout_thinking","scout_finding","designer_thinking","designer_decision","cell_added","done","error"] = "scout_thinking"
    agent: Literal["scout","designer","orchestrator"] = "orchestrator"
    msg: str = ""
    data: dict | None = None
