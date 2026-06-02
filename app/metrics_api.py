"""Per-project user-configurable metric system — FastAPI router.

Prefix: /api/projects  (paths include the project slug)

Endpoint ordering matters: literal sub-paths registered before /{name} catch-all.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Central SQL validator + helper (fail-soft if missing)
try:
    from dash.tools.sql_validator import validate_and_fix  # type: ignore
    from dash.tools.llm_sql_helper import _postgres_sql_rules, get_schema_hint  # type: ignore
    _SQL_VALIDATOR_AVAILABLE = True
except Exception as _sqlv_exc:  # noqa: BLE001
    logger.warning("SQL validator unavailable, skipping validation: %s", _sqlv_exc)
    validate_and_fix = None  # type: ignore
    _postgres_sql_rules = None  # type: ignore
    get_schema_hint = None  # type: ignore
    _SQL_VALIDATOR_AVAILABLE = False

router = APIRouter(prefix="/api/projects", tags=["metrics"])


# ---------------------------------------------------------------------------
# Auth helpers (mirror corrections_api + learning.py pattern)
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> Dict[str, Any]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        try:
            from app.auth import get_current_user  # type: ignore
            user = get_current_user(request)
        except Exception:
            user = None
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_editor(user: Dict[str, Any], slug: str) -> None:
    """Gate: require editor (or higher) role on the project."""
    try:
        from app.auth import check_project_permission  # type: ignore
        perm = check_project_permission(user, slug, required_role="editor")
        if not perm:
            raise HTTPException(403, "Editor role required")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("check_project_permission failed: %s", exc)
        raise HTTPException(403, "Editor role required")


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------

class SpecBody(BaseModel):
    """Full metric spec — matches the shape documented in metric_compiler."""
    name: Optional[str] = None
    synonyms: Optional[List[str]] = None
    description: Optional[str] = None
    kind: Optional[str] = None            # count|rate|ratio|contribution|sum|avg
    source_glob: Optional[str] = None
    source_tables: Optional[List[str]] = None
    measure_col: Optional[str] = None
    filters: Optional[List[Dict[str, Any]]] = None
    denom_filters: Optional[List[Dict[str, Any]]] = None
    group_dims: Optional[List[str]] = None
    default_group: Optional[List[str]] = None
    trim_values: Optional[Any] = None
    verified_answer: Optional[Any] = None
    status: Optional[str] = None

    class Config:
        extra = "allow"


class TestBody(BaseModel):
    spec: Dict[str, Any]
    group_by: Optional[str] = None
    extra_filters: Optional[List[Dict[str, Any]]] = None


class TierCompareBody(BaseModel):
    spec: Dict[str, Any]
    question: str


class DeriveBody(BaseModel):
    text: str


class FromChatBody(BaseModel):
    question: str
    sql: str


class ImportBody(BaseModel):
    rows: List[Dict[str, Any]]


class AliasBody(BaseModel):
    synonyms: List[str]


class RollbackBody(BaseModel):
    pass  # version comes from path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATES = [
    {
        "name": "active_customer_count",
        "description": "Count of customers with at least one order in a date window.",
        "kind": "count",
        "source_tables": [],
        "filters": [{"col": "status", "op": "=", "value": "active"}],
        "group_dims": [],
        "synonyms": ["active customers", "customers active"],
    },
    {
        "name": "conversion_rate",
        "description": "Share of leads that converted to paying customers.",
        "kind": "rate",
        "source_tables": [],
        "filters": [{"col": "stage", "op": "=", "value": "converted"}],
        "denom_filters": [],
        "group_dims": ["channel"],
        "synonyms": ["conversion", "lead conversion rate"],
    },
    {
        "name": "revenue_contribution",
        "description": "Revenue contribution (%) of a segment relative to total.",
        "kind": "contribution",
        "source_tables": [],
        "measure_col": "revenue",
        "filters": [],
        "group_dims": ["category"],
        "synonyms": ["share of revenue", "revenue share"],
    },
    {
        "name": "average_order_value",
        "description": "Mean order value across all completed transactions.",
        "kind": "avg",
        "source_tables": [],
        "measure_col": "amount",
        "filters": [{"col": "status", "op": "=", "value": "completed"}],
        "group_dims": [],
        "synonyms": ["AOV", "mean order value"],
    },
]


def _safe_import():
    """Import metric_compiler; raise 503 if not yet available."""
    try:
        from dash.tools import metric_compiler  # type: ignore
        return metric_compiler
    except ImportError as exc:
        raise HTTPException(503, f"metric_compiler not available: {exc}")


# ---------------------------------------------------------------------------
# Literal sub-paths — MUST come before /{slug}/metrics/{name} catch-all
# ---------------------------------------------------------------------------

@router.get("/{slug}/metrics/columns")
def get_columns(slug: str, request: Request, table: Optional[str] = Query(None)):
    """Column catalog for filter-value pickers and schema explorer."""
    _get_user(request)
    mc = _safe_import()
    try:
        catalog = mc.column_catalog(slug, table=table)
        return catalog
    except Exception as exc:
        logger.warning("column_catalog failed for %s: %s", slug, exc)
        raise HTTPException(502, f"column_catalog error: {exc}")


@router.get("/{slug}/metrics/templates")
def get_templates(slug: str, request: Request):
    """Built-in starter templates (clone seeds — not saved)."""
    _get_user(request)
    return _TEMPLATES


@router.get("/{slug}/metrics/review-queue")
def get_review_queue(slug: str, request: Request):
    """Metrics pending review (status=draft)."""
    _get_user(request)
    mc = _safe_import()
    try:
        defs = mc.list_definitions(slug, status="draft")
        return defs
    except Exception as exc:
        logger.warning("review-queue failed for %s: %s", slug, exc)
        raise HTTPException(502, f"review-queue error: {exc}")


@router.get("/{slug}/metrics/drift")
def get_drift(slug: str, request: Request):
    """
    For each verified metric with a verified_answer pin, run_metric and
    compare live total vs the pin.  Returns [{name, pinned, live, ok|drift}].
    """
    _get_user(request)
    mc = _safe_import()
    results = []
    try:
        defs = mc.list_definitions(slug, status="verified")
    except Exception as exc:
        raise HTTPException(502, f"list_definitions error: {exc}")

    for d in defs:
        pin = d.get("verified_answer")
        if not isinstance(pin, dict) or not pin:
            continue
        name = d.get("name", "?")
        kind = d.get("kind", "count")
        # Pick the scalar to compare: count/sum/avg -> total; rate/ratio -> rate_pct.
        # Always run UNGROUPED so the headline figure is a single scalar.
        pin_key = "rate_pct" if kind in ("rate", "ratio") else "total"
        expected = pin.get(pin_key)
        try:
            run = mc.run_metric(slug, d, group_by=[])
            if kind in ("rate", "ratio"):
                rows = run.get("rows") or []
                live = rows[0][-1] if rows and rows[0] else None  # rate_pct column
            else:
                live = run.get("total")
            if expected is None or live is None:
                status = "no_pin" if expected is None else "error"
                ok = None
            else:
                ok = abs(float(live) - float(expected)) <= 0.15  # tolerance for rounding
                status = "ok" if ok else "drift"
            results.append({"name": name, "kind": kind, "pinned": expected,
                            "live": live, "status": status})
        except Exception as exc:
            results.append({"name": name, "kind": kind, "pinned": expected,
                            "live": None, "status": "error", "error": str(exc)})

    return results


@router.post("/{slug}/metrics/test")
def test_metric(slug: str, body: TestBody, request: Request):
    """Run a metric spec without saving (editor TEST button)."""
    _get_user(request)
    mc = _safe_import()
    try:
        result = mc.run_metric(
            slug,
            body.spec,
            group_by=body.group_by,
            extra_filters=body.extra_filters,
        )
        return result
    except Exception as exc:
        logger.warning("test_metric failed for %s: %s", slug, exc)
        raise HTTPException(502, f"run_metric error: {exc}")


@router.post("/{slug}/metrics/tier-compare")
def tier_compare(slug: str, body: TierCompareBody, request: Request):
    """
    Run metric for the LOCKED number; then best-effort per-tier raw-LLM compare.
    Returns {locked_total, tiers:[{tier, answer, error}]}.
    """
    _get_user(request)
    mc = _safe_import()

    # Locked total
    locked_total = None
    try:
        run = mc.run_metric(slug, body.spec)
        locked_total = run.get("total")
    except Exception as exc:
        logger.warning("tier_compare locked run failed: %s", exc)

    # Column catalog for LLM context
    catalog_text = ""
    try:
        catalog = mc.column_catalog(slug)
        catalog_text = "\n".join(
            f"- {c['table']}.{c['column']} ({c.get('dtype','')})"
            for c in catalog[:60]
        )
    except Exception:
        pass

    # Build Postgres dialect rules + schema hint prefix (injected into prompt before LLM call)
    rules_block = ""
    schema_hint = ""
    if _SQL_VALIDATOR_AVAILABLE:
        try:
            rules_block = _postgres_sql_rules() or ""
        except Exception as _rb_exc:  # noqa: BLE001
            logger.warning("_postgres_sql_rules failed for %s: %s", slug, _rb_exc)
        try:
            schema_hint = get_schema_hint(slug) or ""
        except Exception as _sh_exc:  # noqa: BLE001
            logger.warning("get_schema_hint failed for %s: %s", slug, _sh_exc)

    tiers_out = []
    for tier in ["lite", "deep"]:
        try:
            from dash.settings import training_llm_call  # type: ignore
            task = "extraction" if tier == "lite" else "deep_analysis"
            prompt_parts = []
            if rules_block:
                prompt_parts.append(rules_block)
            if schema_hint:
                prompt_parts.append(f"SCHEMA:\n{schema_hint}")
            prompt_parts.append(
                f"You are a data analyst. The user asks: {body.question}\n\n"
                f"Available tables and columns:\n{catalog_text}\n\n"
                f"Write ONE valid PostgreSQL SELECT that answers this question. "
                f"Return ONLY the SQL, no markdown fences."
            )
            prompt = "\n\n".join(prompt_parts)
            sql_resp = training_llm_call(prompt, task)
            if not sql_resp:
                tiers_out.append({"tier": tier, "answer": None, "error": "empty LLM response"})
                continue

            # Strip fences
            sql_clean = re.sub(r"```[a-z]*\n?", "", sql_resp).replace("```", "").strip()

            # Central SQL validation — fail-soft if validator missing
            if _SQL_VALIDATOR_AVAILABLE:
                try:
                    v = validate_and_fix(sql_clean, slug, strict=True)
                    if not v.get("ok"):
                        tiers_out.append({
                            "tier": tier,
                            "answer": None,
                            "error": f"SQL validation failed: {v.get('errors')}",
                            "validation_errors": v.get("errors"),
                        })
                        continue
                    sql_clean = v.get("sql") or sql_clean  # SQL-VALIDATED
                except Exception as _val_exc:  # noqa: BLE001
                    logger.warning("validate_and_fix raised, skipping: %s", _val_exc)

            # Run via run_metric-style — build a minimal spec and run_metric
            synthetic_spec = dict(body.spec)
            synthetic_spec["_raw_sql_override"] = sql_clean  # SQL-VALIDATED
            try:
                result = mc.run_metric(slug, synthetic_spec)
                tiers_out.append({"tier": tier, "answer": result.get("total"), "error": None})
            except Exception as exec_exc:
                tiers_out.append({"tier": tier, "answer": None, "error": str(exec_exc)})
        except Exception as exc:
            tiers_out.append({"tier": tier, "answer": None, "error": str(exc)})

    return {"locked_total": locked_total, "tiers": tiers_out}


@router.post("/{slug}/metrics/derive")
async def derive_metric(slug: str, body: DeriveBody, request: Request):
    """
    NL → spec DRAFT.  Uses LLM with column catalog to produce a proposed spec.
    Returns {spec, confidence}.  NOT saved.
    """
    _get_user(request)
    mc = _safe_import()

    catalog_text = ""
    try:
        catalog = mc.column_catalog(slug)
        catalog_text = "\n".join(
            f"- {c['table']}.{c['column']} ({c.get('dtype','')})"
            + (f" sample: {c.get('sample_values','')}" if c.get("sample_values") else "")
            for c in catalog[:80]
        )
    except Exception:
        pass

    prompt = (
        f"You are a metric definition expert. The user describes a metric: \"{body.text}\"\n\n"
        f"Available columns:\n{catalog_text}\n\n"
        "Output STRICT JSON only (no markdown, no fences) with keys:\n"
        "  name (snake_case), kind (count|rate|ratio|contribution|sum|avg),\n"
        "  filters (list of {{col, op, value}}), group_dims (list of col names),\n"
        "  measure_col (string or null), description (one sentence).\n"
        "Example: {\"name\":\"active_users\",\"kind\":\"count\","
        "\"filters\":[{\"col\":\"status\",\"op\":\"=\",\"value\":\"active\"}],"
        "\"group_dims\":[],\"measure_col\":null,\"description\":\"...\"}"
    )

    try:
        import json
        from dash.settings import training_llm_call  # type: ignore
        raw = training_llm_call(prompt, "extraction") or ""
        raw = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "").strip()

        # Robust parse: the LLM sometimes returns prose around the JSON, or emits
        # MULTIPLE objects (e.g. for an exploratory ask). Scan for the FIRST
        # balanced {...} object and parse only that — never let trailing content 502.
        def _first_object(s: str):
            start = s.find("{")
            if start < 0:
                return None
            depth, in_str, esc = 0, False, False
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return s[start:i + 1]
            return None

        spec = {}
        blob = _first_object(raw)
        if blob:
            try:
                spec = json.loads(blob)
            except Exception:
                # last resort: greedy match + trim trailing junk
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    try:
                        spec = json.loads(m.group(0))
                    except Exception:
                        spec = {}
        if not isinstance(spec, dict):
            spec = {}
        confidence = "high" if spec.get("name") and spec.get("kind") else "low"
        return {"spec": spec, "confidence": confidence}
    except Exception as exc:
        logger.warning("derive_metric failed for %s: %s", slug, exc)
        # Fail soft to the UI so the panel shows a friendly message, not a 502.
        return {"spec": {}, "confidence": "low", "error": f"derive error: {exc}"}


@router.get("/{slug}/metrics/recommend-new")
async def recommend_new_metrics(slug: str, request: Request):
    """AI suggests NEW metric specs from the schema that aren't defined yet.

    Scans column catalog + existing verified/draft metric names, asks the LLM to
    propose up to 6 useful metrics the user has NOT created. Returns
    {suggestions: [{name, kind, filters, group_dims, measure_col, description, reason}]}.
    NOT saved — each suggestion is a one-click 'Create' draft.
    """
    _get_user(request)
    mc = _safe_import()

    # existing names + synonyms to exclude
    existing: set[str] = set()
    try:
        for d in mc.list_definitions(slug) or []:
            if d.get("name"):
                existing.add(str(d["name"]).lower())
            for s in (d.get("synonyms") or []):
                existing.add(str(s).lower())
    except Exception:
        pass

    catalog_text = ""
    try:
        catalog = mc.column_catalog(slug)
        catalog_text = "\n".join(
            f"- {c['table']}.{c['column']} ({c.get('dtype','')})"
            + (f" sample: {c.get('sample_values','')}" if c.get("sample_values") else "")
            for c in catalog[:80]
        )
    except Exception:
        pass

    if not catalog_text:
        return {"suggestions": []}

    prompt = (
        "You are a metrics analyst. Given the schema below, propose up to 6 USEFUL "
        "metrics a business user would want, that are NOT already in the existing list.\n\n"
        f"Existing metrics (do NOT repeat these or close variants): {sorted(existing) or 'none'}\n\n"
        f"Available columns:\n{catalog_text}\n\n"
        "Output STRICT JSON only (no markdown/fences): a JSON array where each item has keys:\n"
        "  name (snake_case), kind (count|rate|ratio|contribution|sum|avg),\n"
        "  filters (list of {{col, op, value}}), group_dims (list of real column names),\n"
        "  measure_col (string or null), description (one sentence),\n"
        "  reason (why this metric is useful, one short phrase).\n"
        "Use only real column names from the schema. Return [] if nothing new is worthwhile."
    )

    try:
        from dash.settings import training_llm_call  # type: ignore
        import json
        raw = training_llm_call(prompt, "extraction") or ""
        raw = re.sub(r"```[a-z]*\n?", "", raw).replace("```", "").strip()
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        arr = json.loads(m.group(0)) if m else []
        out = []
        for s in arr if isinstance(arr, list) else []:
            nm = str(s.get("name", "")).lower()
            if nm and nm not in existing:
                out.append(s)
        return {"suggestions": out[:6]}
    except Exception as exc:
        logger.warning("recommend_new_metrics failed for %s: %s", slug, exc)
        return {"suggestions": []}


@router.post("/{slug}/metrics/from-chat")
async def from_chat(slug: str, body: FromChatBody, request: Request):
    """
    Prefill a spec from the SQL the agent actually ran.
    Parses simple WHERE predicates, COUNT/SUM/AVG → kind, GROUP BY → group_dims.
    Returns a draft spec (NOT saved).
    """
    _get_user(request)
    sql = body.sql or ""

    # Detect kind
    kind = "count"
    if re.search(r"\bSUM\s*\(", sql, re.I):
        kind = "sum"
    elif re.search(r"\bAVG\s*\(", sql, re.I):
        kind = "avg"

    # Detect measure_col
    measure_col = None
    m = re.search(r"\b(?:SUM|AVG)\s*\(\s*(\w+)\s*\)", sql, re.I)
    if m:
        measure_col = m.group(1)

    # Parse WHERE col = 'val' or col IN (...)
    filters: List[Dict[str, Any]] = []
    for col, val in re.findall(r"(\w+)\s*=\s*'([^']+)'", sql):
        if col.upper() not in ("WHERE", "AND", "OR"):
            filters.append({"col": col, "op": "=", "value": val})
    for col, vals_raw in re.findall(r"(\w+)\s+IN\s*\(([^)]+)\)", sql, re.I):
        vals = [v.strip().strip("'\"") for v in vals_raw.split(",")]
        filters.append({"col": col, "op": "in", "value": vals})

    # Detect GROUP BY
    group_dims: List[str] = []
    m2 = re.search(r"GROUP\s+BY\s+([\w\s,]+?)(?:ORDER|HAVING|LIMIT|$)", sql, re.I)
    if m2:
        group_dims = [g.strip() for g in m2.group(1).split(",") if g.strip()]

    spec = {
        "description": body.question,
        "kind": kind,
        "measure_col": measure_col,
        "filters": filters,
        "group_dims": group_dims,
        "status": "draft",
    }
    return {"spec": spec}


@router.post("/{slug}/metrics/import")
async def import_metrics(slug: str, body: ImportBody, request: Request):
    """Bulk save specs as status=draft. Returns {created, errors}."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()

    username = user.get("username")
    created = 0
    errors: List[Dict[str, Any]] = []

    mc.cache_bust(slug)

    for row in body.rows:
        row_copy = dict(row)
        row_copy["status"] = "draft"
        try:
            mc.save_definition(slug, row_copy, username)
            created += 1
        except Exception as exc:
            errors.append({"name": row_copy.get("name", "?"), "error": str(exc)})

    return {"created": created, "errors": errors}


@router.get("/{slug}/metrics/permissions")
def get_permissions(slug: str, request: Request):
    """Static role → capability matrix (informational for UI)."""
    _get_user(request)
    return {
        "permissions": {
            "viewer":  {"view": True,  "test": True,  "save": False, "approve": False},
            "editor":  {"view": True,  "test": True,  "save": True,  "approve": False},
            "admin":   {"view": True,  "test": True,  "save": True,  "approve": True},
        }
    }


# ---------------------------------------------------------------------------
# Main list + create
# ---------------------------------------------------------------------------

@router.get("/{slug}/metrics")
def list_metrics(slug: str, request: Request, status: Optional[str] = Query(None)):
    """List metric definitions, optionally filtered by status."""
    _get_user(request)
    mc = _safe_import()
    try:
        defs = mc.list_definitions(slug, status=status)
        return defs
    except Exception as exc:
        logger.warning("list_metrics failed for %s: %s", slug, exc)
        raise HTTPException(502, f"list_definitions error: {exc}")


@router.post("/{slug}/metrics")
async def create_metric(slug: str, body: SpecBody, request: Request):
    """Create or update a metric definition. Requires editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()

    spec = body.model_dump(exclude_none=True)
    if not spec.get("name"):
        raise HTTPException(400, "name is required")

    try:
        result = mc.save_definition(slug, spec, user.get("username"))
        mc.cache_bust(slug)
        return result
    except Exception as exc:
        logger.warning("create_metric failed for %s: %s", slug, exc)
        raise HTTPException(502, f"save_definition error: {exc}")


@router.get("/{slug}/metrics/crm-eligible")
def crm_eligible(slug: str, request: Request):
    """Whether this project's schema looks like a CRM (drives the 'Seed CRM
    metrics' button visibility). Fail-soft → eligible:false."""
    _get_user(request)
    try:
        from dash.tools.crm_starter import looks_like_crm  # type: ignore
        return {"eligible": bool(looks_like_crm(slug))}
    except Exception as exc:  # noqa: BLE001
        logger.debug("crm-eligible failed for %s: %s", slug, exc)
        return {"eligible": False}


@router.get("/{slug}/metrics/crm-preview")
def crm_preview(slug: str, request: Request):
    """Candidate CRM starter metrics resolved to this project's columns, WITHOUT
    saving — so the user can pick which to seed."""
    _get_user(request)
    try:
        from dash.tools.crm_starter import preview_crm_starter  # type: ignore
        return preview_crm_starter(slug)
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm-preview failed for %s: %s", slug, exc)
        return {"ok": False, "candidates": [], "error": str(exc)}


@router.post("/{slug}/metrics/seed-crm")
async def seed_crm(slug: str, request: Request):
    """Seed the generic CRM starter metric pack (universal, column-alias-resolved,
    status='suggested'). Optional JSON body {"names": [...]} seeds only the
    selected metrics; absent/empty seeds all resolvable. Idempotent. Editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    only = None
    try:
        body = await request.json()
        if isinstance(body, dict) and isinstance(body.get("names"), list):
            only = [str(n) for n in body["names"] if n]
    except Exception:  # noqa: BLE001
        only = None
    try:
        from dash.tools.crm_starter import seed_crm_starter  # type: ignore
        res = seed_crm_starter(slug, only=only)
        try:
            _safe_import().cache_bust(slug)
        except Exception:  # noqa: BLE001
            pass
        return res
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed-crm failed for %s: %s", slug, exc)
        raise HTTPException(502, f"seed-crm error: {exc}")


# ---------------------------------------------------------------------------
# /{name} catch-all and sub-resources — AFTER all literal paths
# ---------------------------------------------------------------------------

@router.get("/{slug}/metrics/{name}/history")
def get_history(slug: str, name: str, request: Request):
    """Version history for a metric."""
    _get_user(request)
    mc = _safe_import()
    try:
        versions = mc.list_versions(slug, name)
        return versions
    except Exception as exc:
        logger.warning("list_versions failed: %s", exc)
        raise HTTPException(502, f"list_versions error: {exc}")


@router.post("/{slug}/metrics/{name}/rollback/{version}")
def rollback_metric(slug: str, name: str, version: int, request: Request):
    """Roll back a metric to a previous version. Requires editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()
    try:
        result = mc.rollback(slug, name, version, user.get("username"))
        mc.cache_bust(slug)
        return result
    except Exception as exc:
        logger.warning("rollback failed: %s", exc)
        raise HTTPException(502, f"rollback error: {exc}")


@router.post("/{slug}/metrics/{name}/approve")
def approve_metric(slug: str, name: str, request: Request):
    """Set metric status to 'verified'. Requires editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()
    try:
        result = mc.set_status(slug, name, "verified", user.get("username"))
        mc.cache_bust(slug)
        return result
    except Exception as exc:
        logger.warning("approve_metric failed: %s", exc)
        raise HTTPException(502, f"set_status error: {exc}")


@router.patch("/{slug}/metrics/{name}/aliases")
async def update_aliases(slug: str, name: str, body: AliasBody, request: Request):
    """Update synonyms list for a metric. Requires editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()

    try:
        defn = mc.load_definition(slug, name)
        if defn is None:
            raise HTTPException(404, f"Metric '{name}' not found")
        defn["synonyms"] = body.synonyms
        result = mc.save_definition(slug, defn, user.get("username"))
        mc.cache_bust(slug)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("update_aliases failed: %s", exc)
        raise HTTPException(502, f"alias update error: {exc}")


@router.get("/{slug}/metrics/{name}")
def get_metric(slug: str, name: str, request: Request):
    """Load a single metric definition (404 if not found)."""
    _get_user(request)
    mc = _safe_import()
    try:
        defn = mc.load_definition(slug, name)
    except Exception as exc:
        logger.warning("load_definition failed: %s", exc)
        raise HTTPException(502, f"load_definition error: {exc}")
    if defn is None:
        raise HTTPException(404, f"Metric '{name}' not found")
    return defn


@router.delete("/{slug}/metrics/{name}")
def delete_metric(slug: str, name: str, request: Request):
    """Soft-delete (deprecate) a metric. Requires editor role."""
    user = _get_user(request)
    _require_editor(user, slug)
    mc = _safe_import()
    try:
        mc.delete_definition(slug, name)
        mc.cache_bust(slug)
        return {"ok": True, "deleted": name}
    except Exception as exc:
        logger.warning("delete_metric failed: %s", exc)
        raise HTTPException(502, f"delete_definition error: {exc}")
