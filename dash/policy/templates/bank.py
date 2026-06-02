TEMPLATE = {
    "label": "Bank",
    "description": "Multi-branch bank — heavy hide+mask on all financial and PII fields",
    "scope_keyword": "branch",
    "icon": "🏦",
    "policy": {
        "version": 1,
        "private": {"fields": {}},
        "network": {"fields": {
            "balance": {"mode": "hide"},
            "transaction_amount": {"mode": "mask", "mask_with": "$**"},
            "customer_id": {"mode": "hide"},
            "account_number": {"mode": "mask", "mask_with": "ACCT-****"},
            "credit_score": {"mode": "band", "bands": [
                {"name": "poor", "max": 579},
                {"name": "fair", "max": 669},
                {"name": "good", "max": 739},
                {"name": "excellent", "max": 850},
            ]},
            "loan_amount": {"mode": "mask", "mask_with": "$**"},
        }},
        "public": {"fields": {
            "balance": {"mode": "hide"},
            "transaction_amount": {"mode": "hide"},
            "customer_id": {"mode": "hide"},
            "account_number": {"mode": "hide"},
            "credit_score": {"mode": "hide"},
            "loan_amount": {"mode": "hide"},
        }},
    },
    "suggested_roles": [
        {"role_name": "teller", "allowed_intents": ["private"], "description": "branch own data only"},
        {"role_name": "branch_manager", "allowed_intents": ["private", "network"], "description": "own + peer-network bands"},
        {"role_name": "regional_admin", "allowed_intents": ["private", "network", "public"], "description": "all access"},
    ],
}
