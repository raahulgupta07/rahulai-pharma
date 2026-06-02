"""Finance / FP&A vertical pack.

Detects: project schemas w/ GL, ledger, budget, forecast, cost-center, or
account tables. Targets monthly close + variance review + rolling forecast
workflows.

Two formats:
  PACK     — legacy {placeholder} template_sql, install-time bind.
  MDL_PACK — WrenAI-style logical model (`gl_transactions`, `budget`,
             `accounts`) w/ virtual cols + relationships.
"""
PACK = {
    "name": "finance_fpa",
    "vertical": "Finance / FP&A",
    "description": "GL variance + budget vs actual + rolling forecast + P&L workflows",
    "detect": {
        "required_tables_any": ["gl_transactions", "general_ledger", "ledger",
                                "budget_lines", "budget", "forecast_lines",
                                "forecast", "cost_centers", "accounts",
                                "chart_of_accounts"],
        "required_cols_any":   ["debit", "credit", "period", "posting_date",
                                "account_code", "cost_center", "budget_amount",
                                "actual_amount", "amount"],
    },
    "workflows": [
        {
            "name": "Monthly Variance Review",
            "description": "Largest budget-vs-actual variances for the period",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["gl_transactions", "general_ledger", "ledger"]},
                "cols": {
                    "account": ["account_code", "account", "gl_account"],
                    "amount": ["amount", "actual_amount", "posted_amount"],
                    "period": ["period", "posting_period", "fiscal_period"],
                },
            },
            "template_sql": "SELECT {account}, {period}, SUM({amount}) AS actual "
                            "FROM {table} "
                            "GROUP BY {account}, {period} "
                            "ORDER BY actual DESC LIMIT 100",
        },
        {
            "name": "Top Cost Centers",
            "description": "Cost centers ranked by total spend",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["gl_transactions", "general_ledger", "ledger"]},
                "cols": {
                    "cc": ["cost_center", "cc_code", "department_code"],
                    "amount": ["amount", "actual_amount", "posted_amount"],
                },
            },
            "template_sql": "SELECT {cc}, SUM({amount}) AS total_spend "
                            "FROM {table} GROUP BY {cc} "
                            "ORDER BY total_spend DESC LIMIT 50",
        },
        {
            "name": "Period P&L Summary",
            "description": "Debit vs credit totals per period",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["gl_transactions", "general_ledger", "ledger"]},
                "cols": {
                    "period": ["period", "posting_period", "fiscal_period"],
                    "debit": ["debit", "debit_amount", "dr"],
                    "credit": ["credit", "credit_amount", "cr"],
                },
            },
            "template_sql": "SELECT {period}, SUM({debit}) AS total_debit, "
                            "       SUM({credit}) AS total_credit "
                            "FROM {table} GROUP BY {period} "
                            "ORDER BY {period} DESC LIMIT 24",
        },
    ],
}


# ───────────────────────── MDL FORMAT (Phase 3) ───────────────────────────
MDL_PACK = {
    "name": "finance_fpa_mdl",
    "vertical": "Finance / FP&A",
    "description": "MDL-format FP&A pack. Logical `gl_transactions` + `budget` + "
                   "`accounts` + `cost_centers` models. Variance, BvA, rolling "
                   "forecast, EBITDA bridge workflows portable across ledger schemas.",
    "detect": {
        "required_tables_any": ["gl_transactions", "general_ledger", "ledger",
                                "budget_lines", "budget", "forecast_lines",
                                "forecast", "cost_centers", "accounts",
                                "chart_of_accounts"],
        "required_cols_any":   ["debit", "credit", "period", "posting_date",
                                "account_code", "cost_center", "budget_amount",
                                "actual_amount", "amount"],
    },
    "models": [
        {
            "name": "gl_transactions",
            "raw_table_aliases": [
                "gl_transactions", "general_ledger", "ledger", "gl_entries",
                "gl_postings", "journal_entries", "gl",
            ],
            "virtual_columns": [
                {"name": "account",     "aliases": ["account_code", "account",
                                                     "gl_account", "account_id"],
                 "type": "string"},
                {"name": "cost_center", "aliases": ["cost_center", "cc_code",
                                                     "department_code", "dept_code"],
                 "type": "string"},
                {"name": "period",      "aliases": ["period", "posting_period",
                                                     "fiscal_period", "month",
                                                     "accounting_period"],
                 "type": "string"},
                {"name": "posting_date","aliases": ["posting_date", "post_date",
                                                     "transaction_date", "txn_date",
                                                     "entry_date"],
                 "type": "date"},
                {"name": "debit",       "aliases": ["debit", "debit_amount", "dr",
                                                     "dr_amount"],
                 "type": "numeric"},
                {"name": "credit",      "aliases": ["credit", "credit_amount", "cr",
                                                     "cr_amount"],
                 "type": "numeric"},
                {"name": "amount",      "aliases": ["amount", "actual_amount",
                                                     "posted_amount", "net_amount"],
                 "type": "numeric"},
                # Derived
                {"name": "net_amount",  "expression": "COALESCE(debit, 0) - COALESCE(credit, 0)",
                 "type": "numeric"},
            ],
            "relationships": [
                {"model": "accounts", "on": "account = accounts.code",
                 "type": "many_to_one", "optional": True},
                {"model": "cost_centers", "on": "cost_center = cost_centers.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "budget",
            "raw_table_aliases": [
                "budget_lines", "budget", "budgets", "annual_budget",
                "budget_plan",
            ],
            "virtual_columns": [
                {"name": "account",     "aliases": ["account_code", "account",
                                                     "gl_account"],
                 "type": "string"},
                {"name": "cost_center", "aliases": ["cost_center", "cc_code",
                                                     "department_code"],
                 "type": "string"},
                {"name": "period",      "aliases": ["period", "budget_period",
                                                     "fiscal_period", "month"],
                 "type": "string"},
                {"name": "budget_amount", "aliases": ["budget_amount", "budget",
                                                       "amount", "planned_amount"],
                 "type": "numeric"},
            ],
            "relationships": [
                {"model": "accounts", "on": "account = accounts.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "forecast",
            "raw_table_aliases": [
                "forecast_lines", "forecast", "forecasts", "rolling_forecast",
            ],
            "virtual_columns": [
                {"name": "account",     "aliases": ["account_code", "account"],
                 "type": "string"},
                {"name": "period",      "aliases": ["period", "forecast_period",
                                                     "fiscal_period"],
                 "type": "string"},
                {"name": "forecast_amount", "aliases": ["forecast_amount",
                                                         "forecast", "amount",
                                                         "predicted_amount"],
                 "type": "numeric"},
            ],
            "relationships": [],
        },
        {
            "name": "accounts",
            "raw_table_aliases": [
                "accounts", "chart_of_accounts", "coa", "gl_accounts",
                "account_master",
            ],
            "virtual_columns": [
                {"name": "code",        "aliases": ["account_code", "code",
                                                     "account_id"],
                 "type": "string"},
                {"name": "name",        "aliases": ["account_name", "name",
                                                     "description"],
                 "type": "string"},
                {"name": "account_type","aliases": ["account_type", "type",
                                                     "category"],
                 "type": "string"},
            ],
            "relationships": [],
        },
        {
            "name": "cost_centers",
            "raw_table_aliases": [
                "cost_centers", "cost_center_master", "departments",
                "dept_master",
            ],
            "virtual_columns": [
                {"name": "code",        "aliases": ["cc_code", "code",
                                                     "cost_center_code",
                                                     "department_code"],
                 "type": "string"},
                {"name": "name",        "aliases": ["cc_name", "name",
                                                     "department_name"],
                 "type": "string"},
            ],
            "relationships": [],
        },
    ],
    "workflows": [
        {
            "name": "Monthly Variance Review",
            "description": "Top accounts with largest actual vs budget variance",
            "action": "post_insight",
            "model": "gl_transactions",
            "sql": "SELECT g.account, g.period, "
                   "       SUM(g.amount) AS actual, "
                   "       COALESCE(SUM(b.budget_amount), 0) AS budget, "
                   "       SUM(g.amount) - COALESCE(SUM(b.budget_amount), 0) AS variance_abs "
                   "FROM gl_transactions g "
                   "LEFT JOIN budget b ON g.account = b.account AND g.period = b.period "
                   "GROUP BY g.account, g.period "
                   "ORDER BY ABS(variance_abs) DESC LIMIT 50",
        },
        {
            "name": "Budget vs Actual",
            "description": "Period-level budget vs actual with variance %",
            "action": "post_insight",
            "model": "gl_transactions",
            "sql": "SELECT g.period, "
                   "       SUM(g.amount) AS actual, "
                   "       COALESCE(SUM(b.budget_amount), 0) AS budget, "
                   "       CASE WHEN SUM(b.budget_amount) > 0 "
                   "            THEN ROUND(100.0 * (SUM(g.amount) - SUM(b.budget_amount)) "
                   "                       / SUM(b.budget_amount), 2) "
                   "            ELSE NULL END AS variance_pct "
                   "FROM gl_transactions g "
                   "LEFT JOIN budget b ON g.account = b.account AND g.period = b.period "
                   "GROUP BY g.period ORDER BY g.period DESC LIMIT 24",
        },
        {
            "name": "Rolling Forecast",
            "description": "Forecast vs actual per account for trend tracking",
            "action": "post_insight",
            "model": "gl_transactions",
            "sql": "SELECT g.account, g.period, "
                   "       SUM(g.amount) AS actual, "
                   "       COALESCE(SUM(f.forecast_amount), 0) AS forecast, "
                   "       SUM(g.amount) - COALESCE(SUM(f.forecast_amount), 0) AS fcst_vs_actual "
                   "FROM gl_transactions g "
                   "LEFT JOIN forecast f ON g.account = f.account AND g.period = f.period "
                   "GROUP BY g.account, g.period "
                   "ORDER BY g.period DESC, ABS(fcst_vs_actual) DESC LIMIT 100",
        },
        {
            "name": "P&L Summary",
            "description": "Net P&L per period (debit - credit)",
            "action": "post_insight",
            "model": "gl_transactions",
            "sql": "SELECT period, "
                   "       SUM(debit) AS total_debit, "
                   "       SUM(credit) AS total_credit, "
                   "       SUM(net_amount) AS net_pnl "
                   "FROM gl_transactions GROUP BY period "
                   "ORDER BY period DESC LIMIT 24",
        },
        {
            "name": "EBITDA Bridge",
            "description": "Period-over-period EBITDA delta by account type",
            "action": "post_insight",
            "model": "gl_transactions",
            "sql": "SELECT g.period, a.account_type, "
                   "       SUM(g.net_amount) AS contribution "
                   "FROM gl_transactions g "
                   "LEFT JOIN accounts a ON g.account = a.code "
                   "WHERE a.account_type IN ('revenue', 'cogs', 'opex', 'expense') "
                   "GROUP BY g.period, a.account_type "
                   "ORDER BY g.period DESC, contribution DESC LIMIT 60",
        },
    ],
}
