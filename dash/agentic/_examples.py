"""
HITL example tools — demonstrate the three decorator patterns.

These are NOT wired into any agent; they exist as documentation for tool
authors and as smoke targets for tests.
"""
from __future__ import annotations

from typing import Any, Dict

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore
    def Field(*args, **kwargs):  # type: ignore
        return None

from dash.agentic.hitl import (
    require_confirmation,
    require_user_input,
    external_execution,
)


# ── 1. require_confirmation example ─────────────────────────────────────
@require_confirmation(
    "create_view",
    "Engineer wants to create view {name}",
)
def safe_create_view(name: str, sql: str) -> Dict[str, Any]:
    """Create a database view after human approval."""
    # Pretend we run CREATE VIEW <name> AS <sql>
    return {"ok": True, "view": name, "ddl": f"CREATE VIEW {name} AS {sql}"}


# ── 2. require_user_input example ───────────────────────────────────────
class SegmentChoice(BaseModel):
    name: str = Field(..., description="Segment label, e.g. 'Champions'")
    discount: float = Field(..., ge=0.0, le=100.0, description="Discount percent (0-100)")


@require_user_input(SegmentChoice)
def ask_user_segment(parsed: SegmentChoice, prompt: str) -> Dict[str, Any]:
    """Prompt the operator to pick a segment + discount."""
    return {
        "ok": True,
        "prompt": prompt,
        "segment": getattr(parsed, "name", None) if parsed is not None else None,
        "discount": getattr(parsed, "discount", None) if parsed is not None else None,
    }


# ── 3. external_execution example ───────────────────────────────────────
def _deploy_manifest(model_id: str) -> Dict[str, Any]:
    return {
        "kind": "deploy_model",
        "model_id": model_id,
        "instructions": (
            "Execute this deploy via your CI pipeline and POST the result to "
            "/api/hitl/{run_id}/external-result with {result: {...}}."
        ),
    }


@external_execution(_deploy_manifest)
def external_deploy_model(model_id: str) -> Dict[str, Any]:
    """Hand off model deploy to an external CI executor."""
    # Body is never invoked when EXPERIMENTAL_AGI=1; kept as a fallback.
    return {"ok": True, "model_id": model_id, "via": "fallback-inproc"}
