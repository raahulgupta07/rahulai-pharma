TEMPLATE = {
    "label": "Hotel",
    "description": "Multi-property hotel group — share occupancy bands, mask rates and guest IDs",
    "scope_keyword": "property",
    "icon": "🏨",
    "policy": {
        "version": 1,
        "private": {"fields": {}},
        "network": {"fields": {
            "occupancy": {"mode": "band", "bands": [
                {"name": "low", "max": 40},
                {"name": "mid", "max": 75},
                {"name": "high", "max": 100},
            ]},
            "adr": {"mode": "mask", "mask_with": "$**"},
            "rate": {"mode": "mask", "mask_with": "$**"},
            "revpar": {"mode": "mask", "mask_with": "$**"},
            "booking_id": {"mode": "mask", "mask_with": "BKG-***"},
            "guest_id": {"mode": "hide"},
        }},
        "public": {"fields": {
            "occupancy": {"mode": "band", "bands": [
                {"name": "low", "max": 40},
                {"name": "mid", "max": 75},
                {"name": "high", "max": 100},
            ]},
            "adr": {"mode": "hide"},
            "rate": {"mode": "hide"},
            "revpar": {"mode": "hide"},
            "booking_id": {"mode": "hide"},
            "guest_id": {"mode": "hide"},
        }},
    },
    "suggested_roles": [
        {"role_name": "property_staff", "allowed_intents": ["private"], "description": "property own data only"},
        {"role_name": "property_manager", "allowed_intents": ["private", "network"], "description": "own + peer-network occupancy"},
        {"role_name": "regional_admin", "allowed_intents": ["private", "network", "public"], "description": "all access"},
    ],
}
