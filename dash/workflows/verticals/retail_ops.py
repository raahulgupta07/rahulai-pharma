"""Retail Operations vertical pack.

Detects: project schemas w/ POS transactions, SKU master, store master,
inventory snapshots, or shrink events. Targets daily sales recap, basket
analysis, shrink audit, stockout alerting, GMROI review.

Two formats:
  PACK     — legacy {placeholder} template_sql, install-time bind.
  MDL_PACK — WrenAI-style logical model w/ virtual cols + relationships.
"""
PACK = {
    "name": "retail_ops",
    "vertical": "Retail Operations",
    "description": "POS sales recap + SKU velocity + shrink audit + basket analysis",
    "detect": {
        "required_tables_any": ["pos_transactions", "pos", "transactions",
                                "sku_master", "sku", "products", "items",
                                "store_master", "stores", "inventory_snapshot",
                                "inventory", "shrink_events", "shrink"],
        "required_cols_any":   ["sku", "store_id", "qty", "quantity", "unit_price",
                                "basket_id", "transaction_id", "shrink_qty",
                                "on_hand", "stock_qty"],
    },
    "workflows": [
        {
            "name": "Daily Sales Recap",
            "description": "Yesterday's sales by store",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["pos_transactions", "pos", "transactions"]},
                "cols": {
                    "store": ["store_id", "store_code", "site_id"],
                    "qty": ["qty", "quantity", "units"],
                    "price": ["unit_price", "price", "amount"],
                },
            },
            "template_sql": "SELECT {store}, COUNT(*) AS lines, "
                            "       SUM({qty}) AS units, "
                            "       SUM({qty} * {price}) AS revenue "
                            "FROM {table} "
                            "GROUP BY {store} ORDER BY revenue DESC LIMIT 100",
        },
        {
            "name": "Top SKU Velocity",
            "description": "Fastest-moving SKUs by units sold",
            "action": "post_insight",
            "expects": {
                "table": {"aliases": ["pos_transactions", "pos", "transactions"]},
                "cols": {
                    "sku": ["sku", "product_code", "item_code"],
                    "qty": ["qty", "quantity", "units"],
                },
            },
            "template_sql": "SELECT {sku}, SUM({qty}) AS units_sold "
                            "FROM {table} "
                            "GROUP BY {sku} ORDER BY units_sold DESC LIMIT 50",
        },
        {
            "name": "Shrink Audit",
            "description": "Stores ranked by shrink quantity",
            "action": "alert",
            "expects": {
                "table": {"aliases": ["shrink_events", "shrink"]},
                "cols": {
                    "store": ["store_id", "store_code", "site_id"],
                    "qty": ["shrink_qty", "qty", "quantity"],
                },
            },
            "template_sql": "SELECT {store}, SUM({qty}) AS shrink_units "
                            "FROM {table} "
                            "GROUP BY {store} ORDER BY shrink_units DESC LIMIT 50",
        },
    ],
}


# ───────────────────────── MDL FORMAT (Phase 3) ───────────────────────────
MDL_PACK = {
    "name": "retail_ops_mdl",
    "vertical": "Retail Operations",
    "description": "MDL-format retail pack. Logical `pos`, `sku`, `store`, "
                   "`inventory`, `shrink` models. Workflows portable across "
                   "retail vendor schemas.",
    "detect": {
        "required_tables_any": ["pos_transactions", "pos", "transactions",
                                "sku_master", "sku", "products", "items",
                                "store_master", "stores", "inventory_snapshot",
                                "inventory", "shrink_events", "shrink"],
        "required_cols_any":   ["sku", "store_id", "qty", "quantity", "unit_price",
                                "basket_id", "transaction_id", "shrink_qty",
                                "on_hand", "stock_qty"],
    },
    "models": [
        {
            "name": "pos",
            "raw_table_aliases": [
                "pos_transactions", "pos", "transactions", "sales_transactions",
                "tx", "sales", "pos_lines",
            ],
            "virtual_columns": [
                {"name": "transaction_id", "aliases": ["transaction_id", "txn_id",
                                                        "tx_id", "receipt_id",
                                                        "order_id"],
                 "type": "string"},
                {"name": "basket_id",      "aliases": ["basket_id", "ticket_id",
                                                        "cart_id", "order_id"],
                 "type": "string"},
                {"name": "sku",            "aliases": ["sku", "product_code",
                                                        "item_code", "article_code"],
                 "type": "string"},
                {"name": "store",          "aliases": ["store_id", "store_code",
                                                        "site_id", "location_id"],
                 "type": "string"},
                {"name": "qty",            "aliases": ["qty", "quantity", "units",
                                                        "units_sold"],
                 "type": "numeric"},
                {"name": "unit_price",     "aliases": ["unit_price", "price",
                                                        "sell_price", "amount"],
                 "type": "numeric"},
                {"name": "txn_date",       "aliases": ["transaction_date", "tx_date",
                                                        "sale_date", "date"],
                 "type": "date"},
                # Derived
                {"name": "line_revenue",   "expression": "qty * unit_price",
                 "type": "numeric"},
            ],
            "relationships": [
                {"model": "sku", "on": "sku = sku.code",
                 "type": "many_to_one", "optional": True},
                {"model": "store", "on": "store = store.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "sku",
            "raw_table_aliases": [
                "sku_master", "sku", "products", "items", "product_master",
                "article_master",
            ],
            "virtual_columns": [
                {"name": "code",        "aliases": ["sku", "product_code",
                                                     "item_code", "code"],
                 "type": "string"},
                {"name": "name",        "aliases": ["product_name", "name",
                                                     "description", "item_name"],
                 "type": "string"},
                {"name": "category",    "aliases": ["category", "dept", "department",
                                                     "product_category"],
                 "type": "string"},
                {"name": "cost",        "aliases": ["unit_cost", "cost", "wac"],
                 "type": "numeric"},
            ],
            "relationships": [],
        },
        {
            "name": "store",
            "raw_table_aliases": [
                "store_master", "stores", "locations", "site_master",
            ],
            "virtual_columns": [
                {"name": "code",        "aliases": ["store_id", "store_code",
                                                     "site_id", "code"],
                 "type": "string"},
                {"name": "name",        "aliases": ["store_name", "name",
                                                     "location_name"],
                 "type": "string"},
                {"name": "region",      "aliases": ["region", "area", "district"],
                 "type": "string"},
            ],
            "relationships": [],
        },
        {
            "name": "inventory",
            "raw_table_aliases": [
                "inventory_snapshot", "inventory", "stock", "stock_levels",
                "balance_stock",
            ],
            "virtual_columns": [
                {"name": "sku",         "aliases": ["sku", "product_code",
                                                     "item_code", "article_code"],
                 "type": "string"},
                {"name": "store",       "aliases": ["store_id", "store_code",
                                                     "site_id", "location_id"],
                 "type": "string"},
                {"name": "on_hand",     "aliases": ["on_hand", "stock_qty",
                                                     "qty", "quantity", "balance"],
                 "type": "numeric"},
                {"name": "unit_cost",   "aliases": ["unit_cost", "cost",
                                                     "weighted_cost_price"],
                 "type": "numeric"},
                # Derived
                {"name": "stockout_flag", "expression": "on_hand <= 0",
                 "type": "boolean"},
                {"name": "inventory_value", "expression": "on_hand * unit_cost",
                 "type": "numeric"},
            ],
            "relationships": [
                {"model": "sku", "on": "sku = sku.code",
                 "type": "many_to_one", "optional": True},
                {"model": "store", "on": "store = store.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
        {
            "name": "shrink",
            "raw_table_aliases": [
                "shrink_events", "shrink", "shrinkage", "inventory_adjustments",
                "loss_events",
            ],
            "virtual_columns": [
                {"name": "sku",         "aliases": ["sku", "product_code",
                                                     "item_code"],
                 "type": "string"},
                {"name": "store",       "aliases": ["store_id", "store_code",
                                                     "site_id"],
                 "type": "string"},
                {"name": "shrink_qty",  "aliases": ["shrink_qty", "qty", "quantity",
                                                     "loss_qty"],
                 "type": "numeric"},
                {"name": "event_date",  "aliases": ["event_date", "shrink_date",
                                                     "date", "reported_at"],
                 "type": "date"},
            ],
            "relationships": [
                {"model": "store", "on": "store = store.code",
                 "type": "many_to_one", "optional": True},
            ],
        },
    ],
    "workflows": [
        {
            "name": "Daily Sales Recap",
            "description": "Sales per store with units, baskets, revenue",
            "action": "post_insight",
            "model": "pos",
            "sql": "SELECT store, "
                   "       COUNT(DISTINCT basket_id) AS baskets, "
                   "       SUM(qty) AS units, "
                   "       SUM(line_revenue) AS revenue, "
                   "       CASE WHEN COUNT(DISTINCT basket_id) > 0 "
                   "            THEN ROUND(SUM(line_revenue)::numeric "
                   "                       / COUNT(DISTINCT basket_id), 2) "
                   "            ELSE 0 END AS avg_ticket "
                   "FROM pos GROUP BY store "
                   "ORDER BY revenue DESC LIMIT 100",
        },
        {
            "name": "SKU Velocity",
            "description": "Top-selling SKUs by units and revenue",
            "action": "post_insight",
            "model": "pos",
            "sql": "SELECT sku, SUM(qty) AS units_sold, "
                   "       SUM(line_revenue) AS revenue "
                   "FROM pos GROUP BY sku "
                   "ORDER BY units_sold DESC LIMIT 50",
        },
        {
            "name": "Basket Analysis",
            "description": "Avg basket size + units-per-basket by store",
            "action": "post_insight",
            "model": "pos",
            "sql": "SELECT store, "
                   "       COUNT(DISTINCT basket_id) AS baskets, "
                   "       ROUND(SUM(qty)::numeric "
                   "             / NULLIF(COUNT(DISTINCT basket_id), 0), 2) AS units_per_basket, "
                   "       ROUND(SUM(line_revenue)::numeric "
                   "             / NULLIF(COUNT(DISTINCT basket_id), 0), 2) AS avg_basket_value "
                   "FROM pos GROUP BY store "
                   "ORDER BY baskets DESC LIMIT 50",
        },
        {
            "name": "Shrink Audit",
            "description": "Stores w/ highest shrink quantity and dollar loss",
            "action": "alert",
            "model": "shrink",
            "sql": "SELECT s.store, SUM(s.shrink_qty) AS shrink_units, "
                   "       COUNT(*) AS shrink_events "
                   "FROM shrink s GROUP BY s.store "
                   "ORDER BY shrink_units DESC LIMIT 50",
        },
        {
            "name": "Stockout Alert",
            "description": "SKUs at zero stock across stores",
            "action": "alert",
            "model": "inventory",
            "sql": "SELECT sku, store, on_hand "
                   "FROM inventory WHERE stockout_flag "
                   "ORDER BY sku, store LIMIT 100",
        },
        {
            "name": "GMROI Review",
            "description": "Inventory value tied up per store (capital exposure)",
            "action": "post_insight",
            "model": "inventory",
            "sql": "SELECT store, "
                   "       SUM(on_hand) AS total_units, "
                   "       SUM(inventory_value) AS capital_tied "
                   "FROM inventory GROUP BY store "
                   "ORDER BY capital_tied DESC LIMIT 50",
        },
    ],
}
