"""Output guards — runtime checks on agent responses + MDL install payloads.

  number_cite      H3/H10 — every number in agent text must appear in a
                   tool_call output; flag fabrications.
  bounds           H12     — per-vcol bounds validator (min/max/nullable)
                            scans post-exec rows, logs anomalies.

All guards fail-soft: return advisory dicts, never raise. Caller decides
whether to surface (trace panel) or block (refusal path).
"""

from dash.guards.number_cite import audit_numbers
from dash.guards.bounds import check_bounds
from dash.guards.context import cap_tool_result, trim_stale_tool_results

__all__ = [
    "audit_numbers",
    "check_bounds",
    "cap_tool_result",
    "trim_stale_tool_results",
]
