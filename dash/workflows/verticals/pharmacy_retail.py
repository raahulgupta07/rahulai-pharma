"""Pharmacy / retail inventory vertical pack.

Detects: project schemas w/ inventory + stock + SKU columns.

Two formats:
  PACK     — legacy {placeholder} template_sql, install-time bind.
  MDL_PACK — WrenAI-style logical model (`inventory`) w/ virtual cols
             + relationships. Workflows write SQL against logical names;
             dash.semantic.compile_query rewrites raw at exec time.
"""
PACK = {
    "name": "pharmacy_retail",
    "vertical": "Pharmacy / retail inventory",
    "description": "Stock / inventory / SKU velocity + expiry + reorder workflows",
    "detect": {
        "required_tables_any": ["inventory", "stock", "balance_stock",
                                "sku", "products", "items", "article"],
        "required_cols_any":   ["stock_qty", "on_hand", "qty", "unit_cost",
                                "reorder_point", "article_code", "sku"],
    },
    "workflows": [
        {
            "name": "Low Stock Alert",
            "description": "SKUs below safety stock — reorder candidates",
            "action": "alert",
            "expects": {
                "table": {"aliases": ["balance_stock", "inventory", "stock"]},
                "cols": {
                    "sku": ["article_code", "sku", "product_code"],
                    "site": ["site_code", "store_code", "location"],
                    "qty": ["stock_qty", "on_hand", "qty"],
                },
            },
            "template_sql": "SELECT {site}, {sku}, {qty} "
                            "FROM {table} "
                            "WHERE {qty} < 20 "
                            "ORDER BY {qty} ASC LIMIT 100",
        },
        {
            "name": "Inventory Value Summary",
            "description": "Total stock value per site",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["balance_stock", "inventory", "stock"]},
                "cols": {
                    "site": ["site_code", "store_code", "location"],
                    "qty": ["stock_qty", "on_hand", "qty"],
                    "cost": ["weighted_cost_price", "unit_cost", "cost", "avg_cost"],
                },
            },
            "template_sql": "SELECT {site}, "
                            "       SUM({qty}) AS total_qty, "
                            "       SUM({qty} * {cost}) AS total_value "
                            "FROM {table} "
                            "GROUP BY {site} ORDER BY total_value DESC LIMIT 50",
        },
        {
            "name": "Zero Stock Critical",
            "description": "SKUs at zero stock across all sites — out-of-stock alert",
            "action": "alert",
            "expects": {
                "table": {"aliases": ["balance_stock", "inventory", "stock"]},
                "cols": {
                    "sku": ["article_code", "sku", "product_code"],
                    "qty": ["stock_qty", "on_hand", "qty"],
                },
            },
            "template_sql": "SELECT {sku}, SUM({qty}) AS total_qty "
                            "FROM {table} "
                            "GROUP BY {sku} HAVING SUM({qty}) = 0 LIMIT 100",
        },
        {
            "name": "Top SKU Holdings",
            "description": "Highest-stock SKUs by total qty",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["balance_stock", "inventory", "stock"]},
                "cols": {
                    "sku": ["article_code", "sku", "product_code"],
                    "qty": ["stock_qty", "on_hand", "qty"],
                },
            },
            "template_sql": "SELECT {sku}, SUM({qty}) AS total_qty "
                            "FROM {table} "
                            "GROUP BY {sku} ORDER BY total_qty DESC LIMIT 50",
        },
    ],
}


# ───────────────────────── MDL FORMAT (Phase 3) ───────────────────────────
MDL_PACK = {
    "name": "pharmacy_retail_mdl",
    "vertical": "Pharmacy / retail inventory",
    "description": "MDL-format inventory pack. Logical `inventory` model + "
                   "optional `articles` join. Workflows portable across "
                   "balance_stock / stock_master / inventory_daily schemas.",
    "detect": {
        "required_tables_any": ["inventory", "stock", "balance_stock",
                                "sku", "products", "items", "article"],
        "required_cols_any":   ["stock_qty", "on_hand", "qty", "unit_cost",
                                "reorder_point", "article_code", "sku"],
    },
    "models": [
        {
            "name": "inventory",
            "raw_table_aliases": [
                "balance_stock", "inventory", "stock", "stock_master",
                "balance_stock_smoke", "inventory_daily", "stock_levels",
            ],
            "virtual_columns": [
                {"name": "sku",          "aliases": ["article_code", "sku",
                                                      "product_code", "item_code"],
                 "type": "string"},
                {"name": "site",         "aliases": ["site_code", "store_code",
                                                      "location", "branch_code"],
                 "type": "string"},
                {"name": "qty",          "aliases": ["stock_qty", "on_hand",
                                                      "qty", "quantity", "balance"],
                 "type": "numeric"},
                {"name": "unit_cost",    "aliases": ["weighted_cost_price",
                                                      "unit_cost", "cost",
                                                      "avg_cost", "wac"],
                 "type": "numeric"},
                {"name": "reorder_point","aliases": ["reorder_point", "reorder_qty",
                                                      "min_stock", "safety_stock"],
                 "type": "numeric"},
                # Derived flags
                {"name": "is_zero_stock",   "expression": "qty = 0",
                 "type": "boolean"},
                {"name": "is_low_stock",    "expression": "qty < 20",
                 "type": "boolean"},
                {"name": "extended_value",  "expression": "qty * unit_cost",
                 "type": "numeric"},
            ],
            "relationships": [
                {"model": "articles", "on": "sku = articles.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
    ],
    "workflows": [
        {
            "name": "Low Stock Alert",
            "description": "SKUs below safety threshold — reorder candidates",
            "action": "alert",
            "model": "inventory",
            "sql": "SELECT site, sku, qty FROM inventory "
                   "WHERE is_low_stock ORDER BY qty ASC LIMIT 100",
        },
        {
            "name": "Inventory Value Summary",
            "description": "Total stock value per site",
            "action": "post_insight",
            "model": "inventory",
            "sql": "SELECT site, SUM(qty) AS total_qty, "
                   "       SUM(extended_value) AS total_value "
                   "FROM inventory GROUP BY site "
                   "ORDER BY total_value DESC LIMIT 50",
        },
        {
            "name": "Zero Stock Critical",
            "description": "SKUs at zero stock across all sites",
            "action": "alert",
            "model": "inventory",
            "sql": "SELECT sku, SUM(qty) AS total_qty FROM inventory "
                   "GROUP BY sku HAVING SUM(qty) = 0 LIMIT 100",
        },
        {
            "name": "Top SKU Holdings",
            "description": "Highest-stock SKUs by total qty",
            "action": "post_insight",
            "model": "inventory",
            "sql": "SELECT sku, SUM(qty) AS total_qty FROM inventory "
                   "GROUP BY sku ORDER BY total_qty DESC LIMIT 50",
        },
        {
            "name": "High-Value Slow Movers",
            "description": "Top extended-value SKUs (capital tied up)",
            "action": "post_insight",
            "model": "inventory",
            "sql": "SELECT sku, SUM(extended_value) AS capital_tied "
                   "FROM inventory GROUP BY sku "
                   "ORDER BY capital_tied DESC LIMIT 50",
        },
    ],
}
