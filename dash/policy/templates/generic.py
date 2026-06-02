TEMPLATE = {
    "label": "Generic",
    "description": "Minimal default — band on counts, hide on customer/cost",
    "scope_keyword": "unit",
    "icon": "📦",
    "policy": {
        "version": 1,
        "private": {"fields": {}},
        "network": {"fields": {
            "qty": {"mode": "band", "bands": [
                {"name": "out", "max": 0},
                {"name": "low", "max": 10},
                {"name": "ok", "max": 99999},
            ]},
            "count": {"mode": "band", "bands": [
                {"name": "low", "max": 10},
                {"name": "ok", "max": 99999},
            ]},
            "customer_id": {"mode": "hide"},
            "cost": {"mode": "hide"},
        }},
        "public": {"fields": {
            "qty": {"mode": "band", "bands": [
                {"name": "out", "max": 0},
                {"name": "available", "max": 99999},
            ]},
            "count": {"mode": "hide"},
            "customer_id": {"mode": "hide"},
            "cost": {"mode": "hide"},
        }},
    },
    "suggested_roles": [
        {"role_name": "unit_staff", "allowed_intents": ["private"], "description": "unit own data only"},
        {"role_name": "unit_manager", "allowed_intents": ["private", "network"], "description": "own + peer-network"},
        {"role_name": "regional_admin", "allowed_intents": ["private", "network", "public"], "description": "all access"},
    ],
}
