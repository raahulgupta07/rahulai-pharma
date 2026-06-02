from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["full", "band", "mask", "hide"] = "full"
    bands: list[dict] = Field(default_factory=list)
    mask_with: str = "***"


class AudienceRules(BaseModel):
    model_config = ConfigDict(extra="ignore")
    fields: dict[str, FieldRule] = Field(default_factory=dict)


class VisibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int = 1
    private: AudienceRules = Field(default_factory=AudienceRules)
    network: AudienceRules = Field(default_factory=AudienceRules)
    public: AudienceRules = Field(default_factory=AudienceRules)
    applied_template: str | None = None
    applied_template_at: str | None = None
