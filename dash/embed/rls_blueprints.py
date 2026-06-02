"""RLS Blueprints — system presets + seeder.

A blueprint is a preset bundle of `claims + policies + description + required_tables`
that can be one-click applied to an embed's RLS config. Two sources:

- System (shipped, seeded here on lifespan startup, ``is_system=TRUE``)
- User-saved (created via POST /api/embed-rls-blueprints, ``is_system=FALSE``)

``seed_system_blueprints(engine)`` upserts each SYSTEM_BLUEPRINTS entry on
``slug`` PK with ``is_system=TRUE``. Idempotent: re-running overwrites the
preset definitions but never touches user-saved blueprints (different slugs).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Helpers for terse claim/policy authoring ────────────────────────────────
def _c(key: str, ctype: str = "string", required: bool = False,
       label: str | None = None, values: list[str] | None = None) -> dict:
    out: dict[str, Any] = {
        "key": key,
        "label": label or key.replace("_", " ").title(),
        "type": ctype,
        "required": required,
    }
    if values:
        out["values"] = values
    return out


def _p(table: str, column: str, mode: str, filter_: str | None = None,
       bypass_roles: list[str] | None = None) -> dict:
    """mode: private | shared | redacted | hidden | own_value"""
    out: dict[str, Any] = {"table": table, "column": column, "mode": mode}
    if filter_:
        out["filter"] = filter_
    if bypass_roles:
        out["bypass_roles"] = bypass_roles
    return out


# ── Shipped system blueprints ───────────────────────────────────────────────
SYSTEM_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "slug": "pharmacy",
        "name": "Pharmacy / Multi-Site Retail",
        "industry": "pharmacy",
        "icon": "💊",
        "tagline": "Each pharmacy sees only its own stock; customers can still discover availability across the network.",
        "description": (
            "Per-site stock visibility for pharmacy chains. Each site sees only "
            "its own stock_qty; weighted_cost_price is hidden from all roles. "
            "Catalog (articles_list) is shared across all sites."
        ),
        "required_tables": ["balance_stock", "articles_list"],
        "who_is_this_for": [
            "Pharmacy chain operating multiple branches",
            "Customer-facing in-store kiosks or web widgets",
            "Pharmacist staff app (per-site)",
            "HQ analytics / supply-chain team",
        ],
        "common_pitfalls": [
            "If your stock table is named `inventory` or `stock_balance` instead of `balance_stock`, the apply will skip those policies — rename via IMPORT FROM SCHEMA or save as a custom blueprint.",
            "Missing `site_code` claim → every request will be denied. Pass it from your auth layer.",
            "Setting cost columns to `shared` instead of `hidden` leaks margin to staff and customers.",
        ],
        "next_steps": [
            "Assign HQ users role=hq so they bypass per-site filters.",
            "Test as a Store A staff user: they should see Store A stock as numbers and other stores as 'available' (NULL qty).",
            "Add a `region` claim if you want regional managers to span 3-5 stores.",
        ],
        "faq": [
            {"q": "Can HQ see all stores?", "a": "Yes — assign HQ users role=hq. The bypass_roles list lets them see actual values across every site."},
            {"q": "Will customers see other stores' quantities?", "a": "No — own_value mode shows the actual number only for the caller's store. Other stores appear in the result list but with NULL quantities (interpreted as 'available, exact count hidden')."},
            {"q": "Is cost price ever visible to staff?", "a": "No — weighted_cost_price is mode=hidden for everyone except HQ. Staff queries return NULL even with direct SQL."},
            {"q": "What if a staff member moves between stores?", "a": "Update their site_code claim in your auth layer; the new value takes effect on the next request."},
        ],
        "claims": [
            _c("site_code", "string", required=True, label="Site Code"),
            _c("role", "enum", required=False, label="Role",
               values=["staff", "manager", "hq"]),
        ],
        "policies": [
            _p("balance_stock", "stock_qty", "own_value", filter_="site_code",
               bypass_roles=["hq"]),
            _p("balance_stock", "weighted_cost_price", "hidden",
               bypass_roles=["hq"]),
            _p("balance_stock", "site_code", "shared"),
            _p("balance_stock", "article_code", "shared"),
            _p("articles_list", "*", "shared"),
        ],
    },
    {
        "slug": "retail_multi_store",
        "name": "Retail Multi-Store",
        "industry": "retail",
        "icon": "🛒",
        "tagline": "Each store sees only its own sales and stock; HQ sees the network.",
        "description": (
            "Store-scoped inventory + sales for retail chains. Each store sees "
            "only its own quantities and sales amounts. Cost fields hidden from "
            "non-HQ roles. Product catalog shared across stores."
        ),
        "required_tables": ["inventory", "sales", "products"],
        "who_is_this_for": [
            "Multi-store retail chain (5+ locations)",
            "Store managers needing per-location P&L",
            "Regional managers spanning multiple stores",
            "HQ merchandising and finance teams",
        ],
        "common_pitfalls": [
            "If your sales table is `transactions` or `pos_sales`, the apply skips those policies — rename or save a custom blueprint.",
            "Forgetting to populate `region` for regional managers — they'll only see their home store.",
            "Marking `cost_price` as `shared` accidentally exposes margin to store staff.",
        ],
        "next_steps": [
            "Assign HQ users role=hq for cross-store visibility.",
            "Test as a store user — they should see only their store's rows in inventory and sales.",
            "Add per-region bypass via a custom claim if regional managers need 3-5 stores.",
        ],
        "faq": [
            {"q": "Can a manager see multiple stores?", "a": "Yes — give them role=manager and set region; combine with a region-based filter or use hq role for full visibility."},
            {"q": "Are product catalogs shared?", "a": "Yes — products table is mode=shared so every store sees the same SKU master."},
            {"q": "Will cost leak to staff?", "a": "No — cost_price and cost columns are hidden except for HQ."},
        ],
        "claims": [
            _c("store_id", "string", required=True, label="Store"),
            _c("region", "string", required=False, label="Region"),
            _c("role", "enum", required=False, label="Role",
               values=["staff", "manager", "hq"]),
        ],
        "policies": [
            _p("inventory", "qty", "own_value", filter_="store_id",
               bypass_roles=["hq"]),
            _p("sales", "amount", "own_value", filter_="store_id",
               bypass_roles=["hq"]),
            _p("inventory", "cost_price", "hidden", bypass_roles=["hq"]),
            _p("inventory", "cost", "hidden", bypass_roles=["hq"]),
            _p("sales", "cost", "hidden", bypass_roles=["hq"]),
            _p("products", "*", "shared"),
        ],
    },
    {
        "slug": "saas_b2b",
        "name": "SaaS B2B Multi-Tenant",
        "industry": "saas",
        "icon": "☁️",
        "tagline": "Hard tenant isolation — every row scoped to tenant_id, no cross-tenant leaks.",
        "description": (
            "Hard tenant isolation for B2B SaaS. Every row in organizations, "
            "billing, and usage tables is scoped to the caller's tenant_id. "
            "Cross-tenant reads are denied."
        ),
        "required_tables": ["organizations", "billing", "usage"],
        "who_is_this_for": [
            "B2B SaaS platform with shared schema",
            "Multi-tenant analytics dashboards",
            "Embedded customer-facing reports",
            "Internal admin tools needing tenant scope",
        ],
        "common_pitfalls": [
            "Forgetting to mint a tenant_id claim → all requests rejected (private mode requires the filter).",
            "Using `org_id` or `account_id` instead of `tenant_id` → rename the claim or save a custom blueprint.",
            "Super-admin users need an explicit bypass role; otherwise even admin SQL is scoped.",
        ],
        "next_steps": [
            "Add a `super_admin` role and include it as bypass on all 3 policies if you need a god-mode dashboard.",
            "Test cross-tenant: tenant A's request returning tenant B's rows is a critical bug.",
            "Hook plan_tier to feature gates in your widget.",
        ],
        "faq": [
            {"q": "Can two tenants ever see each other's data?", "a": "No — private mode filters every row by tenant_id. There is no scenario where tenant A sees tenant B unless you add a bypass role."},
            {"q": "How do super-admins query across tenants?", "a": "Add a role claim and include it in bypass_roles, or use a separate admin embed without RLS."},
            {"q": "Does this work with row-level usage analytics?", "a": "Yes — usage table is private+tenant_scoped, so aggregations only include the caller's own usage."},
        ],
        "claims": [
            _c("tenant_id", "string", required=True, label="Tenant"),
            _c("plan_tier", "enum", required=False, label="Plan",
               values=["free", "starter", "pro", "enterprise"]),
        ],
        "policies": [
            _p("organizations", "*", "private", filter_="tenant_id"),
            _p("billing", "*", "private", filter_="tenant_id"),
            _p("usage", "*", "private", filter_="tenant_id"),
        ],
    },
    {
        "slug": "hr_analytics",
        "name": "HR Analytics",
        "industry": "hr",
        "icon": "👥",
        "tagline": "Employees see themselves, managers see reports, HR sees all — salaries hidden except for HR.",
        "description": (
            "Employees see only their own record; managers see direct reports; "
            "HR sees all. Salaries and payroll amounts are hidden except for HR. "
            "Sensitive compensation columns never leave the database for non-HR roles."
        ),
        "required_tables": ["employees", "salaries", "payroll"],
        "who_is_this_for": [
            "Self-service employee portal",
            "Manager dashboards (1-up view)",
            "HR business partners with full visibility",
            "Executive comp-band analytics",
        ],
        "common_pitfalls": [
            "If managers can't see direct reports, double-check the `manager_id` column on employees and add it as a custom filter.",
            "Setting salaries to `redacted` (rounded bands) instead of `hidden` may still let staff infer comp.",
            "Forgetting role=hr → even HR users see NULL salaries.",
        ],
        "next_steps": [
            "Tag every user with role: self, manager, or hr.",
            "Build a comp-band view (mode=redacted) for finance partners who need ranges but not exact figures.",
            "Add audit log queries to detect salary access patterns.",
        ],
        "faq": [
            {"q": "Can a manager see their boss's salary?", "a": "No — the manager bypass only covers reports, not upward. Only role=hr sees all."},
            {"q": "Why is the employees table own_value instead of private?", "a": "own_value lets you keep org-chart context (other employees appear) while masking sensitive columns. Use private if you want to hide the row entirely."},
            {"q": "How do contractors fit in?", "a": "Add a contractor role and exclude them from manager bypass."},
        ],
        "claims": [
            _c("employee_id", "string", required=True, label="Employee"),
            _c("dept_id", "string", required=False, label="Department"),
            _c("role", "enum", required=True, label="Role",
               values=["self", "manager", "hr"]),
        ],
        "policies": [
            _p("salaries", "*", "hidden", bypass_roles=["hr"]),
            _p("employees", "*", "own_value", filter_="employee_id",
               bypass_roles=["hr", "manager"]),
            _p("payroll", "amount", "hidden", bypass_roles=["hr"]),
        ],
    },
    {
        "slug": "banking_branch",
        "name": "Banking — Branch Scoped",
        "industry": "banking",
        "icon": "🏦",
        "tagline": "Tellers see only their branch; regional and compliance bypass; SSN always hidden.",
        "description": (
            "Branch-scoped account balances and transactions for retail banking. "
            "Tellers see only their branch's data. Customer SSN is always hidden. "
            "Cross-branch reads denied for non-regional roles."
        ),
        "required_tables": ["accounts", "transactions", "customers"],
        "who_is_this_for": [
            "Retail bank with branch-level access",
            "Teller workstations",
            "Regional managers spanning 10-30 branches",
            "Compliance / AML audit teams",
        ],
        "common_pitfalls": [
            "Missing `branch_id` claim → all teller requests rejected.",
            "Setting customers.ssn to anything other than `hidden` is a regulatory red flag.",
            "Compliance users must have role=compliance — without it, they cannot run audits.",
        ],
        "next_steps": [
            "Confirm regional manager role on a sample user; they should see balances across their region.",
            "Add a `pii_visible` claim if you need tier-2 KYC viewers.",
            "Hook teller audit log to dash_embed_rls_audit for compliance reports.",
        ],
        "faq": [
            {"q": "Why is SSN globally hidden?", "a": "Regulatory baseline — even compliance users see it only via a separate explicit lookup, not in normal queries."},
            {"q": "Can a regional manager pull all branches at once?", "a": "Yes — role=regional bypasses branch_id filter on accounts and transactions."},
            {"q": "What if branches reorganize?", "a": "Update the branch_id claim for affected users; no schema change needed."},
        ],
        "claims": [
            _c("branch_id", "string", required=True, label="Branch"),
            _c("region", "string", required=False, label="Region"),
            _c("role", "enum", required=False, label="Role",
               values=["teller", "manager", "regional", "compliance"]),
        ],
        "policies": [
            _p("accounts", "balance", "own_value", filter_="branch_id",
               bypass_roles=["regional", "compliance"]),
            _p("transactions", "*", "private", filter_="branch_id",
               bypass_roles=["regional", "compliance"]),
            _p("customers", "ssn", "hidden", bypass_roles=["compliance"]),
        ],
    },
    {
        "slug": "healthcare_clinic",
        "name": "Healthcare — Clinic Scoped",
        "industry": "healthcare",
        "icon": "🏥",
        "tagline": "Each clinic sees only its own patients; billing hidden from clinical staff.",
        "description": (
            "Clinic-scoped patient records and prescriptions. Each clinic sees "
            "only its own patients. Billing amounts hidden from clinical staff "
            "(visible to billing/admin roles only)."
        ),
        "required_tables": ["patients", "billing", "prescriptions"],
        "who_is_this_for": [
            "Multi-clinic medical group",
            "Front-desk and clinical staff portals",
            "Billing back-office team",
            "Clinic-network admin dashboard",
        ],
        "common_pitfalls": [
            "Missing `clinic_id` claim → all patient queries rejected.",
            "If your prescriptions table is `rx` or `medications`, the apply skips that policy.",
            "Letting clinical staff see billing amounts violates the staff/finance split.",
        ],
        "next_steps": [
            "Tag billing/admin users with role=billing or role=admin to unlock billing view.",
            "Add a `referring_clinic` filter if patients move between clinics.",
            "Audit prescription access via dash_embed_rls_audit.",
        ],
        "faq": [
            {"q": "Can a patient appear in two clinics?", "a": "Only if you duplicate the row with both clinic_id values, or add a junction table and adjust the filter."},
            {"q": "Will the doctor see billing amounts?", "a": "No — billing.amount is hidden for everyone except role=billing or role=admin."},
            {"q": "Is this HIPAA compliant?", "a": "RLS is one control; you still need TLS, audit logging, BAA agreements, and access reviews. This blueprint enforces the data-layer scope."},
        ],
        "claims": [
            _c("clinic_id", "string", required=True, label="Clinic"),
            _c("license_tier", "enum", required=False, label="License",
               values=["basic", "premium", "enterprise"]),
        ],
        "policies": [
            _p("patients", "*", "private", filter_="clinic_id"),
            _p("billing", "amount", "hidden", bypass_roles=["billing", "admin"]),
            _p("prescriptions", "*", "private", filter_="clinic_id"),
        ],
    },
]


# ── Seeder ──────────────────────────────────────────────────────────────────
_UPSERT_SQL = text(
    """
    INSERT INTO public.dash_embed_rls_blueprints
        (slug, name, industry, icon, description, claims, policies,
         required_tables, is_system, created_by, popularity)
    VALUES
        (:slug, :name, :industry, :icon, :description,
         CAST(:claims AS jsonb), CAST(:policies AS jsonb),
         :required_tables, TRUE, NULL, 0)
    ON CONFLICT (slug) DO UPDATE SET
        name            = EXCLUDED.name,
        industry        = EXCLUDED.industry,
        icon            = EXCLUDED.icon,
        description     = EXCLUDED.description,
        claims          = EXCLUDED.claims,
        policies        = EXCLUDED.policies,
        required_tables = EXCLUDED.required_tables,
        is_system       = TRUE
    """
)


def seed_system_blueprints(engine) -> dict:
    """UPSERT every SYSTEM_BLUEPRINTS entry on slug PK with is_system=TRUE.

    Idempotent — safe to call on every lifespan start. Returns
    ``{"seeded": int, "errors": list}``.
    """
    seeded = 0
    errors: list[str] = []
    try:
        with engine.begin() as conn:
            for bp in SYSTEM_BLUEPRINTS:
                try:
                    conn.execute(_UPSERT_SQL, {
                        "slug": bp["slug"],
                        "name": bp["name"],
                        "industry": bp.get("industry"),
                        "icon": bp.get("icon"),
                        "description": bp.get("description"),
                        "claims": json.dumps(bp.get("claims") or []),
                        "policies": json.dumps(bp.get("policies") or []),
                        "required_tables": list(bp.get("required_tables") or []),
                    })
                    seeded += 1
                except Exception as e:
                    errors.append(f"{bp.get('slug')}: {e}")
                    logger.warning("seed_system_blueprints: %s failed: %s",
                                   bp.get("slug"), e)
    except Exception as e:
        errors.append(f"engine: {e}")
        logger.warning("seed_system_blueprints engine error: %s", e)
    logger.info("seed_system_blueprints: seeded=%d errors=%d", seeded, len(errors))
    return {"seeded": seeded, "errors": errors}
