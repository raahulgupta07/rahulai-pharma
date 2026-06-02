"""Pharma vertical workflows — 8 multi-step analyst playbooks."""
from __future__ import annotations

WORKFLOWS: list[dict] = [
    {
        "name": "Stockout Investigation",
        "description": "Detect SKUs at zero stock, classify by velocity, surface root cause and supplier",
        "steps": [
            {"type": "query", "prompt": "List all SKUs with current qty = 0 across every store. Show SKU, generic name, store, last movement date."},
            {"type": "analysis", "prompt": "For each stockout SKU, compute 30-day historical sales velocity to classify as A/B/C-class."},
            {"type": "diagnostic", "prompt": "For A-class stockouts, check supplier lead time, last PO date, and whether the SKU has an open replenishment order."},
            {"type": "summary", "prompt": "Draft a stockout brief: top 10 critical stockouts, root cause hypothesis, recommended action per SKU."},
        ],
    },
    {
        "name": "Expiry Monitoring",
        "description": "Surface batches expiring in 30/60/90-day windows for pull, markdown, or return-to-vendor",
        "steps": [
            {"type": "query", "prompt": "List all inventory batches with expiry_date within 30 days. Show batch_no, SKU, store, qty, retail value at risk."},
            {"type": "query", "prompt": "List all batches expiring 31-60 and 61-90 days out, ranked by retail value at risk."},
            {"type": "analysis", "prompt": "Calculate total dollar exposure across the three expiry windows; flag any A-class SKUs."},
            {"type": "summary", "prompt": "Generate FEFO action list: batches to pull immediately, batches to mark down, batches to return to vendor."},
        ],
    },
    {
        "name": "Slow Mover Analysis",
        "description": "Identify SKUs with no movement >60 days, recommend markdown / transfer / return",
        "steps": [
            {"type": "query", "prompt": "Find every SKU with zero sales in the last 60 days. Show SKU, store, on-hand qty, last sale date, inventory cost."},
            {"type": "analysis", "prompt": "Group slow movers by therapeutic class and supplier; total tied-up capital."},
            {"type": "prescriptive", "prompt": "Recommend per-SKU action: markdown %, inter-store transfer target, or return-to-vendor based on shelf life remaining."},
        ],
    },
    {
        "name": "High-Demand Alert",
        "description": "Top 10 SKUs by velocity, ensure stock cover and reorder triggers are healthy",
        "steps": [
            {"type": "query", "prompt": "Top 10 SKUs by units sold in the last 30 days, network-wide."},
            {"type": "analysis", "prompt": "For each top SKU, compute current days-on-hand per store; flag stores with DOH < 14."},
            {"type": "prescriptive", "prompt": "Recommend immediate transfer or expedited reorder for any top-10 SKU at risk of stockout in the next 14 days."},
        ],
    },
    {
        "name": "Cross-Store Availability",
        "description": "Pivot SKU × store availability to identify gaps and transfer opportunities",
        "steps": [
            {"type": "query", "prompt": "Build a pivot of every active SKU across every store showing on-hand qty. Highlight cells with qty = 0."},
            {"type": "analysis", "prompt": "For each store with stockouts, find sibling stores in the same city/region holding surplus (DOH > 60)."},
            {"type": "prescriptive", "prompt": "Output recommended inter-store transfer list: from store, to store, SKU, qty, reason."},
        ],
    },
    {
        "name": "Margin Analysis",
        "description": "Top and bottom margin SKUs with supplier and category breakdown",
        "steps": [
            {"type": "query", "prompt": "Top 20 SKUs by gross margin % over the last 90 days. Show SKU, supplier, units sold, GM%."},
            {"type": "query", "prompt": "Bottom 20 SKUs by gross margin %. Flag any with GM% below network average."},
            {"type": "analysis", "prompt": "Roll up margin by supplier and therapeutic category; identify the lowest-margin supplier overall."},
            {"type": "summary", "prompt": "Margin brief: where the network earns most, where it leaks, and supplier renegotiation candidates."},
        ],
    },
    {
        "name": "Schedule Reorder",
        "description": "Recommend reorder quantity per SKU based on velocity, lead time, and safety stock",
        "steps": [
            {"type": "query", "prompt": "List every SKU below its reorder point. Include current qty, 30-day velocity, supplier lead time."},
            {"type": "analysis", "prompt": "Compute recommended order qty per SKU using formula: (velocity × (lead_time + safety_days)) − current_qty."},
            {"type": "prescriptive", "prompt": "Generate purchase order draft grouped by supplier, sorted by total order value."},
        ],
    },
    {
        "name": "Compliance Audit",
        "description": "Expired stock, controlled substances, and prescription requirement audit",
        "steps": [
            {"type": "query", "prompt": "List any inventory rows with expiry_date in the past — these must NOT be on shelf."},
            {"type": "query", "prompt": "List all controlled substances (Schedule II/III) with their qty and last audit date."},
            {"type": "analysis", "prompt": "Cross-check any SKUs flagged Rx-only against recent dispensing logs to ensure prescriptions are recorded."},
            {"type": "summary", "prompt": "Compliance audit report: expired-stock count, controlled-substance reconciliation status, Rx-record gaps."},
        ],
    },
]
