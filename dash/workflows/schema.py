"""Workflow spec pydantic models.

Spec shape:
{
  "steps": [
    {
      "id": "fetch_data",
      "kind": "agent",                  // 'agent' | 'tool' | 'router' | 'parallel' | 'loop' | 'hitl'
      "agent": "Analyst",               // for kind='agent'
      "tool": "make_pdf",               // for kind='tool'
      "prompt": "...",                  // jinja-style {var} substitution from prior outputs
      "args": {...},                    // for kind='tool'
      "depends_on": ["step_id"],
      "parallel_group": "gather",       // run concurrently w/ siblings sharing same group
      "loop_until": "expr",             // python expr eval against ctx, kind='loop' only
      "max_iter": 3,                    // loop cap
      "route_by": "expr",               // kind='router' — eval expr → branch name
      "branches": {"high": ["step_id1"], "low": ["step_id2"]},
      "condition": "expr",              // skip step if False
      "on_error": "fail" | "continue" | "retry",
      "retry_max": 2,
      "hitl_action": "confirmation"     // kind='hitl' — pauses for human approval
    }
  ],
  "inputs": {"key": "default_value"},
  "outputs": ["final_step_id"]
}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except Exception:
    HAS_PYDANTIC = False
    BaseModel = object  # type: ignore
    Field = lambda *a, **k: None  # type: ignore


if HAS_PYDANTIC:
    class StepSpec(BaseModel):
        id: str
        kind: str = "agent"
        agent: Optional[str] = None
        tool: Optional[str] = None
        prompt: Optional[str] = None
        args: Optional[Dict[str, Any]] = None
        depends_on: List[str] = []
        parallel_group: Optional[str] = None
        loop_until: Optional[str] = None
        max_iter: int = 3
        route_by: Optional[str] = None
        branches: Optional[Dict[str, List[str]]] = None
        condition: Optional[str] = None
        on_error: str = "fail"  # 'fail' | 'continue' | 'retry'
        retry_max: int = 2
        hitl_action: Optional[str] = None

        class Config:
            extra = "ignore"

    class WorkflowSpec(BaseModel):
        steps: List[StepSpec]
        inputs: Dict[str, Any] = {}
        outputs: List[str] = []

        class Config:
            extra = "ignore"


def validate(spec_dict: Dict[str, Any]) -> tuple[bool, List[str]]:
    """Returns (ok, errors)."""
    errors = []
    if not isinstance(spec_dict, dict):
        return False, ["spec must be dict"]
    steps = spec_dict.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return False, ["steps must be non-empty list"]
    ids = set()
    for s in steps:
        sid = s.get("id")
        if not sid:
            errors.append("step missing id")
            continue
        if sid in ids:
            errors.append(f"duplicate step id: {sid}")
        ids.add(sid)
        kind = s.get("kind", "agent")
        if kind not in ("agent", "tool", "router", "parallel", "loop", "hitl"):
            errors.append(f"step {sid}: invalid kind '{kind}'")
        if kind == "agent" and not s.get("agent"):
            errors.append(f"step {sid}: kind=agent requires 'agent' field")
        if kind == "tool" and not s.get("tool"):
            errors.append(f"step {sid}: kind=tool requires 'tool' field")
        if kind == "router" and not (s.get("route_by") and s.get("branches")):
            errors.append(f"step {sid}: kind=router requires route_by + branches")
        if kind == "loop" and not s.get("loop_until"):
            errors.append(f"step {sid}: kind=loop requires loop_until")
        for d in s.get("depends_on", []):
            if d not in ids and d not in [x.get("id") for x in steps]:
                errors.append(f"step {sid}: depends_on unknown step '{d}'")
    return (len(errors) == 0), errors
