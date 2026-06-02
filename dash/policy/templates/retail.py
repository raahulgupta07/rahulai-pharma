TEMPLATE = {
    "label": "Retail",
    "description": "Multi-store retail chain — protect customer + commercial fields, share availability",
    "scope_keyword": "store",
    "icon": "🛒",
    "policy": {
        "version": 1,
        "private": {"fields": {}},
        "network": {"fields": {
            "qty": {"mode": "band", "bands": [
                {"name": "out", "max": 0},
                {"name": "low", "max": 10},
                {"name": "ok", "max": 99999},
            ]},
            "cost": {"mode": "hide"},
            "margin": {"mode": "hide"},
            "customer_id": {"mode": "hide"},
            "transaction_id": {"mode": "mask", "mask_with": "TXN-***"},
            "discount_pct": {"mode": "mask", "mask_with": "**%"},
        }},
        "public": {"fields": {
            "qty": {"mode": "band", "bands": [
                {"name": "out", "max": 0},
                {"name": "available", "max": 99999},
            ]},
            "cost": {"mode": "hide"},
            "margin": {"mode": "hide"},
            "customer_id": {"mode": "hide"},
            "transaction_id": {"mode": "hide"},
            "discount_pct": {"mode": "hide"},
        }},
    },
    "suggested_roles": [
        {"role_name": "store_staff", "allowed_intents": ["private"], "description": "store own data only"},
        {"role_name": "store_manager", "allowed_intents": ["private", "network"], "description": "own + peer-network availability"},
        {"role_name": "regional_admin", "allowed_intents": ["private", "network", "public"], "description": "all access"},
    ],
}
