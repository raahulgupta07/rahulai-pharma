"""Plain-English explanations for RLS blueprints, policies, claims, and wizard output.

Pure-Python lookup tables + string templates. NO LLM. Deterministic.

Used by app/embed.py to enrich API responses so non-technical users (store
owners, pharmacists, ops managers) can understand what an RLS blueprint
actually does, who sees what, and what breaks if they get it wrong.

User-facing strings deliberately avoid jargon: never say "principal",
"actor", "tenant", "HMAC", "AST", "sqlglot". Always say "your store",
"the caller", "the customer", "the user signed in to the widget".
"""
from __future__ import annotations

import re
import time
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Mode reference — the 5 RLS policy modes, in plain English
# ─────────────────────────────────────────────────────────────────────────────

_MODE_EXPLANATIONS: dict[str, dict[str, str]] = {
    "private": {
        "label": "Private",
        "one_liner": "Only your own rows are visible. Other rows disappear entirely.",
        "detail": (
            "The caller sees only rows that match their scope (e.g. their "
            "store_code). Rows belonging to other stores are filtered out — "
            "as if they did not exist."
        ),
        "gotcha": (
            "Customers using the widget cannot discover what other stores carry, "
            "even when that would be helpful (e.g. 'where else is this drug sold?')."
        ),
        "example_in_practice": (
            "Store A asks 'show me my stock' → sees only Store A rows. "
            "Store B's rows are completely hidden."
        ),
    },
    "shared": {
        "label": "Shared",
        "one_liner": "Everyone sees this column. No filtering.",
        "detail": (
            "Every caller sees the real value, regardless of their scope. "
            "Use for non-sensitive columns like product names, SKU codes, "
            "category labels."
        ),
        "gotcha": (
            "Do not mark sensitive data (prices, customer emails, stock counts) "
            "as 'shared' — every caller will see it."
        ),
        "example_in_practice": (
            "Drug name 'Paracetamol' is shared — every store and every customer "
            "sees the same name."
        ),
    },
    "redacted": {
        "label": "Redacted",
        "one_liner": "Value is masked (e.g. shown as ***) for callers outside scope.",
        "detail": (
            "Rows still appear, but the column's value is replaced with a "
            "placeholder for anyone who isn't allowed to see the real value. "
            "Good for emails, phone numbers, internal notes."
        ),
        "gotcha": (
            "The fact that a row exists is still visible — only the value is "
            "hidden. If existence itself is secret, use 'private' or 'hidden'."
        ),
        "example_in_practice": (
            "Customer list shows 'j***@gmail.com' to staff outside the owner "
            "store; the owner store sees the real address."
        ),
    },
    "hidden": {
        "label": "Hidden",
        "one_liner": "Column is removed from the response for callers outside scope.",
        "detail": (
            "The row still appears, but the column is dropped entirely from the "
            "returned data — as if the column did not exist in the schema for "
            "that caller. Stronger than 'redacted' (no placeholder leaks the "
            "fact that a value was withheld)."
        ),
        "gotcha": (
            "Front-end code that always expects this column may break. Test the "
            "widget after switching a column from 'shared' → 'hidden'."
        ),
        "example_in_practice": (
            "Cost price column is hidden from store staff; only HQ sees it."
        ),
    },
    "own_value": {
        "label": "Own value",
        "one_liner": (
            "Show the real value only when the row belongs to the caller; "
            "show NULL on every other row."
        ),
        "detail": (
            "Combines 'shared' (rows still appear) with 'private' (value is "
            "scoped). The caller's own rows show real numbers; other stores' "
            "rows still appear in the list, but with NULL in the protected "
            "column. NULL is interpreted as 'not your data'."
        ),
        "gotcha": (
            "If the filter column is wrong, even the caller's own rows come "
            "back as NULL. If you actually want other rows hidden completely, "
            "use 'private' instead — but then customers can't see where else "
            "a product is available."
        ),
        "example_in_practice": (
            "Store A asks 'where is Paracetamol?' → sees own stock (24 units) "
            "AND a list of other stores carrying it, with quantities shown "
            "as NULL."
        ),
    },
}


def explain_mode(mode: str) -> dict[str, str]:
    """Return {label, one_liner, detail, gotcha, example_in_practice}."""
    key = (mode or "").lower().strip()
    if key in _MODE_EXPLANATIONS:
        return dict(_MODE_EXPLANATIONS[key])
    return {
        "label": mode or "Unknown",
        "one_liner": "Unknown policy mode.",
        "detail": (
            f"The mode '{mode}' is not one of the standard five "
            "(private, shared, redacted, hidden, own_value)."
        ),
        "gotcha": "Behaviour depends on the embed's custom policy code.",
        "example_in_practice": "",
    }


def mode_legend() -> list[dict[str, str]]:
    """Return all 5 modes as a list, suitable for a frontend tooltip menu."""
    return [explain_mode(k) for k in
            ("private", "shared", "redacted", "hidden", "own_value")]


# ─────────────────────────────────────────────────────────────────────────────
# Apply-mode (merge vs replace)
# ─────────────────────────────────────────────────────────────────────────────

_APPLY_MODE_EXPLANATIONS: dict[str, dict[str, str]] = {
    "replace": {
        "what_happens": (
            "Existing claims and policies on this embed are deleted and "
            "replaced with the blueprint's. Clean slate."
        ),
        "when_to_use": (
            "Use when starting fresh, or when the embed's current rules are "
            "wrong and you want to wipe them."
        ),
        "risk": (
            "Any custom rules you previously added to this embed will be lost. "
            "Cannot be undone automatically — export current settings first if "
            "you might need them back."
        ),
    },
    "merge": {
        "what_happens": (
            "Blueprint rules are added to existing rules. If both define a rule "
            "for the same column, the blueprint wins; the previous rule is "
            "recorded as a 'conflict' so you can review."
        ),
        "when_to_use": (
            "Use when the embed already has rules you want to keep, and the "
            "blueprint just adds new ones (or upgrades a few)."
        ),
        "risk": (
            "Silent overrides on conflicting columns — always review the "
            "'conflicts' list after merging."
        ),
    },
}


def explain_apply_mode(mode: str) -> dict[str, str]:
    """Return {what_happens, when_to_use, risk} for merge/replace."""
    key = (mode or "").lower().strip()
    if key in _APPLY_MODE_EXPLANATIONS:
        return dict(_APPLY_MODE_EXPLANATIONS[key])
    return {
        "what_happens": f"Unknown apply mode '{mode}'.",
        "when_to_use": "",
        "risk": "Behaviour undefined.",
    }


def apply_modes_legend() -> dict[str, dict[str, str]]:
    """Return both apply modes as a dict for frontend tooltip rendering."""
    return {
        "merge":   explain_apply_mode("merge"),
        "replace": explain_apply_mode("replace"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claims — what each claim means and why it must be present
# ─────────────────────────────────────────────────────────────────────────────

_CLAIM_KEY_HINTS: dict[str, str] = {
    "site_code":   "which store",
    "store_code":  "which store",
    "store_id":    "which store",
    "shop_code":   "which shop",
    "branch_code": "which branch",
    "branch_id":   "which branch",
    "outlet_id":   "which outlet",
    "tenant_id":   "which tenant",
    "company_id":  "which company",
    "region":      "which region",
    "region_code": "which region",
    "city_code":   "which city",
    "user_id":     "which user",
    "customer_id": "which customer",
    "role":        "what role the caller has (e.g. staff, manager, hq)",
}


def explain_claim(claim: dict[str, Any]) -> str:
    """Return a one-sentence plain-English description of a claim.

    Example:
        {key:'site_code', type:'string', required:True}
        → "Identifies which store the user belongs to. Each request must
           include this value, or the request is rejected."
    """
    claim = claim or {}
    key = str(claim.get("key") or "").strip()
    required = bool(claim.get("required"))
    ctype = str(claim.get("type") or "string").lower()
    values = claim.get("values") or []

    hint = _CLAIM_KEY_HINTS.get(key.lower(), f"the value of '{key}'")

    parts = [f"Identifies {hint}."]

    if ctype == "enum" and values:
        parts.append(
            f"Allowed values: {', '.join(str(v) for v in values[:6])}"
            + ("." if len(values) <= 6 else f", and {len(values) - 6} more.")
        )
    elif ctype in ("int", "integer", "number"):
        parts.append("Must be a number.")
    elif ctype == "string":
        parts.append("Must be a text value.")

    if required:
        parts.append("Each request must include this value, or the request is rejected.")
    else:
        parts.append("Optional — requests without it still go through.")

    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Policy explanations — composed from table + column + mode + filter
# ─────────────────────────────────────────────────────────────────────────────

_PII_COL_PAT = re.compile(
    r"(email|phone|mobile|address|contact|ssn|nrc|passport|dob|birth)",
    re.IGNORECASE,
)
_MONEY_COL_PAT = re.compile(
    r"(price|cost|amount|revenue|sales|margin|salary|wage|gross|net|total)",
    re.IGNORECASE,
)
_QTY_COL_PAT = re.compile(
    r"(qty|quantity|stock|inventory|on_hand|available|count)",
    re.IGNORECASE,
)


def _col_kind(col: str) -> str:
    """Rough classification used for tailored examples."""
    c = (col or "").lower()
    if c == "*":
        return "wildcard"
    if _PII_COL_PAT.search(c):
        return "pii"
    if _MONEY_COL_PAT.search(c):
        return "money"
    if _QTY_COL_PAT.search(c):
        return "qty"
    return "other"


def _scope_phrase(filter_col: str | None) -> str:
    if not filter_col:
        return "the caller's scope"
    key = filter_col.lower()
    if "site" in key or "store" in key:
        return "the caller's store"
    if "branch" in key:
        return "the caller's branch"
    if "tenant" in key or "company" in key:
        return "the caller's company"
    if "region" in key:
        return "the caller's region"
    if "user" in key:
        return "the caller"
    return f"the caller's {filter_col}"


def explain_policy(policy: dict[str, Any]) -> dict[str, str]:
    """Return {what, who_sees, who_doesnt, example, risk_if_wrong}.

    Tailored by column kind (price/cost/email/qty/etc) where possible.
    """
    policy = policy or {}
    table = str(policy.get("table") or "?").strip()
    column = str(policy.get("column") or "?").strip()
    mode = str(policy.get("mode") or "").lower().strip()
    filter_col = policy.get("filter") or policy.get("claim")
    scope = _scope_phrase(filter_col)
    kind = _col_kind(column)
    bypass_roles = policy.get("bypass_roles") or []

    target = f"`{table}.{column}`" if column != "*" else f"all columns in `{table}`"

    out: dict[str, str] = {}

    if mode == "own_value":
        out["what"] = (
            f"Show the real `{column}` value only for rows where `{filter_col}` "
            f"matches {scope}. Other rows still appear, but with NULL in that column."
        )
        out["who_sees"] = f"{scope.capitalize()} sees actual values."
        out["who_doesnt"] = (
            "Callers from other scopes see NULL (interpreted as 'not your data')."
        )
        if kind == "qty":
            out["example"] = (
                f"Store A asks 'where is Paracetamol?' → sees own stock (e.g. 24 units) "
                "AND a list of other stores carrying it (qty shown as NULL)."
            )
        elif kind == "money":
            out["example"] = (
                f"Store A sees their own `{column}` (e.g. 12,500 MMK); other stores' "
                "rows appear with NULL in this column."
            )
        else:
            out["example"] = (
                f"{scope.capitalize()} sees the real `{column}` value; rows from "
                "other scopes appear but the value is NULL."
            )
        out["risk_if_wrong"] = (
            f"If `{filter_col}` is the wrong column, even {scope} sees NULL. "
            f"If you switch this to 'private' instead, other stores' rows are hidden "
            "completely — useful for secrecy, harmful when customers need to find a "
            "product across the network."
        )

    elif mode == "private":
        out["what"] = (
            f"Only rows in `{table}` matching {scope} are returned. Rows belonging "
            "to other scopes are filtered out entirely."
        )
        out["who_sees"] = f"{scope.capitalize()} sees only their own rows."
        out["who_doesnt"] = "Everyone else sees nothing from those rows — they appear to not exist."
        out["example"] = (
            f"Store A queries `{table}` → only Store A rows return. "
            "Store B rows are invisible, as if they were never inserted."
        )
        out["risk_if_wrong"] = (
            "Customers cannot discover anything outside their own scope. "
            "If you wanted them to see catalog rows from other stores (e.g. "
            "'where else is this drug sold?'), use 'own_value' instead."
        )

    elif mode == "hidden":
        out["what"] = f"Column {target} is removed from the response for callers outside {scope}."
        out["who_sees"] = f"{scope.capitalize()} sees the column normally." \
            if filter_col else "Only callers with a bypass role see this column."
        out["who_doesnt"] = "Everyone else gets the response without this column at all."
        if kind == "money":
            out["example"] = (
                f"Cost price `{column}` is hidden from store staff; only HQ sees the column. "
                "Other rows still appear, but the column itself is missing."
            )
        elif kind == "pii":
            out["example"] = (
                f"Customer `{column}` is hidden from non-owner stores; the row is still "
                "there, but the column is dropped from the response."
            )
        else:
            out["example"] = (
                f"`{column}` is dropped from the response unless the caller is in {scope} "
                f"or has a bypass role ({', '.join(bypass_roles) or 'none configured'})."
            )
        out["risk_if_wrong"] = (
            "Front-end code that always expects this column may break. Test the widget "
            "after switching from 'shared' to 'hidden'."
        )

    elif mode == "redacted":
        out["what"] = (
            f"Column {target} is shown as a placeholder (e.g. `***`) for callers "
            f"outside {scope}."
        )
        out["who_sees"] = f"{scope.capitalize()} sees real values."
        out["who_doesnt"] = "Everyone else sees a placeholder; the value itself is hidden."
        if kind == "pii":
            out["example"] = (
                f"Customer `{column}` shows as `j***@gmail.com` to non-owner stores; "
                "the owner store sees the full address."
            )
        elif kind == "money":
            out["example"] = (
                f"`{column}` shows as `***` to callers outside {scope}."
            )
        else:
            out["example"] = (
                f"`{column}` is masked as `***` for callers outside {scope}."
            )
        out["risk_if_wrong"] = (
            "Existence of the row is still visible — only the value is hidden. "
            "If row existence itself is secret, use 'private' or 'hidden'."
        )

    elif mode == "shared":
        out["what"] = f"Column {target} is visible to every caller, with no filtering."
        out["who_sees"] = "Everyone."
        out["who_doesnt"] = "Nobody — this column is fully public to anyone using the widget."
        if kind == "money":
            out["example"] = (
                f"`{column}` is shown to every caller. Only mark money columns as "
                "'shared' if you want public pricing (e.g. customer-facing catalog price)."
            )
        elif column == "*":
            out["example"] = (
                f"All columns in `{table}` are visible to every caller — typically used "
                "for reference tables like product catalog, categories, store list."
            )
        else:
            out["example"] = f"`{column}` is the same for every caller (e.g. product name, SKU code)."
        out["risk_if_wrong"] = (
            "Do not mark sensitive data (cost price, customer email, internal notes) "
            "as 'shared' — every caller will see it."
        )

    else:
        out["what"] = f"Custom mode '{mode}' applied to {target}."
        out["who_sees"] = "Depends on the embed's policy code."
        out["who_doesnt"] = "Depends on the embed's policy code."
        out["example"] = ""
        out["risk_if_wrong"] = "Behaviour undefined — review the embed's custom code."

    if bypass_roles:
        out["bypass"] = (
            f"Users with role(s) {', '.join(bypass_roles)} bypass this rule and see "
            "everything (typically HQ, auditor, super-admin)."
        )

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Required tables — schema compatibility check explanation
# ─────────────────────────────────────────────────────────────────────────────

def _jaccard(a: str, b: str) -> float:
    """Token Jaccard on _-split lowercase tokens."""
    ta = set(re.split(r"[^a-z0-9]+", a.lower())) - {""}
    tb = set(re.split(r"[^a-z0-9]+", b.lower())) - {""}
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def suggest_table_rename(
    missing: list[str],
    present_all: list[str],
    min_confidence: float = 0.5,
) -> list[dict[str, Any]]:
    """Fuzzy-match missing tables to existing ones via Jaccard on tokens.

    Returns list of {required, suggested, confidence} for each missing
    table where the best match scores above min_confidence.
    """
    missing = missing or []
    present_all = present_all or []
    suggestions: list[dict[str, Any]] = []
    for req in missing:
        best_name = None
        best_score = 0.0
        for cand in present_all:
            if cand.lower() == req.lower():
                continue  # already-matched would not be in `missing`
            score = _jaccard(req, cand)
            # Boost: candidate contains required name as a substring (e.g. "balance_stock_07052026" contains "balance_stock")
            if req.lower() in cand.lower():
                score = max(score, 0.85)
            if score > best_score:
                best_score = score
                best_name = cand
        if best_name and best_score >= min_confidence:
            suggestions.append({
                "required":   req,
                "suggested":  best_name,
                "confidence": round(best_score, 2),
            })
    return suggestions


def explain_required_tables(
    required: list[str],
    present: list[str],
    missing: list[str],
    present_all: list[str] | None = None,
) -> dict[str, Any]:
    """Return {status, message, impact_if_missing, what_to_do, suggestions:[...]}.

    Status is one of: 'ok', 'partial', 'incompatible'.
    """
    required = list(required or [])
    present = list(present or [])
    missing = list(missing or [])
    present_all = list(present_all or [])

    total = len(required)
    n_missing = len(missing)
    n_present = len(present)

    suggestions = suggest_table_rename(missing, present_all) if present_all else []

    if n_missing == 0:
        return {
            "status":            "ok",
            "message":           "All required tables are present.",
            "impact_if_missing": "",
            "what_to_do":        "You can apply the blueprint as-is.",
            "suggestions":       [],
        }

    if n_present == 0:
        status = "incompatible"
        msg = f"None of the {total} required tables exist in this project."
    else:
        status = "partial"
        msg = f"{n_missing} of {total} required tables are missing."

    impact = (
        f"Policies that target the missing table(s) ({', '.join(missing)}) "
        "will be skipped when you apply the blueprint. The blueprint will still "
        f"apply for the {n_present} table(s) that are present."
    )

    if suggestions:
        sug_lines = [
            f"Did you mean `{s['suggested']}` for `{s['required']}`? "
            f"(confidence {int(s['confidence'] * 100)}%)"
            for s in suggestions
        ]
        what_to_do = (
            "Possible matches found in your schema:\n" + "\n".join(sug_lines) +
            "\nEither rename the table to drop the date suffix, OR apply the "
            "blueprint anyway and edit it manually after."
        )
    else:
        what_to_do = (
            "Either upload data for the missing tables, OR apply the blueprint "
            "anyway and edit it manually after (skipped policies are returned in "
            "the 'skipped' list)."
        )

    return {
        "status":            status,
        "message":           msg,
        "impact_if_missing": impact,
        "what_to_do":        what_to_do,
        "suggestions":       suggestions,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Blueprint summary — tailored from name / industry / policies
# ─────────────────────────────────────────────────────────────────────────────

_INDUSTRY_TEMPLATES: dict[str, dict[str, Any]] = {
    "pharmacy": {
        "what_it_does": (
            "Lets each pharmacy store see their own stock without seeing other "
            "stores' numbers, while still letting customers find which other "
            "stores carry a drug."
        ),
        "who_uses_it": (
            "Customers in-store and pharmacists; the HQ role bypasses the rules "
            "to see all stores."
        ),
        "before_after": (
            "Before: anyone with widget access sees ALL store stock. "
            "After: the caller is scoped to their store_code; other stores' "
            "stock quantities come back as NULL."
        ),
        "common_questions_answered": [
            "How much stock do I have?",
            "Where else can a customer find this drug?",
            "What does this medicine cost?",
        ],
    },
    "retail": {
        "what_it_does": (
            "Each store sees its own sales, stock, and customer data; "
            "headquarters sees the network-wide view."
        ),
        "who_uses_it": "Store staff and managers; HQ and auditors bypass the scope.",
        "before_after": (
            "Before: every staff member sees every store's numbers. "
            "After: each store is scoped to its own data, with HQ as the only "
            "cross-store view."
        ),
        "common_questions_answered": [
            "What did my store sell today?",
            "What is in stock at my store?",
            "Which products are low on stock?",
        ],
    },
    "hospitality": {
        "what_it_does": (
            "Each hotel property sees its own bookings, occupancy, and revenue; "
            "HQ sees the full portfolio."
        ),
        "who_uses_it": "Front-desk staff, property managers; HQ bypasses scope.",
        "before_after": (
            "Before: any staff member can pull any property's data. "
            "After: scoped to property_code; revenue and ADR are private per property."
        ),
        "common_questions_answered": [
            "What is my occupancy tonight?",
            "What is my ADR this month?",
            "How many bookings do I have for next week?",
        ],
    },
    "saas": {
        "what_it_does": (
            "Each tenant (customer company) sees only their own data; "
            "no tenant ever sees another tenant's rows."
        ),
        "who_uses_it": "End-users within each tenant; super-admin bypasses.",
        "before_after": (
            "Before: shared database with no isolation. "
            "After: every query is scoped to tenant_id; cross-tenant leaks are blocked."
        ),
        "common_questions_answered": [
            "How many users do I have?",
            "What is my usage this month?",
            "Where is my data?",
        ],
    },
}

_GENERIC_TEMPLATE: dict[str, Any] = {
    "what_it_does": (
        "Restricts what each caller can see based on a scope claim (e.g. "
        "store_code, tenant_id) and protects sensitive columns."
    ),
    "who_uses_it": (
        "End-users of the embedded widget; bypass roles see everything."
    ),
    "before_after": (
        "Before: every caller sees every row. "
        "After: each caller is scoped to their own data; sensitive columns "
        "are hidden, redacted, or shown as NULL for other scopes."
    ),
    "common_questions_answered": [
        "What is my data?",
        "What can I share with my team?",
        "Who else has access?",
    ],
}


def _tagline(industry: str | None, name: str | None) -> str:
    ind = (industry or "").lower().strip()
    if ind == "pharmacy":
        return "Each pharmacy sees their own stock; customers can still find drugs at other stores."
    if ind == "retail":
        return "Each store sees their own sales; HQ sees the network."
    if ind == "hospitality":
        return "Each property sees its own bookings; HQ sees the portfolio."
    if ind == "saas":
        return "Each tenant sees only their own data; no cross-tenant leaks."
    return f"Scoped data access for the {name or 'embed'} widget."


def explain_blueprint_summary(bp: dict[str, Any]) -> dict[str, Any]:
    """Return {what_it_does, who_uses_it, before_after, common_questions_answered, tagline}.

    Tailored from blueprint name + industry. Falls back to a generic template
    for unknown industries.
    """
    bp = bp or {}
    industry = (bp.get("industry") or "").lower().strip()
    name = bp.get("name") or bp.get("slug") or "blueprint"

    tmpl = _INDUSTRY_TEMPLATES.get(industry, _GENERIC_TEMPLATE)
    out = {
        "what_it_does":              tmpl["what_it_does"],
        "who_uses_it":               tmpl["who_uses_it"],
        "before_after":              tmpl["before_after"],
        "common_questions_answered": list(tmpl["common_questions_answered"]),
        "tagline":                   _tagline(industry, name),
    }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Per-policy / per-claim batch enrichment helpers (used by the API layer)
# ─────────────────────────────────────────────────────────────────────────────

def explain_claims(claims: list[dict]) -> list[dict[str, Any]]:
    """Return [{claim, explanation}] for every claim."""
    out = []
    for c in claims or []:
        if not isinstance(c, dict):
            continue
        out.append({"claim": c, "explanation": explain_claim(c)})
    return out


def explain_policies(policies: list[dict]) -> list[dict[str, Any]]:
    """Return [{policy, explanation}] for every policy."""
    out = []
    for p in policies or []:
        if not isinstance(p, dict):
            continue
        out.append({"policy": p, "explanation": explain_policy(p)})
    return out


def explain_skipped(skipped: list[dict]) -> list[dict[str, Any]]:
    """Return [{policy, reason, impact}] for policies the apply step dropped.

    Each item has a `_skip_reason` field set by the validation step in
    app/embed.py (e.g. "table 'X' not found").
    """
    out = []
    for p in skipped or []:
        if not isinstance(p, dict):
            continue
        reason = str(p.get("_skip_reason") or "unknown")
        if "table" in reason and "not found" in reason:
            impact = (
                "This policy is skipped completely — the column it protects "
                "will fall back to default (typically 'shared'). Upload the "
                "table or rename an existing one to enable this rule."
            )
        elif "column" in reason and "not found" in reason:
            impact = (
                "This policy is skipped completely — the column it protects "
                "does not exist in your schema. Check for a typo or upload "
                "fresh data."
            )
        else:
            impact = "This policy is skipped; the column is not protected."
        out.append({
            "policy":      p,
            "reason":      reason,
            "explanation": (
                f"Skipped because: {reason}. "
                f"For column `{p.get('table')}.{p.get('column')}` in mode "
                f"'{p.get('mode')}'."
            ),
            "impact":      impact,
        })
    return out


def build_example_walkthrough(bp: dict[str, Any]) -> list[dict[str, str]]:
    """Auto-generate a 3-scene story showing how the blueprint behaves at runtime.

    Pulls from the first own_value policy + first hidden policy + first claim.
    Falls back to generic phrasing when specific data is missing.
    """
    bp = bp or {}
    industry = (bp.get("industry") or "").lower()
    policies = bp.get("policies") or []
    claims = bp.get("claims") or []

    own_pol = next((p for p in policies if str(p.get("mode") or "").lower() == "own_value"), None)
    hidden_pol = next((p for p in policies if str(p.get("mode") or "").lower() in ("hidden", "private")), None)

    scope_claim = (own_pol or {}).get("filter") or (claims[0].get("key") if claims else "scope")
    own_table = (own_pol or {}).get("table") or "data"
    own_col = (own_pol or {}).get("column") or "value"
    hidden_col = (hidden_pol or {}).get("column") if hidden_pol else None

    if industry == "pharmacy":
        q = "Where can I find Paracetamol?"
        result = (
            "Customer sees: Store A=24 units · Store B=available · Store C=available. "
            "Other stores' exact quantities are hidden."
        )
    elif industry == "retail":
        q = "What did my store sell today?"
        result = (
            f"Store A staff sees their own sales totals; other stores' rows appear with "
            f"NULL in `{own_col}`. HQ sees everything."
        )
    elif industry == "saas":
        q = "How many users do I have this month?"
        result = (
            f"Tenant A sees only their own usage rows; tenant B's data is invisible — "
            "queries return zero rows from other tenants."
        )
    elif industry == "hr":
        q = "What is my salary?"
        result = (
            "Employee sees their own record; salary column is hidden unless caller has "
            "role=hr."
        )
    elif industry == "banking":
        q = "What is the balance on account 12345?"
        result = (
            "Teller sees balance only if account belongs to their branch; SSN is always NULL."
        )
    elif industry == "healthcare":
        q = "Show me patient X's prescriptions."
        result = (
            "Clinical staff sees prescriptions only for patients in their clinic_id; "
            "billing.amount is NULL unless caller is role=billing."
        )
    else:
        q = f"Show me my {own_table}."
        result = (
            f"Caller sees own `{own_col}` values; other scopes return NULL "
            + (f"and `{hidden_col}` is hidden everywhere." if hidden_col else "for that column.")
        )

    return [
        {
            "scene":  "1. CUSTOMER ASKS",
            "actor":  "End user (authenticated with their scope claim)",
            "action": q,
            "result": "Question lands on the agent with the caller's claim attached.",
        },
        {
            "scene":  "2. AGENT RUNS SQL",
            "actor":  "Agent (with this blueprint applied)",
            "action": f"Agent issues SQL across all rows; the policy rewriter injects `{scope_claim}` filters and masks protected columns.",
            "result": "Query returns full network rows, but values from other scopes are NULL or hidden.",
        },
        {
            "scene":  "3. USER SEES RESULT",
            "actor":  "End user",
            "action": "Result is rendered in the widget.",
            "result": result,
        },
    ]


def build_blueprint_display(bp: dict[str, Any]) -> dict[str, Any]:
    """Compose the full `display` payload for GET /api/embed-rls-blueprints/{slug}.

    Returns the bundle that the frontend injects into the preview drawer.
    """
    bp = bp or {}
    return {
        "summary":            explain_blueprint_summary(bp),
        "mode_legend":        mode_legend(),
        "apply_modes":        apply_modes_legend(),
        "claims_explained":   explain_claims(bp.get("claims") or []),
        "policies_explained": explain_policies(bp.get("policies") or []),
        "example_walkthrough": build_example_walkthrough(bp),
        # Extra context fields surfaced from SYSTEM_BLUEPRINTS metadata
        # (injected by the API layer; passed through if present on bp)
        "tagline":            bp.get("tagline"),
        "who_is_this_for":    list(bp.get("who_is_this_for") or []),
        "common_pitfalls":    list(bp.get("common_pitfalls") or []),
        "next_steps":         list(bp.get("next_steps") or []),
        "faq":                list(bp.get("faq") or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tiny in-memory cache (60s) keyed by blueprint slug
# ─────────────────────────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_S = 60.0


def cached_blueprint_display(slug: str, bp: dict[str, Any]) -> dict[str, Any]:
    """Return display payload, caching by slug for 60s.

    The cache is keyed by slug only — callers must invalidate (or skip cache)
    after editing the blueprint. For the read-only library endpoint this is
    safe because blueprints rarely change.
    """
    now = time.time()
    hit = _CACHE.get(slug)
    if hit and (now - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    payload = build_blueprint_display(bp)
    _CACHE[slug] = (now, payload)
    return payload


def invalidate_cache(slug: str | None = None) -> None:
    """Drop one (or all) cached display payloads."""
    if slug is None:
        _CACHE.clear()
        return
    _CACHE.pop(slug, None)
