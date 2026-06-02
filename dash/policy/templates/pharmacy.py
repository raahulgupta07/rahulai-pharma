TEMPLATE = {
    "label": "Pharmacy",
    "description": "Multi-store pharmacy network — protect sensitive Rx + commercial fields",
    "scope_keyword": "store",
    "icon": "💊",
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
            "supplier_id": {"mode": "hide"},
            "patient_id": {"mode": "hide"},
            "rx_number": {"mode": "mask", "mask_with": "RX-***"},
            "price": {"mode": "mask", "mask_with": "$**"},
            "expiry_date": {"mode": "mask", "mask_with": "***"},
        }},
        "public": {"fields": {
            "qty": {"mode": "band", "bands": [
                {"name": "out", "max": 0},
                {"name": "available", "max": 99999},
            ]},
            "cost": {"mode": "hide"},
            "margin": {"mode": "hide"},
            "supplier_id": {"mode": "hide"},
            "patient_id": {"mode": "hide"},
            "rx_number": {"mode": "hide"},
            "price": {"mode": "hide"},
            "expiry_date": {"mode": "hide"},
        }},
    },
    "suggested_roles": [
        {"role_name": "store_staff", "allowed_intents": ["private"], "description": "store own data only"},
        {"role_name": "store_manager", "allowed_intents": ["private", "network"], "description": "own + peer-network availability"},
        {"role_name": "regional_admin", "allowed_intents": ["private", "network", "public"], "description": "all access"},
    ],
}
