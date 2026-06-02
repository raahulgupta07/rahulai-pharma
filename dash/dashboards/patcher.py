"""Phase-3 chat-driven JSON Patch editing for dashboard specs (RFC 6902 subset)."""
from __future__ import annotations

import copy
import json
import logging
import re

from dash.dashboards.spec import DashboardSpec

logger = logging.getLogger(__name__)


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\[.*\]", s, re.DOTALL)
    if m:
        return m.group(0)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    return m.group(0) if m else s


def _parse_json_robust(raw: str):
    text = (raw or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # try array first, then object
    for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
        m = re.search(pattern, text)
        if m:
            candidate = m.group(0)
            try:
                return json.loads(candidate)
            except Exception:
                fixed = re.sub(r',(\s*[}\]])', r'\1', candidate)
                fixed = re.sub(r'//.*?$', '', fixed, flags=re.MULTILINE)
                fixed = re.sub(r'/\*[\s\S]*?\*/', '', fixed)
                try:
                    return json.loads(fixed)
                except Exception:
                    pass
    try:
        from json_repair import repair_json
        return json.loads(repair_json(text))
    except Exception:
        pass
    return None


_PROMPT = """You are DASHBOARD-PATCHER. Edit a dashboard spec via JSON Patch (RFC 6902).

CURRENT SPEC:
{spec}

USER INSTRUCTION: {prompt}

Output ONLY a JSON object (no fences) with EXACT shape:
{{"ops": [ {{"op": "replace|add|remove", "path": "/cells/N/...", "value": ...}} ],
 "rationale": "<one short sentence>"}}

Rules:
- Allowed ops: replace, add, remove only.
- Paths use JSON Pointer (e.g. /cells/0/title, /cells/2/config/color, /title).
- Cell ids are stable; reference cells by index in /cells/N/.
- For "add" to an array end, use path ending /-.
- Prefer minimal ops. Do not rewrite the whole spec.
"""


def _resolve(obj, tokens: list[str]):
    cur = obj
    for tok in tokens:
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(tok)]
        else:
            cur = cur[tok]
    return cur


def _set(obj, tokens: list[str], value, op: str):
    if not tokens:
        raise ValueError("cannot operate on root")
    parent = _resolve(obj, tokens[:-1])
    last = tokens[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(parent, list):
        if op == "add":
            if last == "-":
                parent.append(value)
            else:
                parent.insert(int(last), value)
        elif op == "replace":
            parent[int(last)] = value
        elif op == "remove":
            parent.pop(int(last))
    else:
        if op == "remove":
            parent.pop(last, None)
        else:
            parent[last] = value


def apply_patch(spec: dict, ops: list[dict]) -> dict:
    """Pure: apply RFC 6902 ops (replace/add/remove) to deep copy. Validate via DashboardSpec."""
    new_spec = copy.deepcopy(spec)
    for op_obj in ops or []:
        op = op_obj.get("op")
        path = op_obj.get("path", "")
        if op not in ("replace", "add", "remove"):
            raise ValueError(f"unsupported op: {op}")
        tokens = [t for t in path.split("/")[1:]] if path.startswith("/") else []
        if op == "remove":
            _set(new_spec, tokens, None, "remove")
        else:
            _set(new_spec, tokens, op_obj.get("value"), op)
    DashboardSpec(**new_spec)
    return new_spec


def llm_patch(spec: dict, prompt: str) -> dict:
    """Call cheap LLM, return {ops, rationale} or {error}."""
    try:
        from dash.settings import training_llm_call
        raw = training_llm_call(
            _PROMPT.format(spec=json.dumps(spec)[:6000], prompt=prompt[:500]),
            task="extraction",
        )
    except Exception as e:
        return {"error": f"LLM call failed: {e}"}
    if not raw:
        return {"error": "empty LLM response"}
    parsed = _parse_json_robust(_strip_fences(raw))
    if parsed is None:
        return {"error": f"parse failed: {raw[:200]}"}
    if isinstance(parsed, list):
        parsed = {"ops": parsed, "rationale": ""}
    ops = parsed.get("ops") or []
    if not isinstance(ops, list):
        return {"error": "ops must be a list"}
    try:
        apply_patch(spec, ops)
    except Exception as e:
        return {"error": f"validate failed: {e}"}
    return {"ops": ops, "rationale": str(parsed.get("rationale", ""))[:300]}
