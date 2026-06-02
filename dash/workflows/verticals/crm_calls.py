"""CRM / call-center vertical pack.

Detects: project schemas w/ call-tracking columns (call_outcome, call_type,
status, contact, channel). Matches the proj_demo_pg_crm shape (P&G CRM).

Two pack formats coexist:

* `PACK` — legacy {table}/{col} placeholder resolution at install time.
  Pack installer rewrites template_sql with real names. Used by current
  `verticals/__init__.py` resolver.

* `MDL_PACK` — WrenAI-style semantic model. Declares ONE logical model
  (`customer_calls`) w/ virtual_columns + relationships. Workflows write
  SQL against logical names. Pack installer stamps the MDL into
  `dash_metric_definitions` (model_name + raw_table_ref + virtual_columns).
  At runtime, `dash.semantic.compile_query` rewrites semantic SQL → raw SQL.

Migration path: new pack installs use MDL_PACK. Existing PACK installs
continue to work via legacy resolver. Both can coexist on same project.
"""
PACK = {
    "name": "crm_calls",
    "vertical": "CRM / call-center",
    "description": "Call-tracking + retention + contact analytics for sales/support CRMs",
    "detect": {
        "required_tables_any": ["crm", "call", "contact", "interaction", "lead"],
        "required_cols_any":   ["call_outcome", "call_type", "status",
                                "contact_name", "channel", "outcome"],
    },
    "workflows": [
        {
            "name": "Daily Outcome Distribution",
            "description": "Status × outcome breakdown for the latest period",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["crm", "calls", "interactions"]},
                "cols": {
                    "status": ["status", "call_status"],
                    "outcome": ["call_outcome", "outcome", "result"],
                },
            },
            "template_sql": "SELECT {status}, {outcome}, COUNT(*) AS n "
                            "FROM {table} "
                            "GROUP BY 1, 2 ORDER BY n DESC LIMIT 50",
        },
        {
            "name": "Call Type Performance",
            "description": "Volume + success rate per call type",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["crm", "calls", "interactions"]},
                "cols": {
                    "ctype": ["call_type", "type"],
                    "outcome": ["call_outcome", "outcome", "result"],
                },
            },
            "template_sql": "SELECT {ctype}, COUNT(*) AS calls, "
                            "       COUNT(*) FILTER (WHERE {outcome} ILIKE '%success%') AS successful "
                            "FROM {table} "
                            "GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
        {
            "name": "Top Brands by Conversion",
            "description": "Per-brand call volume + conversion rate",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["crm", "calls", "interactions"]},
                "cols": {
                    "brand": ["brand", "product_name", "related_channel_response__brand"],
                    "outcome": ["call_outcome", "outcome", "result"],
                },
            },
            "template_sql": "SELECT COALESCE({brand}, '(no brand)') AS brand, "
                            "       COUNT(*) AS calls, "
                            "       COUNT(*) FILTER (WHERE {outcome} ILIKE '%success%') AS won "
                            "FROM {table} "
                            "GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
        {
            "name": "Unsuccessful Call Reasons",
            "description": "Top reasons for unsuccessful or uncontactable calls",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["crm", "calls", "interactions"]},
                "cols": {
                    "reason": ["unsuccessful_reason__affiliate_value_name",
                               "uncontactable_reason__affiliate_value_name",
                               "failure_reason", "reason"],
                    "outcome": ["call_outcome", "outcome", "result"],
                },
            },
            "template_sql": "SELECT COALESCE({reason}, '(none)') AS reason, "
                            "       COUNT(*) AS n "
                            "FROM {table} "
                            "WHERE {outcome} NOT ILIKE '%success%' "
                            "GROUP BY 1 ORDER BY n DESC LIMIT 50",
        },
        {
            "name": "Channel Mix",
            "description": "Calls per channel + conversion",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["crm", "calls", "interactions"]},
                "cols": {
                    "channel": ["channel_type", "channel", "channel__channel_name"],
                    "outcome": ["call_outcome", "outcome", "result"],
                },
            },
            "template_sql": "SELECT COALESCE({channel}, '(no channel)') AS channel, "
                            "       COUNT(*) AS calls, "
                            "       COUNT(*) FILTER (WHERE {outcome} ILIKE '%success%') AS won "
                            "FROM {table} "
                            "GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
    ],
}


# ───────────────────────── MDL FORMAT (Phase 3) ───────────────────────────
# Logical model 'customer_calls' = clean names that survive across schemas.
# Pack installer:
#   1. Detects raw_table (alias scan)
#   2. Maps each virtual_column to its raw expression (alias scan per col)
#   3. INSERTs into dash_metric_definitions w/ model_name='customer_calls'
#   4. Stamps workflows that reference 'customer_calls' — no further binding
#      needed since `compile_query` rewrites at runtime.
MDL_PACK = {
    "name": "crm_calls_mdl",
    "vertical": "CRM / call-center",
    "description": "MDL/WrenAI-style: logical 'customer_calls' model w/ "
                   "virtual cols. Workflows portable across CRM schemas.",
    "detect": {
        "required_tables_any": ["crm", "call", "contact", "interaction", "lead"],
        "required_cols_any":   ["call_outcome", "call_type", "status",
                                "contact_name", "channel", "outcome"],
    },
    "models": [
        {
            "name": "customer_calls",
            "raw_table_aliases": ["crm", "calls", "interactions", "crm_jun_2025",
                                  "crm_data", "call_log"],
            "virtual_columns": [
                # name → list of raw-column aliases (first match wins)
                {"name": "call_id",       "aliases": ["id", "call_id", "interaction_id",
                                              "interaction_uid", "case_id",
                                              "record_id", "ticket_id",
                                              "session_id", "contact_id",
                                              "lead_id", "event_id"],
                 "type": "string"},
                {"name": "outcome",       "aliases": ["call_outcome", "outcome", "result"],
                 "type": "string"},
                {"name": "call_type",     "aliases": ["call_type", "type", "interaction_type"],
                 "type": "string"},
                {"name": "channel",       "aliases": ["channel_type", "channel",
                                                       "channel__channel_name"],
                 "type": "string"},
                {"name": "brand",         "aliases": ["brand", "product_name",
                                                       "related_channel_response__brand"],
                 "type": "string"},
                {"name": "status",        "aliases": ["status", "call_status"],
                 "type": "string"},
                {"name": "unsuccess_reason", "aliases": [
                    "unsuccessful_reason__affiliate_value_name",
                    "uncontactable_reason__affiliate_value_name",
                    "failure_reason", "reason"], "type": "string"},
                # Derived flag: works on any project once `outcome` resolved
                {"name": "was_successful",
                 "expression": "outcome ILIKE '%success%'",
                 "type": "boolean"},
            ],
            "relationships": [
                # Optional. Filled when project also has brands master.
                {"model": "brands", "on": "brand = brands.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
    ],
    # Workflows now write SQL against the LOGICAL model. Compiler resolves.
    # No {placeholders} — pack is data, not templated code.
    "workflows": [
        {
            "name": "Daily Outcome Distribution",
            "description": "Status × outcome breakdown",
            "action": "post_insight",
            "model": "customer_calls",
            "sql": "SELECT status, outcome, COUNT(*) AS n "
                   "FROM customer_calls GROUP BY 1, 2 ORDER BY n DESC LIMIT 50",
        },
        {
            "name": "Call Type Performance",
            "description": "Volume + success rate per call type",
            "action": "post_insight",
            "model": "customer_calls",
            "sql": "SELECT call_type, COUNT(*) AS calls, "
                   "       COUNT(*) FILTER (WHERE was_successful) AS successful "
                   "FROM customer_calls GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
        {
            "name": "Top Brands by Conversion",
            "description": "Per-brand call volume + conversion rate",
            "action": "post_insight",
            "model": "customer_calls",
            "sql": "SELECT COALESCE(brand, '(no brand)') AS brand, "
                   "       COUNT(*) AS calls, "
                   "       COUNT(*) FILTER (WHERE was_successful) AS won "
                   "FROM customer_calls GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
        {
            "name": "Unsuccessful Call Reasons",
            "description": "Top reasons for unsuccessful or uncontactable calls",
            "action": "post_insight",
            "model": "customer_calls",
            "sql": "SELECT COALESCE(unsuccess_reason, '(none)') AS reason, "
                   "       COUNT(*) AS n FROM customer_calls "
                   "WHERE NOT was_successful GROUP BY 1 ORDER BY n DESC LIMIT 50",
        },
        {
            "name": "Channel Mix",
            "description": "Calls per channel + conversion",
            "action": "post_insight",
            "model": "customer_calls",
            "sql": "SELECT COALESCE(channel, '(no channel)') AS channel, "
                   "       COUNT(*) AS calls, "
                   "       COUNT(*) FILTER (WHERE was_successful) AS won "
                   "FROM customer_calls GROUP BY 1 ORDER BY calls DESC LIMIT 50",
        },
    ],
}
