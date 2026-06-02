"""RLS Setup Wizard — deterministic, schema-aware policy generator.

3 hardcoded questions:
  Q1 audience   — customers / staff / both
  Q2 scope      — tenant column (site_code, store_id, …) or none
  Q3 sensitive  — money / qty / pii / codes / timestamps + role overrides

NO LLM. Pure Python rule-based generator. Output is reviewable before apply.

Schema-aware option discovery reuses app.embed._build_schema_catalog.
"""
from __future__ import annotations

import re as _re
from typing import Any

# ── Sensitive-column matchers (mirror IMPORT FROM SCHEMA suggester) ─────
_SENSITIVE_MATCHERS: dict[str, _re.Pattern] = {
    "money": _re.compile(
        r"price|cost|salary|wage|revenue|amount|margin|profit|commission|discount",
        _re.I,
    ),
    "qty": _re.compile(
        r"qty|stock|inventory|count|on_hand|balance|available", _re.I
    ),
    "pii": _re.compile(r"email|phone|address|dob|date_of_birth|ssn|tax_id", _re.I),
    "codes": _re.compile(r"_id$|_code$|code$|^id$", _re.I),
    "timestamps": _re.compile(r"_at$|_on$|date$|time$", _re.I),
}

_SENSITIVE_LABELS = {
    "money": "Money cols",
    "qty": "Stock/qty cols",
    "pii": "PII (email/phone)",
    "codes": "Internal codes",
    "timestamps": "Timestamps",
}

_SCOPE_RE = _re.compile(r"(?:^|_)(id|code)$", _re.I)
_INT_TYPES = ("int", "serial", "bigint", "smallint", "numeric", "decimal")


def _build_catalog(slug: str) -> dict:
    """Late import to avoid circular dependency with app.embed."""
    from app.embed import _build_schema_catalog
    return _build_schema_catalog(slug)


# ─────────────────────────────────────────────────────────────────────────
# Option discovery
# ─────────────────────────────────────────────────────────────────────────
def build_wizard_options(slug: str) -> dict:
    """Inspect project schema and return options for all 3 wizard questions."""
    try:
        cat = _build_catalog(slug)
    except Exception:
        cat = {"tables": [], "suggested_claims": []}

    tables: list[dict] = cat.get("tables") or []
    suggested_claims = cat.get("suggested_claims") or []
    fk_like_keys = {(c.get("key") or "").lower() for c in suggested_claims}

    # Q1 — hardcoded audience options
    q1_audience = [
        {"value": "customers", "label": "Customers",
         "desc": "End users — strict, see only own"},
        {"value": "staff",     "label": "Staff",
         "desc": "Internal — see by scope"},
        {"value": "both",      "label": "Both",
         "desc": "Mixed — role decides"},
    ]

    # Q2 — scope column discovery: *_id|*_code in ≥2 tables
    col_table_map: dict[str, list[str]] = {}
    col_types: dict[str, str] = {}
    for t in tables:
        tname = t.get("name") or ""
        seen: set[str] = set()
        for c in (t.get("columns") or []):
            cname = (c.get("name") or "").lower()
            if not cname or cname in seen:
                continue
            seen.add(cname)
            if _SCOPE_RE.search(cname):
                col_table_map.setdefault(cname, []).append(tname)
                col_types.setdefault(cname, (c.get("type") or "").lower())

    q2_scope: list[dict] = []
    for cname, tlist in sorted(col_table_map.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(tlist) < 2:
            continue
        q2_scope.append({
            "value":       cname,
            "label":       cname,
            "table":       tlist[0],
            "tables":      tlist,
            "recommended": cname in fk_like_keys,
            "found":       True,
        })
    q2_scope.append({
        "value": "none", "label": "None (single-tenant)",
        "table": None, "recommended": False, "found": True,
    })

    q2_hierarchy_options = [
        {"value": "no",  "label": "Flat — one scope per user"},
        {"value": "yes", "label": "Hierarchical (e.g. region → store)"},
    ]

    # Q3 — sensitive column categories
    q3_sensitive: list[dict] = []
    for key, rx in _SENSITIVE_MATCHERS.items():
        matches: list[str] = []
        for t in tables:
            for c in (t.get("columns") or []):
                cname = (c.get("name") or "")
                if cname and rx.search(cname):
                    if cname not in matches:
                        matches.append(cname)
        q3_sensitive.append({
            "key":     key,
            "label":   _SENSITIVE_LABELS[key],
            "matches": matches[:50],
            "found":   bool(matches),
        })

    q3_role_options = [
        {"key": "hq_bypass",       "label": "HQ/admin role bypasses everything"},
        {"key": "auditor_readall", "label": "Auditor role: read-only see-all"},
    ]

    return {
        "q1_audience":          q1_audience,
        "q2_scope":             q2_scope,
        "q2_hierarchy_options": q2_hierarchy_options,
        "q3_sensitive_categories": q3_sensitive,
        "q3_role_options":      q3_role_options,
    }


# ─────────────────────────────────────────────────────────────────────────
# Policy generator
# ─────────────────────────────────────────────────────────────────────────
def _scope_claim_type(slug: str, scope_col: str) -> str:
    """Infer 'number' vs 'string' from schema column type."""
    try:
        cat = _build_catalog(slug)
    except Exception:
        return "string"
    for t in cat.get("tables") or []:
        for c in t.get("columns") or []:
            if (c.get("name") or "").lower() == scope_col.lower():
                dtype = (c.get("type") or "").lower()
                if any(it in dtype for it in _INT_TYPES):
                    return "number"
                return "string"
    return "string"


def _matches_for(cat: dict, category: str) -> list[tuple[str, str]]:
    """Return list of (table, column) pairs matching a sensitive category."""
    rx = _SENSITIVE_MATCHERS.get(category)
    if rx is None:
        return []
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for t in cat.get("tables") or []:
        tname = t.get("name") or ""
        for c in t.get("columns") or []:
            cname = c.get("name") or ""
            if cname and rx.search(cname):
                key = (tname, cname)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def _col_is_sensitive(col_name: str) -> bool:
    """True if column matches any sensitive matcher except 'codes'/'timestamps'."""
    for cat in ("money", "qty", "pii"):
        if _SENSITIVE_MATCHERS[cat].search(col_name or ""):
            return True
    return False


def generate_policies(slug: str, answers: dict) -> dict:
    """Deterministic policy generator. Returns {claims, policies, warnings, summary}."""
    answers = answers or {}
    q1 = (answers.get("q1") or "staff").lower()
    q2_scope = (answers.get("q2_scope") or "none").lower()
    q2_hier = (answers.get("q2_hierarchy") or "no").lower()
    q3_sens = [s.lower() for s in (answers.get("q3_sensitive") or [])]
    q3_roles = [r.lower() for r in (answers.get("q3_roles") or [])]

    try:
        cat = _build_catalog(slug)
    except Exception:
        cat = {"tables": []}

    claims: list[dict] = []
    policies: list[dict] = []
    warnings: list[str] = []

    # ── Claims ──────────────────────────────────────────────────────────
    if q2_scope and q2_scope != "none":
        claims.append({
            "key":      q2_scope,
            "type":     _scope_claim_type(slug, q2_scope),
            "required": True,
        })

    if q3_roles:
        claims.append({
            "key":      "role",
            "type":     "enum",
            "values":   ["staff", "manager", "hq", "auditor"],
            "required": False,
        })

    # ── Bypass roles for sensitive policies ─────────────────────────────
    bypass_private = ["hq"] if "hq_bypass" in q3_roles else []
    universal_bypass = ["auditor"] if "auditor_readall" in q3_roles else []

    def _with_bypass(policy: dict, include_private_bypass: bool) -> dict:
        roles: list[str] = []
        if include_private_bypass:
            roles.extend(bypass_private)
        roles.extend(universal_bypass)
        if roles:
            # dedupe preserve order
            seen = set()
            policy["bypass_roles"] = [r for r in roles if not (r in seen or seen.add(r))]
        return policy

    # ── Sensitive categories → policies ─────────────────────────────────
    protected: set[tuple[str, str]] = set()

    if "money" in q3_sens:
        for (tbl, col) in _matches_for(cat, "money"):
            p = {"table": tbl, "column": col, "mode": "hidden"}
            policies.append(_with_bypass(p, include_private_bypass=True))
            protected.add((tbl, col))

    if "qty" in q3_sens:
        if q2_scope == "none":
            warnings.append(
                "q2_scope was 'none' so stock/qty cols can't be scoped — left as shared"
            )
        else:
            for (tbl, col) in _matches_for(cat, "qty"):
                p = {
                    "table":  tbl,
                    "column": col,
                    "mode":   "own_value",
                    "filter": q2_scope,
                    "claim":  q2_scope,
                }
                policies.append(_with_bypass(p, include_private_bypass=True))
                protected.add((tbl, col))

    if "pii" in q3_sens:
        for (tbl, col) in _matches_for(cat, "pii"):
            p = {"table": tbl, "column": col, "mode": "redacted"}
            policies.append(_with_bypass(p, include_private_bypass=True))
            protected.add((tbl, col))

    if "codes" in q3_sens:
        for (tbl, col) in _matches_for(cat, "codes"):
            if (tbl, col) in protected:
                continue
            policies.append({"table": tbl, "column": col, "mode": "shared"})

    if "timestamps" in q3_sens:
        for (tbl, col) in _matches_for(cat, "timestamps"):
            if (tbl, col) in protected:
                continue
            policies.append({"table": tbl, "column": col, "mode": "shared"})

    # ── All-shared tables (only non-sensitive cols) ─────────────────────
    for t in cat.get("tables") or []:
        tname = t.get("name") or ""
        cols = [(c.get("name") or "") for c in (t.get("columns") or [])]
        if not cols:
            continue
        if any(_col_is_sensitive(cn) for cn in cols):
            continue
        # Skip if any explicit policy already targets this table
        if any(p.get("table") == tname for p in policies):
            continue
        policies.append({"table": tname, "column": "*", "mode": "shared"})

    # ── Audience-specific notes (informational only) ────────────────────
    if q1 == "customers" and q2_scope == "none":
        warnings.append(
            "Audience='customers' but no scope column selected — "
            "customer isolation cannot be enforced without a scope claim."
        )
    if q2_hier == "yes":
        warnings.append(
            "Hierarchical scope selected — wizard emits flat policy; "
            "extend manually for region→store rollups."
        )

    summary = {
        "claims_count":             len(claims),
        "policies_count":           len(policies),
        "sensitive_cols_protected": len(protected),
    }

    return {
        "claims":   claims,
        "policies": policies,
        "warnings": warnings,
        "summary":  summary,
    }


# ─────────────────────────────────────────────────────────────────────────
# Audit-log table bootstrap (migration 065 sibling — idempotent)
# ─────────────────────────────────────────────────────────────────────────
_WIZARD_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS public.dash_embed_wizard_runs (
    id              BIGSERIAL PRIMARY KEY,
    embed_id        TEXT,
    project_slug    TEXT,
    user_id         INTEGER,
    answers         JSONB,
    generated       JSONB,
    applied         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_embed_wizard_runs_embed
    ON public.dash_embed_wizard_runs (embed_id);
CREATE INDEX IF NOT EXISTS idx_embed_wizard_runs_project
    ON public.dash_embed_wizard_runs (project_slug, created_at DESC);
"""

_WIZARD_RUNS_BOOTSTRAPPED = False


def ensure_wizard_runs_table() -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS. Safe to call repeatedly."""
    global _WIZARD_RUNS_BOOTSTRAPPED
    if _WIZARD_RUNS_BOOTSTRAPPED:
        return
    try:
        from sqlalchemy import text as _t
        from dash.embed import _get_engine
        eng = _get_engine()
        with eng.begin() as conn:
            for stmt in _WIZARD_RUNS_DDL.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.execute(_t(s))
        _WIZARD_RUNS_BOOTSTRAPPED = True
    except Exception:
        # Fail-soft — wizard still works without the audit table.
        pass


def log_wizard_run(
    *,
    embed_id: str | None,
    project_slug: str,
    user_id: int | None,
    answers: dict,
    generated: dict,
    applied: bool,
) -> None:
    """Best-effort INSERT into dash_embed_wizard_runs. Fail-soft."""
    ensure_wizard_runs_table()
    try:
        import json as _j
        from sqlalchemy import text as _t
        from dash.embed import _get_engine
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(_t(
                "INSERT INTO public.dash_embed_wizard_runs "
                "  (embed_id, project_slug, user_id, answers, generated, applied) "
                "VALUES (:e, :s, :u, CAST(:a AS jsonb), CAST(:g AS jsonb), :ap)"
            ), {
                "e":  embed_id,
                "s":  project_slug,
                "u":  user_id,
                "a":  _j.dumps(answers or {}),
                "g":  _j.dumps(generated or {}),
                "ap": bool(applied),
            })
    except Exception:
        pass
