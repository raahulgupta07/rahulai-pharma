"""Pharma supply-chain pack."""
import pandas as pd

PACK = {
    "domain": "pharma",
    "expert_prompt": (
        "Senior pharma supply-chain analyst with 15 years experience. "
        "Domain knowledge: stockouts, days-on-hand, ABC/XYZ analysis, EOQ, lead-time variance, "
        "expiry waste (FIFO/FEFO compliance), MSL alerts, formulary tiering, "
        "cold chain, batch/lot tracking, NDC codes, GDP compliance. "
        "Always check: stockout days, expiry within 90/180d, slow-mover write-down risk, "
        "demand-supply variance, supplier reliability, batch concentration risk."
    ),
    "must_run_sqls": [
        {
            "sql": "SELECT COUNT(*) AS total_skus FROM {table}",
            "needs_table_with_cols": ["sku"],
            "headline_template": "Total SKUs in inventory: {value}",
            "tags": ["inventory","sku"],
            "severity": "low",
        },
        {
            "sql": "SELECT COUNT(*) AS low_stock FROM {table} WHERE qty < 10",
            "needs_table_with_cols": ["qty"],
            "headline_template": "{value} SKUs at low stock (qty<10)",
            "tags": ["stockout","alert"],
            "severity": "high",
        },
    ],
    "detectors": [],  # add domain detectors as needed
}
