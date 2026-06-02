"""
Action Tools — Phase 2 internal action execution via HITL gate
================================================================

Two Agno @tool functions wired onto the Engineer agent (gated by feature flag).

1. ``request_action(action_name, payload_json, reason)``
   Look up a named action in ``public.dash_action_registry``, render its
   url/headers/body templates from the payload, and insert a pending row in
   ``public.dash_hitl_requests`` (operation='action_exec'). Returns the
   request_id so the operator can approve or reject it through the existing
   HITL flow.

2. ``execute_approved_action(request_id)``
   Re-read the HITL row, verify it has state='approved' and
   operation='action_exec', then fire the actual HTTP request (httpx, 15s
   timeout). On success, mark the row 'executed' with timestamp. Audited.

Design notes
------------
- Writes to ``public.dash_*`` go through ``db.session.get_write_engine()``
  (the read-only listener on ``get_sql_engine()`` would block them).
- JSONB parameters use ``CAST(:x AS jsonb)`` — PgBouncer + SQLAlchemy
  named-param collision rule.
- Template rendering is simple ``{{var}}`` substitution (no jinja
  dependency). Missing keys raise a clear error.
- ``project_id`` is resolved from the skill_refinery ``_CTX_PROJECT``
  ContextVar (project_slug) — translated to numeric id at lookup time. If
  not set, callers can pass it explicitly via the payload under
  ``__project_id``.
- Fail-soft: every tool returns a JSON-string envelope; exceptions never
  propagate.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, Optional

import httpx
from agno.tools import tool
from sqlalchemy import text

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


# ── Internal helpers ───────────────────────────────────────────────────────

def _write_engine():
    """Return write-capable engine for public.dash_* tables."""
    from db.session import get_write_engine
    return get_write_engine()


def _current_project_slug() -> Optional[str]:
    """Read project slug from skill_refinery ContextVar (set by chat hook)."""
    try:
        from dash.tools.skill_refinery import _CTX_PROJECT
        return _CTX_PROJECT.get()
    except Exception:
        return None


def _resolve_project_id(slug: Optional[str]) -> Optional[int]:
    """Translate project_slug → numeric dash_projects.id. None if not found."""
    if not slug:
        return None
    try:
        eng = _write_engine()
        with eng.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM public.dash_projects WHERE slug = :s LIMIT 1"),
                {"s": slug},
            ).first()
        return int(row[0]) if row else None
    except Exception as e:
        logger.debug("resolve_project_id failed for slug=%s: %s", slug, e)
        return None


def _render_template(template: str, payload: Dict[str, Any]) -> str:
    """Substitute {{var}} placeholders with payload values. Stringify values."""
    def _sub(match):
        key = match.group(1)
        if key not in payload:
            raise KeyError(f"missing payload key: {key}")
        return str(payload[key])
    return _PLACEHOLDER_RE.sub(_sub, template)


def _render_jsonb(template: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Render a JSONB template — string leaves get placeholder substitution,
    other types pass through. Recurses into dicts and lists.
    """
    if isinstance(template, str):
        try:
            return _render_template(template, payload)
        except KeyError:
            raise
    if isinstance(template, dict):
        return {k: _render_jsonb(v, payload) for k, v in template.items()}
    if isinstance(template, list):
        return [_render_jsonb(v, payload) for v in template]
    return template


def _lookup_action(project_id: Optional[int], name: str) -> Optional[Dict[str, Any]]:
    """Find an enabled action by (project_id, name). Falls back to NULL
    project_id (global action) when project-scoped lookup misses."""
    eng = _write_engine()
    with eng.connect() as conn:
        # Project-scoped first
        if project_id is not None:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, name, method, url_template,
                           header_template, body_template,
                           requires_approval, min_approvals, enabled
                      FROM public.dash_action_registry
                     WHERE project_id = :pid AND name = :n AND enabled = TRUE
                     LIMIT 1
                    """
                ),
                {"pid": project_id, "n": name},
            ).mappings().first()
            if row:
                return dict(row)
        # Global fallback (project_id IS NULL)
        row = conn.execute(
            text(
                """
                SELECT id, project_id, name, method, url_template,
                       header_template, body_template,
                       requires_approval, min_approvals, enabled
                  FROM public.dash_action_registry
                 WHERE project_id IS NULL AND name = :n AND enabled = TRUE
                 LIMIT 1
                """
            ),
            {"n": name},
        ).mappings().first()
        return dict(row) if row else None


# ── Tools ──────────────────────────────────────────────────────────────────

@tool(
    name="request_action",
    description=(
        "Request an internal action (HTTP webhook/API call) defined in the "
        "action registry. The action will be queued for human approval via "
        "the HITL system. Returns the request_id to track approval status. "
        "Args: action_name (str — name in dash_action_registry), payload_json "
        "(JSON string with template variables), reason (str — why this "
        "action is needed)."
    ),
)
def request_action(action_name: str, payload_json: str, reason: str) -> str:
    """Look up action, render templates, insert pending HITL request.

    Returns JSON string with {request_id, status, url, body, msg} on success
    or {ok: False, error} on failure.
    """
    try:
        # 1. Parse payload
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"invalid payload_json: {e}"})

        # 2. Resolve project context
        slug = _current_project_slug()
        project_id = _resolve_project_id(slug)
        # Allow override via payload (for explicit cross-project calls)
        if "__project_id" in payload:
            try:
                project_id = int(payload.pop("__project_id"))
            except (ValueError, TypeError):
                pass

        # 3. Look up action
        action = _lookup_action(project_id, action_name)
        if not action:
            return json.dumps({
                "ok": False,
                "error": f"action '{action_name}' not found or disabled "
                         f"(project_id={project_id})",
            })

        # 4. Render templates
        try:
            rendered_url = _render_template(action["url_template"], payload)
            rendered_headers = _render_jsonb(action["header_template"] or {}, payload)
            rendered_body = _render_jsonb(action["body_template"] or {}, payload)
        except KeyError as e:
            return json.dumps({"ok": False, "error": f"template render: {e}"})

        # 5. Insert HITL request (state='pending')
        details = {
            "request_type": "action_exec",
            "action_id": int(action["id"]),
            "action_name": action["name"],
            "method": action["method"],
            "rendered_url": rendered_url,
            "rendered_headers": rendered_headers,
            "rendered_body": rendered_body,
            "reason": reason,
            "payload": payload,
        }
        rid = f"act_{int(time.time() * 1000):x}"
        eng = _write_engine()
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO public.dash_hitl_requests
                      (request_id, project_slug, agent_name, operation,
                       details, state, requested_by)
                    VALUES
                      (:rid, :ps, :agent, 'action_exec',
                       CAST(:details AS jsonb), 'pending', :rb)
                    RETURNING id
                    """
                ),
                {
                    "rid": rid,
                    "ps": slug or "__global__",
                    "agent": "Engineer",
                    "details": json.dumps(details),
                    "rb": "agent",
                },
            ).first()
        request_id = int(row[0]) if row else None

        logger.info(
            "request_action: queued action='%s' request_id=%s rid=%s project=%s",
            action_name, request_id, rid, slug,
        )

        return json.dumps({
            "ok": True,
            "request_id": request_id,
            "request_uid": rid,
            "status": "pending",
            "action_name": action_name,
            "method": action["method"],
            "url": rendered_url,
            "body": rendered_body,
            "requires_approval": bool(action["requires_approval"]),
            "msg": (
                f"Action '{action_name}' queued for approval (id={request_id}). "
                f"Approve via the HITL queue, then call execute_approved_action."
            ),
        })

    except Exception as e:
        logger.exception("request_action failed: %s", e)
        return json.dumps({"ok": False, "error": str(e)})


@tool(
    name="execute_approved_action",
    description=(
        "Execute a previously approved internal action. The request must "
        "have state='approved' and operation='action_exec'. Fires the actual "
        "HTTP request (POST/PUT/PATCH/DELETE) with a 15s timeout, then marks "
        "the row 'executed'. Args: request_id (int — id returned by "
        "request_action)."
    ),
)
def execute_approved_action(request_id: int) -> str:
    """Re-read HITL row, verify approved, fire HTTP request, mark executed.

    Returns JSON string with {request_id, status_code, response_snippet} on
    success or {ok: False, error} on failure.
    """
    try:
        rid = int(request_id)
    except (ValueError, TypeError):
        return json.dumps({"ok": False, "error": f"invalid request_id: {request_id}"})

    try:
        eng = _write_engine()

        # 1. Re-read row + verify state
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, request_id, state, operation, details
                      FROM public.dash_hitl_requests
                     WHERE id = :rid
                     LIMIT 1
                    """
                ),
                {"rid": rid},
            ).mappings().first()

        if not row:
            return json.dumps({"ok": False, "error": f"request_id {rid} not found"})
        if row["operation"] != "action_exec":
            return json.dumps({
                "ok": False,
                "error": f"request_id {rid} is not an action_exec (operation={row['operation']})",
            })
        if row["state"] != "approved":
            return json.dumps({
                "ok": False,
                "error": f"request_id {rid} not approved (state={row['state']})",
            })

        details = row["details"] or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}

        method = (details.get("method") or "POST").upper()
        url = details.get("rendered_url")
        headers = details.get("rendered_headers") or {}
        body = details.get("rendered_body") or {}

        if not url:
            return json.dumps({"ok": False, "error": "rendered_url missing in details"})

        # 2. Fire HTTP request
        # Trace span (fail-soft — never breaks tool)
        try:
            from dash.obs.trace import trace_span
            span_cm = trace_span(
                name=f"action.{details.get('action_name', 'unknown')}",
                kind="task",
                meta={"request_id": rid, "method": method, "url": url[:200]},
            )
        except Exception:
            span_cm = None

        status_code = None
        response_text = ""
        http_error: Optional[str] = None

        try:
            if span_cm is not None:
                span_cm.__enter__()
            with httpx.Client(timeout=15.0) as client:
                if method in ("POST", "PUT", "PATCH"):
                    resp = client.request(method, url, json=body, headers=headers)
                elif method == "DELETE":
                    resp = client.request(method, url, headers=headers)
                else:
                    return json.dumps({"ok": False, "error": f"unsupported method: {method}"})
                status_code = resp.status_code
                response_text = resp.text or ""
        except httpx.TimeoutException:
            http_error = "request timed out after 15s"
        except httpx.HTTPError as e:
            http_error = f"http error: {e}"
        finally:
            if span_cm is not None:
                try:
                    span_cm.__exit__(None, None, None)
                except Exception:
                    pass

        if http_error:
            logger.warning(
                "execute_approved_action: request_id=%s http_error=%s", rid, http_error,
            )
            # Mark as failed so it's not retried indefinitely.
            try:
                with eng.begin() as conn:
                    conn.execute(
                        text(
                            """
                            UPDATE public.dash_hitl_requests
                               SET state = 'failed',
                                   response_at = now(),
                                   details = COALESCE(details, '{}'::jsonb)
                                             || CAST(:ex AS jsonb)
                             WHERE id = :rid
                            """
                        ),
                        {
                            "rid": rid,
                            "ex": json.dumps({"http_error": http_error}),
                        },
                    )
            except Exception:
                logger.exception("failed to mark request_id=%s as failed", rid)
            return json.dumps({"ok": False, "request_id": rid, "error": http_error})

        # 3. Update row → executed
        executed_at = None
        try:
            with eng.begin() as conn:
                upd = conn.execute(
                    text(
                        """
                        UPDATE public.dash_hitl_requests
                           SET state = 'executed',
                               response_at = now(),
                               details = COALESCE(details, '{}'::jsonb)
                                         || CAST(:ex AS jsonb)
                         WHERE id = :rid
                        RETURNING response_at
                        """
                    ),
                    {
                        "rid": rid,
                        "ex": json.dumps({
                            "executed_status_code": status_code,
                            "executed_response_snippet": response_text[:500],
                        }),
                    },
                ).first()
                if upd:
                    executed_at = upd[0].isoformat() if upd[0] else None
        except Exception:
            logger.exception("failed to mark request_id=%s as executed", rid)

        logger.info(
            "execute_approved_action: request_id=%s status=%s url=%s",
            rid, status_code, url[:120],
        )

        return json.dumps({
            "ok": True,
            "request_id": rid,
            "status_code": status_code,
            "response_snippet": response_text[:500],
            "executed_at": executed_at,
        })

    except Exception as e:
        logger.exception("execute_approved_action failed: %s", e)
        return json.dumps({"ok": False, "error": str(e)})
