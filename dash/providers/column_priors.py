"""Static priors for column classification — regex patterns, name vocabulary,
and Brain seed of canonical column roles.

Pure-data module: stdlib only. Consumed by
:mod:`dash.providers.column_classifier`. Adding entries here improves
coverage of the multi-signal classifier without code changes.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# 1. Regex patterns: (pattern, semantic_tag, is_pii, confidence)
# ---------------------------------------------------------------------------

REGEX_PATTERNS: list[tuple[str, str, bool, float]] = [
    # Direct PII
    (r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$",                                   "email",            True,  0.99),
    (r"^\+?\d{10,15}$",                                                  "phone",            True,  0.95),
    (r"^\(\d{3}\)\s?\d{3}-\d{4}$",                                       "phone_us",         True,  0.95),
    (r"^\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}$",   "phone_intl",       True,  0.85),
    (r"^\d{3}-?\d{2}-?\d{4}$",                                           "ssn_us",           True,  0.95),
    (r"^[A-Z]{2}\d{6,9}$",                                               "passport",         True,  0.85),
    (r"^[A-Z]{2}\d{2}[A-Z0-9]{4,30}$",                                   "iban",             True,  0.80),
    (r"^4\d{12}(\d{3})?$",                                               "credit_card_visa", True,  0.95),
    (r"^5[1-5]\d{14}$",                                                  "credit_card_mc",   True,  0.95),
    (r"^3[47]\d{13}$",                                                   "credit_card_amex", True,  0.95),
    (r"^6(?:011|5\d{2})\d{12}$",                                         "credit_card_disc", True,  0.95),
    (r"^\d{3}-?\d{2}-?\d{4}$",                                           "tax_id_us",        True,  0.80),
    (r"^[A-Z]{1,2}\d{6,8}[A-Z]?$",                                       "drivers_license",  True,  0.70),

    # Identifiers / hashes
    (r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",  "uuid",             False, 0.99),
    (r"^[a-f0-9]{32}$",                                                  "md5",              False, 0.95),
    (r"^[a-f0-9]{40}$",                                                  "sha1",             False, 0.95),
    (r"^[a-f0-9]{64}$",                                                  "sha256",           False, 0.95),
    (r"^[A-Za-z0-9_-]{20,}$",                                            "opaque_token",     False, 0.50),

    # URLs / network
    (r"^https?://",                                                      "url",              False, 0.95),
    (r"^ftp://",                                                         "url_ftp",          False, 0.95),
    (r"^[a-zA-Z0-9.-]+\.(com|org|net|io|co|ai|edu|gov)(/.*)?$",         "domain",           False, 0.80),
    (r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",                            "ip_v4",            True,  0.95),
    (r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$",                        "ip_v6",            True,  0.90),
    (r"^([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$",                         "mac_address",      True,  0.95),

    # Dates and times
    (r"^\d{4}-\d{2}-\d{2}$",                                             "iso_date",         False, 0.95),
    (r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}",                                  "iso_datetime",     False, 0.95),
    (r"^\d{2}/\d{2}/\d{4}$",                                             "us_date",          False, 0.85),
    (r"^\d{2}-\d{2}-\d{4}$",                                             "eu_date",          False, 0.80),
    (r"^\d{2}:\d{2}(:\d{2})?$",                                          "time_of_day",      False, 0.85),
    (r"^\d{10}$",                                                        "epoch_seconds",    False, 0.55),
    (r"^\d{13}$",                                                        "epoch_millis",     False, 0.55),

    # Geo / postal
    (r"^\d{5}(-\d{4})?$",                                                "zip_us",           False, 0.90),
    (r"^[A-Z]\d[A-Z] ?\d[A-Z]\d$",                                       "postal_ca",        False, 0.90),
    (r"^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$",                              "postal_uk",        False, 0.88),
    (r"^\d{4,5}$",                                                       "postal_generic",   False, 0.40),
    (r"^-?\d+\.\d+,-?\d+\.\d+$",                                         "lat_lng",          False, 0.85),
    (r"^-?(([0-8]?[0-9])|90)(\.[0-9]+)?$",                               "latitude",         False, 0.55),
    (r"^-?((1[0-7][0-9])|([0-9]?[0-9])|180)(\.[0-9]+)?$",                "longitude",        False, 0.55),

    # Codes
    (r"^[A-Z]{3}$",                                                      "iso_code3",        False, 0.70),
    (r"^[A-Z]{2}$",                                                      "iso_code2",        False, 0.55),
    (r"^[A-Z]{2}-[A-Z0-9]{1,3}$",                                        "iso_subdivision",  False, 0.75),
    (r"^[A-Z0-9]{12}$",                                                  "isin",             False, 0.70),

    # Money / numerics
    (r"^\$[\d,]+(\.\d{2})?$",                                            "currency_usd_str", False, 0.90),
    (r"^[\d,]+\.\d{2}\s?(USD|EUR|GBP|JPY)$",                             "currency_str",     False, 0.85),
    (r"^-?\d+$",                                                         "integer_str",      False, 0.30),
    (r"^-?\d+\.\d+$",                                                    "decimal_str",      False, 0.30),
    (r"^\d+%$",                                                          "percentage_str",   False, 0.85),

    # Booleans / flags
    (r"^(true|false|TRUE|FALSE|True|False|t|f|T|F|0|1|yes|no|Y|N)$",     "boolean_str",      False, 0.85),

    # Vehicle / license
    (r"^[A-HJ-NPR-Z0-9]{17}$",                                           "vin",              False, 0.85),
    (r"^[A-Z0-9]{1,8}$",                                                 "license_plate",    False, 0.30),

    # Files / paths
    (r"^([a-zA-Z]:)?[\\/](.+[\\/])*[^\\/]+\.[a-zA-Z0-9]+$",              "file_path",        False, 0.70),
    (r"^[^\s]+\.(csv|xlsx|json|pdf|docx|pptx|txt|md|sql|png|jpg)$",      "filename",         False, 0.85),

    # Banking / finance identifiers
    (r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$",                             "swift_bic",        False, 0.90),
    (r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$",                                  "iban_strict",      True,  0.92),
    (r"^[A-Z]{3}$",                                                      "iso_currency_code",False, 0.65),

    # Cloud / API tokens
    (r"^AKIA[0-9A-Z]{16}$",                                              "aws_access_key",   True,  0.99),
    (r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$",             "jwt_token",        True,  0.95),
    (r"^ghp_[A-Za-z0-9]{36}$",                                           "github_token",     True,  0.95),
    (r"^sk-[A-Za-z0-9]{32,}$",                                           "openai_api_key",   True,  0.95),

    # Chat / messaging IDs
    (r"^[CG][A-Z0-9]{8,}$",                                              "slack_channel_id", False, 0.85),
    (r"^U[A-Z0-9]{8,}$",                                                 "slack_user_id",    False, 0.80),
    (r"^\d{17,20}$",                                                     "discord_snowflake",False, 0.70),

    # Crypto addresses
    (r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}$",                            "bitcoin_address",  False, 0.90),
    (r"^0x[a-fA-F0-9]{40}$",                                             "ethereum_address", False, 0.95),

    # Phone / time
    (r"^\+[1-9]\d{1,14}$",                                               "phone_e164",       True,  0.95),
    (r"^\d{2}:\d{2}(:\d{2})?$",                                          "time_hms",         False, 0.90),

    # Vehicle (stricter VIN already exists; add canonical alias)
    # Healthcare codes
    (r"^[A-TV-Z][0-9][A-Z0-9](\.[A-Z0-9]{1,4})?$",                       "icd10_code",       False, 0.85),
    (r"^\d{10}$",                                                        "npi_number",       False, 0.55),
    (r"^\d{5}$",                                                         "cpt_code",         False, 0.40),
]


# ---------------------------------------------------------------------------
# 2. Name vocabulary: substring/suffix/exact match → (role, semantic, conf)
# ---------------------------------------------------------------------------

NAME_VOCABULARY: dict[str, tuple[str, str, float]] = {
    # Identity / keys
    "id":              ("id",        "row_id",                  0.90),
    "_id":             ("id",        "foreign_key_or_pk",       0.85),
    "uuid":            ("id",        "uuid",                    0.95),
    "guid":            ("id",        "uuid",                    0.95),
    "key":             ("id",        "key",                     0.80),
    "pk":              ("id",        "primary_key",             0.90),
    "fk":              ("id",        "foreign_key",             0.90),
    "ref":             ("id",        "reference",               0.65),
    "hash":            ("id",        "hash_digest",             0.80),
    "token":           ("id",        "opaque_token",            0.75),

    # Codes / dimensions
    "code":            ("dimension", "code",                    0.70),
    "category":        ("dimension", "category",                0.85),
    "segment":         ("dimension", "segment",                 0.85),
    "tier":            ("dimension", "tier",                    0.80),
    "level":           ("dimension", "level",                   0.70),
    "group":           ("dimension", "group",                   0.70),
    "class":           ("dimension", "class",                   0.70),
    "label":           ("dimension", "label",                   0.70),
    "tag":             ("dimension", "tag",                     0.70),
    "status":          ("dimension", "status",                  0.90),
    "state":           ("dimension", "state_or_geo",            0.65),
    "stage":           ("dimension", "stage",                   0.80),
    "phase":           ("dimension", "phase",                   0.80),
    "flag":            ("dimension", "boolean_flag",            0.85),
    "is_":             ("dimension", "boolean_flag",            0.85),
    "has_":            ("dimension", "boolean_flag",            0.85),
    "type":            ("dimension", "type_code",               0.80),
    "kind":            ("dimension", "type_code",               0.75),
    "channel":         ("dimension", "channel",                 0.85),
    "source":          ("dimension", "source",                  0.75),
    "origin":          ("dimension", "origin",                  0.75),
    "department":      ("dimension", "department",              0.90),
    "team":            ("dimension", "team",                    0.80),
    "division":        ("dimension", "division",                0.85),
    "branch":          ("dimension", "branch",                  0.80),
    "industry":        ("dimension", "industry",                0.85),
    "sector":          ("dimension", "sector",                  0.85),
    "vertical":        ("dimension", "vertical",                0.80),
    "language":        ("dimension", "language",                0.90),
    "locale":          ("dimension", "locale",                  0.90),
    "currency":        ("dimension", "iso_currency",            0.95),
    "country":         ("dimension", "geo_country",             0.95),
    "region":          ("dimension", "geo_region",              0.95),
    "city":            ("dimension", "geo_city",                0.90),
    "province":        ("dimension", "geo_province",            0.90),
    "district":        ("dimension", "geo_district",            0.85),
    "continent":       ("dimension", "geo_continent",           0.95),

    # Measures
    "qty":             ("measure",   "quantity",                0.90),
    "quantity":        ("measure",   "quantity",                0.95),
    "units":           ("measure",   "units",                   0.85),
    "amount":          ("measure",   "currency_amount",         0.85),
    "amt":             ("measure",   "currency_amount",         0.85),
    "price":           ("measure",   "currency_amount",         0.90),
    "cost":            ("measure",   "currency_amount",         0.90),
    "fee":             ("measure",   "currency_amount",         0.85),
    "charge":          ("measure",   "currency_amount",         0.80),
    "revenue":         ("measure",   "currency_amount",         0.95),
    "sales":           ("measure",   "currency_amount",         0.90),
    "profit":          ("measure",   "currency_amount",         0.95),
    "margin":          ("measure",   "ratio_or_amount",         0.85),
    "discount":        ("measure",   "currency_or_pct",         0.80),
    "tax":             ("measure",   "currency_amount",         0.85),
    "balance":         ("measure",   "currency_amount",         0.90),
    "salary":          ("measure",   "currency_amount",         0.95),
    "wage":            ("measure",   "currency_amount",         0.90),
    "budget":          ("measure",   "currency_amount",         0.90),
    "spend":           ("measure",   "currency_amount",         0.85),
    "expense":         ("measure",   "currency_amount",         0.90),
    "income":          ("measure",   "currency_amount",         0.90),
    "total":           ("measure",   "aggregate",               0.75),
    "subtotal":        ("measure",   "aggregate",               0.85),
    "sum":             ("measure",   "aggregate_sum",           0.85),
    "avg":             ("measure",   "aggregate_avg",           0.85),
    "average":         ("measure",   "aggregate_avg",           0.85),
    "median":          ("measure",   "aggregate_median",        0.90),
    "count":           ("measure",   "count",                   0.85),
    "num":             ("measure",   "number",                  0.70),
    "number":          ("measure",   "number",                  0.65),
    "rate":            ("measure",   "rate",                    0.80),
    "ratio":           ("measure",   "ratio",                   0.85),
    "pct":             ("measure",   "percentage",              0.90),
    "percent":         ("measure",   "percentage",              0.90),
    "percentage":      ("measure",   "percentage",              0.95),
    "score":           ("measure",   "score",                   0.85),
    "weight":          ("measure",   "weight",                  0.85),
    "height":          ("measure",   "height",                  0.85),
    "length":          ("measure",   "length",                  0.80),
    "width":           ("measure",   "width",                   0.80),
    "duration":        ("measure",   "duration",                0.90),
    "elapsed":         ("measure",   "duration",                0.85),
    "latency":         ("measure",   "duration_ms",             0.90),
    "size":            ("measure",   "size",                    0.65),
    "volume":          ("measure",   "volume",                  0.85),
    "distance":        ("measure",   "distance",                0.85),

    # Temporal
    "_at":             ("temporal",  "timestamp",               0.95),
    "_date":           ("temporal",  "date",                    0.95),
    "_time":           ("temporal",  "time",                    0.85),
    "_ts":             ("temporal",  "timestamp",               0.90),
    "_on":             ("temporal",  "date",                    0.75),
    "date":            ("temporal",  "date",                    0.85),
    "time":            ("temporal",  "time",                    0.70),
    "timestamp":       ("temporal",  "timestamp",               0.95),
    "datetime":        ("temporal",  "datetime",                0.95),
    "created":         ("temporal",  "audit_created",           0.95),
    "updated":         ("temporal",  "audit_updated",           0.95),
    "modified":        ("temporal",  "audit_updated",           0.95),
    "deleted":         ("temporal",  "audit_deleted",           0.95),
    "inserted":        ("temporal",  "audit_inserted",          0.90),
    "expired":         ("temporal",  "expiry",                  0.90),
    "expiry":          ("temporal",  "expiry",                  0.95),
    "start":           ("temporal",  "start_time",              0.75),
    "end":             ("temporal",  "end_time",                0.75),
    "begin":           ("temporal",  "start_time",              0.70),
    "finish":          ("temporal",  "end_time",                0.70),
    "due":             ("temporal",  "due_date",                0.85),
    "year":            ("temporal",  "year",                    0.85),
    "month":           ("temporal",  "month",                   0.85),
    "day":             ("temporal",  "day",                     0.70),
    "quarter":         ("temporal",  "quarter",                 0.85),
    "week":            ("temporal",  "week",                    0.80),
    "fiscal":          ("temporal",  "fiscal_period",           0.85),

    # Free-text attributes
    "name":            ("attribute", "free_text",               0.80),
    "title":           ("attribute", "free_text",               0.80),
    "desc":            ("attribute", "free_text",               0.85),
    "description":     ("attribute", "free_text",               0.90),
    "summary":         ("attribute", "free_text",               0.85),
    "comment":         ("attribute", "free_text",               0.85),
    "comments":        ("attribute", "free_text",               0.85),
    "note":            ("attribute", "free_text",               0.80),
    "notes":           ("attribute", "free_text",               0.85),
    "remarks":         ("attribute", "free_text",               0.80),
    "message":         ("attribute", "free_text",               0.80),
    "body":            ("attribute", "free_text",               0.75),
    "content":         ("attribute", "free_text",               0.75),
    "text":            ("attribute", "free_text",               0.70),

    # PII (override → role pii)
    "email":           ("pii",       "email",                   0.99),
    "mail":            ("pii",       "email",                   0.85),
    "phone":           ("pii",       "phone",                   0.95),
    "mobile":          ("pii",       "phone",                   0.90),
    "telephone":       ("pii",       "phone",                   0.95),
    "fax":             ("pii",       "phone",                   0.85),
    "address":         ("pii",       "postal_address",          0.95),
    "street":          ("pii",       "street_address",          0.90),
    "ssn":             ("pii",       "ssn",                     0.99),
    "passport":        ("pii",       "passport",                0.95),
    "tax_id":          ("pii",       "tax_id",                  0.95),
    "license":         ("pii",       "license_number",          0.80),
    "iban":            ("pii",       "iban",                    0.95),
    "credit_card":     ("pii",       "credit_card",             0.99),
    "cc_number":       ("pii",       "credit_card",             0.99),
    "card_number":     ("pii",       "credit_card",             0.95),
    "cvv":             ("pii",       "cvv",                     0.99),
    "dob":             ("pii",       "date_of_birth",           0.95),
    "birth":           ("pii",       "date_of_birth",           0.85),
    "first_name":      ("pii",       "given_name",              0.95),
    "last_name":       ("pii",       "family_name",             0.95),
    "full_name":       ("pii",       "person_name",             0.95),
    "username":        ("pii",       "username",                0.85),
    "user_name":       ("pii",       "username",                0.85),
    "password":        ("pii",       "secret",                  0.99),
    "secret":          ("pii",       "secret",                  0.95),
    "ip":              ("pii",       "ip_address",              0.80),
    "ip_address":      ("pii",       "ip_address",              0.95),
    "device_id":       ("pii",       "device_id",               0.85),
    "session_id":      ("pii",       "session_id",              0.85),
    "gender":          ("pii",       "gender",                  0.90),
    "race":            ("pii",       "race",                    0.95),
    "ethnicity":       ("pii",       "ethnicity",               0.95),
    "nationality":     ("pii",       "nationality",             0.85),
    "religion":        ("pii",       "religion",                0.90),

    # Domain-specific FKs
    "customer_id":     ("id",        "customer_fk",             0.95),
    "user_id":         ("id",        "user_fk",                 0.95),
    "order_id":        ("id",        "order_fk",                0.95),
    "product_id":      ("id",        "product_fk",              0.95),
    "transaction_id":  ("id",        "transaction_fk",          0.95),
    "account_id":      ("id",        "account_fk",              0.95),
    "invoice_id":      ("id",        "invoice_fk",              0.95),
    "payment_id":      ("id",        "payment_fk",              0.95),
    "session":         ("id",        "session_fk",              0.70),
    "tenant_id":       ("id",        "tenant_fk",               0.95),
    "org_id":          ("id",        "org_fk",                  0.95),
    "company_id":      ("id",        "company_fk",              0.95),
    "vendor_id":       ("id",        "vendor_fk",               0.90),
    "supplier_id":     ("id",        "supplier_fk",             0.90),
    "store_id":        ("id",        "store_fk",                0.90),
    "warehouse_id":    ("id",        "warehouse_fk",            0.90),
    "campaign_id":     ("id",        "campaign_fk",             0.90),
    "shipment_id":     ("id",        "shipment_fk",             0.90),
    "employee_id":     ("id",        "employee_fk",             0.95),
    "manager_id":      ("id",        "manager_fk",              0.90),
    "lead_id":         ("id",        "lead_fk",                 0.85),
    "opportunity_id":  ("id",        "opportunity_fk",          0.85),
    "ticket_id":       ("id",        "ticket_fk",               0.85),
    "request_id":      ("id",        "request_fk",              0.85),
    "trace_id":        ("id",        "trace_id",                0.90),
    "span_id":         ("id",        "span_id",                 0.90),
    "correlation_id":  ("id",        "correlation_id",          0.90),

    # SaaS metrics
    "mrr":             ("measure",   "monthly_recurring_revenue", 0.95),
    "arr":             ("measure",   "annual_recurring_revenue",  0.95),
    "churn":           ("measure",   "churn_rate",              0.90),
    "ltv":             ("measure",   "lifetime_value",          0.92),
    "cltv":            ("measure",   "lifetime_value",          0.92),
    "gmv":             ("measure",   "gross_merchandise_value", 0.90),
    "nps":             ("measure",   "net_promoter_score",      0.95),
    "csat":            ("measure",   "customer_satisfaction",   0.90),
    "ces":             ("measure",   "customer_effort_score",   0.85),
    "dau":             ("measure",   "daily_active_users",      0.90),
    "mau":             ("measure",   "monthly_active_users",    0.90),
    "wau":             ("measure",   "weekly_active_users",     0.85),

    # Finance metrics
    "opex":            ("measure",   "operating_expense",       0.90),
    "capex":           ("measure",   "capital_expenditure",     0.90),
    "ebitda":          ("measure",   "ebitda",                  0.95),
    "ebit":            ("measure",   "ebit",                    0.92),
    "gross_margin":    ("measure",   "gross_margin",            0.95),
    "net_income":      ("measure",   "net_income",              0.95),
    "net_profit":      ("measure",   "net_profit",              0.95),
    "gross_profit":    ("measure",   "gross_profit",            0.95),
    "coa":             ("dimension", "chart_of_accounts",       0.85),
    "gl_account":      ("id",        "gl_account",              0.92),
    "ledger":          ("dimension", "ledger",                  0.85),
    "debit":           ("measure",   "debit_amount",            0.90),
    "credit":          ("measure",   "credit_amount",           0.90),
    "journal":         ("dimension", "journal_entry",           0.80),

    # Product / SKU codes
    "sku":             ("id",        "sku",                     0.95),
    "upc":             ("id",        "upc",                     0.95),
    "gtin":            ("id",        "gtin",                    0.95),
    "ean":             ("id",        "ean_code",                0.95),
    "isbn":            ("id",        "isbn",                    0.95),
    "asin":            ("id",        "asin",                    0.90),
    "mpn":             ("id",        "manufacturer_part_no",    0.85),

    # Healthcare
    "npi":             ("id",        "npi_number",              0.95),
    "icd10":           ("dimension", "icd10_code",              0.95),
    "icd9":            ("dimension", "icd9_code",               0.90),
    "cpt":             ("dimension", "cpt_code",                0.90),
    "mrn":             ("id",        "medical_record_number",   0.95),
    "patient_id":      ("id",        "patient_fk",              0.95),

    # Marketing / sales
    "aov":             ("measure",   "average_order_value",     0.92),
    "cac":             ("measure",   "customer_acquisition_cost", 0.92),
    "cogs":            ("measure",   "cost_of_goods_sold",      0.95),
    "cpc":             ("measure",   "cost_per_click",          0.92),
    "cpm":             ("measure",   "cost_per_mille",          0.92),
    "cpa":             ("measure",   "cost_per_acquisition",    0.92),
    "roas":            ("measure",   "return_on_ad_spend",      0.92),
    "roi":             ("measure",   "return_on_investment",    0.90),
    "ctr":             ("measure",   "click_through_rate",      0.92),
    "impressions":     ("measure",   "impressions",             0.90),
    "clicks":          ("measure",   "clicks",                  0.90),
    "conversions":     ("measure",   "conversions",             0.90),
    "conversion_rate": ("measure",   "conversion_rate",         0.92),
    "bounce_rate":     ("measure",   "bounce_rate",             0.90),

    # Logistics
    "eta":             ("temporal",  "estimated_arrival",       0.90),
    "etd":             ("temporal",  "estimated_departure",     0.90),
    "dwell":           ("measure",   "dwell_time",              0.85),
    "ldd":             ("temporal",  "latest_delivery_date",    0.85),
    "carrier":         ("dimension", "carrier",                 0.90),
    "tracking_no":     ("id",        "tracking_number",         0.92),
    "tracking_number": ("id",        "tracking_number",         0.95),

    # Finance temporal
    "fiscal_year":     ("temporal",  "fiscal_year",             0.95),
    "fiscal_quarter":  ("temporal",  "fiscal_quarter",          0.95),
    "fiscal_period":   ("temporal",  "fiscal_period",           0.95),
    "accounting_period": ("temporal","accounting_period",       0.95),
    "posting_date":    ("temporal",  "posting_date",            0.92),
    "value_date":      ("temporal",  "value_date",              0.90),

    # Currency suffixes
    "_usd":            ("measure",   "currency_amount_usd",     0.92),
    "_eur":            ("measure",   "currency_amount_eur",     0.92),
    "_gbp":            ("measure",   "currency_amount_gbp",     0.92),
    "_jpy":            ("measure",   "currency_amount_jpy",     0.92),
    "_inr":            ("measure",   "currency_amount_inr",     0.92),
    "_cny":            ("measure",   "currency_amount_cny",     0.92),
    "_cad":            ("measure",   "currency_amount_cad",     0.92),
    "_aud":            ("measure",   "currency_amount_aud",     0.92),

    # Geography
    "latitude":        ("dimension", "latitude",                0.95),
    "longitude":       ("dimension", "longitude",               0.95),
    "lat":             ("dimension", "latitude",                0.85),
    "lng":             ("dimension", "longitude",               0.85),
    "lon":             ("dimension", "longitude",               0.80),
    "geohash":         ("dimension", "geohash",                 0.90),
    "postal_code":     ("dimension", "postal_code",             0.95),
    "zip":             ("dimension", "postal_code",             0.85),
    "zipcode":         ("dimension", "postal_code",             0.90),
    "timezone":        ("dimension", "timezone",                0.92),
    "tz":              ("dimension", "timezone",                0.75),

    # Auditing temporal
    "deleted_at":      ("temporal",  "audit_deleted",           0.95),
    "archived_at":     ("temporal",  "audit_archived",          0.92),
    "last_login_at":   ("temporal",  "last_login",              0.92),
    "last_login":      ("temporal",  "last_login",              0.90),
    "last_seen":       ("temporal",  "last_seen",               0.88),
    "last_seen_at":    ("temporal",  "last_seen",               0.92),
    "first_seen":      ("temporal",  "first_seen",              0.85),
    "signup_date":     ("temporal",  "signup_date",             0.92),
    "registered_at":   ("temporal",  "registered_at",           0.90),

    # Web / product analytics
    "page_views":      ("measure",   "page_views",              0.92),
    "pageviews":       ("measure",   "page_views",              0.90),
    "visits":          ("measure",   "visits",                  0.85),
    "event_name":      ("dimension", "event_name",              0.92),
    "event_type":      ("dimension", "event_type",              0.92),
    "user_agent":      ("attribute", "user_agent",              0.92),
    "referrer":        ("attribute", "referrer_url",            0.85),
    "utm_source":      ("dimension", "utm_source",              0.95),
    "utm_medium":      ("dimension", "utm_medium",              0.95),
    "utm_campaign":    ("dimension", "utm_campaign",            0.95),

    # Tech / audit
    "status_code":     ("dimension", "http_status_code",        0.90),
    "http_method":     ("dimension", "http_method",             0.90),
    "endpoint":        ("dimension", "api_endpoint",            0.85),
    "host":            ("dimension", "host",                    0.75),
    "port":            ("dimension", "port",                    0.75),

    # ML
    "feature_name":    ("dimension", "ml_feature",              0.92),
    "prediction":      ("measure",   "ml_prediction",           0.90),
    "probability":     ("measure",   "probability",             0.90),
    "model_version":   ("dimension", "model_version",           0.92),
    "model_name":      ("dimension", "model_name",              0.90),
    "confidence":      ("measure",   "confidence_score",        0.85),
    # "label" duplicate removed (already defined above with same values)

    # HR
    "hire_date":       ("temporal",  "hire_date",               0.95),
    "termination_date":("temporal",  "termination_date",        0.95),
    "tenure":          ("measure",   "tenure",                  0.85),
    "headcount":       ("measure",   "headcount",               0.92),
    "fte":             ("measure",   "full_time_equivalent",    0.90),

    # Inventory
    "units_on_hand":   ("measure",   "inventory_qty",           0.92),
    "reorder_point":   ("measure",   "reorder_point",           0.90),
    "safety_stock":    ("measure",   "safety_stock",            0.90),
    "lead_time":       ("measure",   "lead_time_days",          0.88),
}


# ---------------------------------------------------------------------------
# 3. Brain seed: canonical column exemplars for embedding-similarity matching
# ---------------------------------------------------------------------------

BRAIN_SEED_COLUMNS: list[dict] = [
    # === SALES & ECOMMERCE ===
    {"name": "order_id",         "role": "id",        "semantic": "order_pk",            "samples": ["O-2024-001", "ORD-99812", "1024"]},
    {"name": "ord_id",           "role": "id",        "semantic": "order_pk",            "samples": ["O1023", "9981"]},
    {"name": "order_number",     "role": "id",        "semantic": "order_pk",            "samples": ["100023", "100024"]},
    {"name": "order_date",       "role": "temporal",  "semantic": "transaction_date",    "samples": ["2024-01-15", "2024-02-03"]},
    {"name": "order_dt",         "role": "temporal",  "semantic": "transaction_date",    "samples": ["2024-01-15", "2024-02-03"]},
    {"name": "order_status",     "role": "dimension", "semantic": "order_status",        "samples": ["completed", "shipped", "cancelled"]},
    {"name": "line_item_id",     "role": "id",        "semantic": "order_line_pk",       "samples": ["LI-001", "LI-002"]},
    {"name": "line_total",       "role": "measure",   "semantic": "currency_amount",     "samples": ["129.99", "245.00"]},
    {"name": "line_amount",      "role": "measure",   "semantic": "currency_amount",     "samples": ["99.95", "1200.00"]},
    {"name": "unit_price",       "role": "measure",   "semantic": "currency_amount",     "samples": ["19.95", "99.00"]},
    {"name": "list_price",       "role": "measure",   "semantic": "currency_amount",     "samples": ["29.99", "59.00"]},
    {"name": "sale_price",       "role": "measure",   "semantic": "currency_amount",     "samples": ["24.99", "49.00"]},
    {"name": "quantity",         "role": "measure",   "semantic": "quantity",            "samples": ["1", "5", "12"]},
    {"name": "qty_ordered",      "role": "measure",   "semantic": "quantity",            "samples": ["2", "10"]},
    {"name": "qty_shipped",      "role": "measure",   "semantic": "quantity",            "samples": ["2", "8"]},
    {"name": "discount_pct",     "role": "measure",   "semantic": "percentage",          "samples": ["0.10", "0.25"]},
    {"name": "discount_amount",  "role": "measure",   "semantic": "currency_amount",     "samples": ["10.00", "25.00"]},
    {"name": "tax_amount",       "role": "measure",   "semantic": "currency_amount",     "samples": ["8.50", "12.40"]},
    {"name": "tax_rate",         "role": "measure",   "semantic": "percentage",          "samples": ["0.08", "0.20"]},
    {"name": "shipping_cost",    "role": "measure",   "semantic": "currency_amount",     "samples": ["5.99", "12.00"]},
    {"name": "subtotal",         "role": "measure",   "semantic": "currency_amount",     "samples": ["199.99", "450.00"]},
    {"name": "grand_total",      "role": "measure",   "semantic": "currency_amount",     "samples": ["218.49", "489.20"]},
    {"name": "revenue",          "role": "measure",   "semantic": "currency_amount_usd", "samples": ["1240.00", "5670.50"]},
    {"name": "gross_revenue",    "role": "measure",   "semantic": "currency_amount_usd", "samples": ["10000.00", "25000.00"]},
    {"name": "net_revenue",      "role": "measure",   "semantic": "currency_amount_usd", "samples": ["9000.00", "23000.00"]},
    {"name": "aov",              "role": "measure",   "semantic": "average_order_value", "samples": ["85.40", "120.00"]},
    {"name": "gmv",              "role": "measure",   "semantic": "gross_merchandise_value","samples":["1500000.00", "2200000.00"]},
    {"name": "refund_amount",    "role": "measure",   "semantic": "currency_amount",     "samples": ["29.99", "100.00"]},
    {"name": "ship_date",        "role": "temporal",  "semantic": "ship_date",           "samples": ["2024-01-17", "2024-02-05"]},
    {"name": "fulfillment_status","role":"dimension", "semantic": "fulfillment_status",  "samples": ["pending", "fulfilled", "returned"]},

    # === FINANCE & ACCOUNTING ===
    {"name": "gl_account",       "role": "id",        "semantic": "gl_account",          "samples": ["4000-100", "5200-300"]},
    {"name": "account_number",   "role": "id",        "semantic": "account_number",      "samples": ["ACC-1001", "ACC-1002"]},
    {"name": "account_name",     "role": "dimension", "semantic": "account_name",        "samples": ["Cash", "Accounts Receivable"]},
    {"name": "debit_amount",     "role": "measure",   "semantic": "debit_amount",        "samples": ["1500.00", "2300.50"]},
    {"name": "credit_amount",    "role": "measure",   "semantic": "credit_amount",       "samples": ["1500.00", "2300.50"]},
    {"name": "balance",          "role": "measure",   "semantic": "currency_amount",     "samples": ["10500.00", "-2300.00"]},
    {"name": "opening_balance",  "role": "measure",   "semantic": "currency_amount",     "samples": ["0.00", "5000.00"]},
    {"name": "closing_balance",  "role": "measure",   "semantic": "currency_amount",     "samples": ["12500.00", "8000.00"]},
    {"name": "fiscal_year",      "role": "temporal",  "semantic": "fiscal_year",         "samples": ["FY2023", "FY2024"]},
    {"name": "fiscal_quarter",   "role": "temporal",  "semantic": "fiscal_quarter",      "samples": ["Q1-2024", "Q2-2024"]},
    {"name": "fiscal_period",    "role": "temporal",  "semantic": "fiscal_period",       "samples": ["2024-P01", "2024-P12"]},
    {"name": "accounting_period","role": "temporal",  "semantic": "accounting_period",   "samples": ["2024-01", "2024-02"]},
    {"name": "posting_date",     "role": "temporal",  "semantic": "posting_date",        "samples": ["2024-03-31", "2024-04-30"]},
    {"name": "value_date",       "role": "temporal",  "semantic": "value_date",          "samples": ["2024-03-29", "2024-04-28"]},
    {"name": "currency_code",    "role": "dimension", "semantic": "iso_currency",        "samples": ["USD", "EUR", "JPY"]},
    {"name": "exchange_rate",    "role": "measure",   "semantic": "exchange_rate",       "samples": ["1.0850", "0.7820"]},
    {"name": "ebitda",           "role": "measure",   "semantic": "ebitda",              "samples": ["1200000", "1500000"]},
    {"name": "net_income",       "role": "measure",   "semantic": "net_income",          "samples": ["450000", "620000"]},
    {"name": "gross_profit",     "role": "measure",   "semantic": "gross_profit",        "samples": ["3500000", "4200000"]},
    {"name": "gross_margin",     "role": "measure",   "semantic": "gross_margin",        "samples": ["0.42", "0.55"]},
    {"name": "opex",             "role": "measure",   "semantic": "operating_expense",   "samples": ["850000", "920000"]},
    {"name": "capex",            "role": "measure",   "semantic": "capital_expenditure", "samples": ["250000", "500000"]},
    {"name": "cogs",             "role": "measure",   "semantic": "cost_of_goods_sold",  "samples": ["1800000", "2100000"]},
    {"name": "journal_id",       "role": "id",        "semantic": "journal_entry_pk",    "samples": ["JE-2024-001", "JE-2024-002"]},
    {"name": "invoice_id",       "role": "id",        "semantic": "invoice_pk",          "samples": ["INV-99812", "INV-99813"]},

    # === CUSTOMER & CRM ===
    {"name": "customer_id",      "role": "id",        "semantic": "customer_pk",         "samples": ["C0001", "C0002", "1234"]},
    {"name": "cust_id",          "role": "id",        "semantic": "customer_pk",         "samples": ["C0001", "1234"]},
    {"name": "customer_name",    "role": "attribute", "semantic": "customer_name",       "samples": ["Acme Corp", "Globex Ltd"]},
    {"name": "lead_id",          "role": "id",        "semantic": "lead_pk",             "samples": ["L-1001", "L-1002"]},
    {"name": "lead_source",      "role": "dimension", "semantic": "lead_source",         "samples": ["organic_search", "paid_ads", "referral"]},
    {"name": "lead_status",      "role": "dimension", "semantic": "lead_status",         "samples": ["new", "qualified", "converted"]},
    {"name": "opportunity_id",   "role": "id",        "semantic": "opportunity_pk",      "samples": ["OPP-501", "OPP-502"]},
    {"name": "opportunity_stage","role": "dimension", "semantic": "sales_stage",         "samples": ["prospecting", "negotiation", "closed_won"]},
    {"name": "deal_size",        "role": "measure",   "semantic": "currency_amount",     "samples": ["25000", "150000"]},
    {"name": "mrr",              "role": "measure",   "semantic": "monthly_recurring_revenue","samples":["1500.00", "5000.00"]},
    {"name": "arr",              "role": "measure",   "semantic": "annual_recurring_revenue", "samples": ["18000", "60000"]},
    {"name": "ltv_usd",          "role": "measure",   "semantic": "lifetime_value",      "samples": ["1200.00", "8500.00"]},
    {"name": "cltv",             "role": "measure",   "semantic": "lifetime_value",      "samples": ["1200.00", "8500.00"]},
    {"name": "churn_date",       "role": "temporal",  "semantic": "churn_date",          "samples": ["2024-04-15", "2024-05-22"]},
    {"name": "churn_reason",     "role": "dimension", "semantic": "churn_reason",        "samples": ["price", "competitor", "no_longer_needed"]},
    {"name": "nps_score",        "role": "measure",   "semantic": "net_promoter_score",  "samples": ["9", "7", "3"]},
    {"name": "csat_score",       "role": "measure",   "semantic": "customer_satisfaction","samples": ["4.5", "4.8"]},
    {"name": "customer_segment", "role": "dimension", "semantic": "customer_segment",    "samples": ["enterprise", "smb", "mid-market"]},
    {"name": "customer_tier",    "role": "dimension", "semantic": "customer_tier",       "samples": ["gold", "silver", "bronze"]},
    {"name": "account_owner",    "role": "dimension", "semantic": "account_owner",       "samples": ["jdoe", "asmith"]},

    # === PRODUCT & INVENTORY ===
    {"name": "product_id",       "role": "id",        "semantic": "product_pk",          "samples": ["P-001", "SKU-1023"]},
    {"name": "prod_id",          "role": "id",        "semantic": "product_pk",          "samples": ["P-001", "P-002"]},
    {"name": "sku",              "role": "id",        "semantic": "stock_keeping_unit",  "samples": ["SKU-WIDGET-RED", "ABC-001"]},
    {"name": "upc",              "role": "id",        "semantic": "upc",                 "samples": ["012345678905", "036000291452"]},
    {"name": "ean",              "role": "id",        "semantic": "ean_code",            "samples": ["5901234123457", "4006381333931"]},
    {"name": "gtin",             "role": "id",        "semantic": "gtin",                "samples": ["00012345678905"]},
    {"name": "isbn",             "role": "id",        "semantic": "isbn",                "samples": ["978-3-16-148410-0"]},
    {"name": "asin",             "role": "id",        "semantic": "asin",                "samples": ["B07PXGQC1Q", "B08N5WRWNW"]},
    {"name": "product_name",     "role": "attribute", "semantic": "product_name",        "samples": ["Wireless Mouse", "USB-C Cable 2m"]},
    {"name": "brand",            "role": "dimension", "semantic": "brand",               "samples": ["Sony", "Apple", "Samsung"]},
    {"name": "category_id",      "role": "id",        "semantic": "category_fk",         "samples": ["CAT-001", "CAT-002"]},
    {"name": "category_name",    "role": "dimension", "semantic": "category",            "samples": ["Electronics", "Apparel"]},
    {"name": "subcategory",      "role": "dimension", "semantic": "subcategory",         "samples": ["Headphones", "Smartphones"]},
    {"name": "units_on_hand",    "role": "measure",   "semantic": "inventory_qty",       "samples": ["150", "23"]},
    {"name": "stock_level",      "role": "measure",   "semantic": "inventory_qty",       "samples": ["150", "23"]},
    {"name": "reorder_point",    "role": "measure",   "semantic": "reorder_point",       "samples": ["50", "100"]},
    {"name": "safety_stock",     "role": "measure",   "semantic": "safety_stock",        "samples": ["20", "40"]},
    {"name": "warehouse_id",     "role": "id",        "semantic": "warehouse_fk",        "samples": ["WH-NYC-01", "WH-LAX-02"]},
    {"name": "bin_location",     "role": "dimension", "semantic": "bin_location",        "samples": ["A1-03-12", "B2-05-08"]},
    {"name": "weight_kg",        "role": "measure",   "semantic": "weight_kg",           "samples": ["1.25", "0.80"]},

    # === HR & PEOPLE ===
    {"name": "employee_id",      "role": "id",        "semantic": "employee_pk",         "samples": ["E-1001", "E-1002"]},
    {"name": "emp_id",           "role": "id",        "semantic": "employee_pk",         "samples": ["E1001", "E1002"]},
    {"name": "employee_number",  "role": "id",        "semantic": "employee_pk",         "samples": ["100001", "100002"]},
    {"name": "first_name",       "role": "pii",       "semantic": "given_name",          "samples": ["Alice", "Bob"]},
    {"name": "last_name",        "role": "pii",       "semantic": "family_name",         "samples": ["Smith", "Johnson"]},
    {"name": "full_name",        "role": "pii",       "semantic": "person_name",         "samples": ["Alice Smith", "Bob Johnson"]},
    {"name": "hire_date",        "role": "temporal",  "semantic": "hire_date",           "samples": ["2019-03-15", "2021-07-01"]},
    {"name": "termination_date", "role": "temporal",  "semantic": "termination_date",    "samples": ["2023-12-31", "2024-02-28"]},
    {"name": "salary_usd",       "role": "measure",   "semantic": "currency_amount_usd", "samples": ["75000", "120000"]},
    {"name": "annual_salary",    "role": "measure",   "semantic": "currency_amount_usd", "samples": ["75000", "120000"]},
    {"name": "hourly_rate",      "role": "measure",   "semantic": "currency_amount_usd", "samples": ["35.50", "62.00"]},
    {"name": "bonus_amount",     "role": "measure",   "semantic": "currency_amount",     "samples": ["5000", "12000"]},
    {"name": "department",       "role": "dimension", "semantic": "department",          "samples": ["Engineering", "Sales", "HR"]},
    {"name": "department_id",    "role": "id",        "semantic": "department_fk",       "samples": ["DEPT-ENG", "DEPT-SAL"]},
    {"name": "manager_id",       "role": "id",        "semantic": "manager_fk",          "samples": ["E-1001", "E-1099"]},
    {"name": "job_title",        "role": "attribute", "semantic": "job_title",           "samples": ["Senior Engineer", "Sales Director"]},
    {"name": "job_level",        "role": "dimension", "semantic": "job_level",           "samples": ["L4", "L5", "L6"]},
    {"name": "headcount",        "role": "measure",   "semantic": "headcount",           "samples": ["12", "85"]},
    {"name": "fte",              "role": "measure",   "semantic": "full_time_equivalent","samples": ["1.0", "0.8"]},
    {"name": "tenure_years",     "role": "measure",   "semantic": "tenure",              "samples": ["3.5", "8.2"]},

    # === SUPPLY CHAIN ===
    {"name": "shipment_id",      "role": "id",        "semantic": "shipment_pk",         "samples": ["SHP-2024-001", "SHP-2024-002"]},
    {"name": "carrier",          "role": "dimension", "semantic": "carrier",             "samples": ["FedEx", "UPS", "DHL"]},
    {"name": "tracking_no",      "role": "id",        "semantic": "tracking_number",     "samples": ["1Z999AA10123456784", "9405511899223197428490"]},
    {"name": "tracking_number",  "role": "id",        "semantic": "tracking_number",     "samples": ["1Z999AA10123456784"]},
    {"name": "eta",              "role": "temporal",  "semantic": "estimated_arrival",   "samples": ["2024-04-22", "2024-04-25"]},
    {"name": "etd",              "role": "temporal",  "semantic": "estimated_departure", "samples": ["2024-04-18", "2024-04-19"]},
    {"name": "actual_delivery_date","role": "temporal","semantic": "actual_delivery",    "samples": ["2024-04-23", "2024-04-26"]},
    {"name": "origin_port",      "role": "dimension", "semantic": "origin_port",         "samples": ["USNYC", "CNSHA"]},
    {"name": "destination_port", "role": "dimension", "semantic": "destination_port",    "samples": ["NLRTM", "DEHAM"]},
    {"name": "container_id",     "role": "id",        "semantic": "container_id",        "samples": ["MSCU1234567", "MAEU7654321"]},
    {"name": "po_number",        "role": "id",        "semantic": "purchase_order_pk",   "samples": ["PO-2024-1001", "PO-2024-1002"]},
    {"name": "supplier_id",      "role": "id",        "semantic": "supplier_fk",         "samples": ["SUP-001", "SUP-002"]},
    {"name": "supplier_name",    "role": "attribute", "semantic": "supplier_name",       "samples": ["Foxconn", "Pegatron"]},
    {"name": "lead_time_days",   "role": "measure",   "semantic": "lead_time_days",      "samples": ["14", "30"]},
    {"name": "freight_cost",     "role": "measure",   "semantic": "currency_amount",     "samples": ["1200.00", "3500.00"]},

    # === MARKETING ===
    {"name": "campaign_id",      "role": "id",        "semantic": "campaign_pk",         "samples": ["CMP-2024-Q1", "CMP-2024-Q2"]},
    {"name": "campaign_name",    "role": "attribute", "semantic": "campaign_name",       "samples": ["Spring Sale 2024", "Holiday Promo"]},
    {"name": "channel",          "role": "dimension", "semantic": "channel",             "samples": ["email", "paid_search", "social"]},
    {"name": "impressions",      "role": "measure",   "semantic": "impressions",         "samples": ["125000", "85000"]},
    {"name": "clicks",           "role": "measure",   "semantic": "clicks",              "samples": ["3200", "1800"]},
    {"name": "ctr",              "role": "measure",   "semantic": "click_through_rate",  "samples": ["0.025", "0.038"]},
    {"name": "conversions",      "role": "measure",   "semantic": "conversions",         "samples": ["120", "85"]},
    {"name": "conversion_rate",  "role": "measure",   "semantic": "conversion_rate",     "samples": ["0.0375", "0.0472"]},
    {"name": "cpc",              "role": "measure",   "semantic": "cost_per_click",      "samples": ["0.85", "1.20"]},
    {"name": "cpm",              "role": "measure",   "semantic": "cost_per_mille",      "samples": ["12.50", "8.75"]},
    {"name": "cpa",              "role": "measure",   "semantic": "cost_per_acquisition","samples": ["35.00", "42.50"]},
    {"name": "cac",              "role": "measure",   "semantic": "customer_acquisition_cost","samples": ["120.00", "350.00"]},
    {"name": "roas",             "role": "measure",   "semantic": "return_on_ad_spend",  "samples": ["3.5", "4.2"]},
    {"name": "ad_spend",         "role": "measure",   "semantic": "currency_amount",     "samples": ["5000.00", "12000.00"]},
    {"name": "utm_source",       "role": "dimension", "semantic": "utm_source",          "samples": ["google", "facebook", "newsletter"]},

    # === WEB & PRODUCT ANALYTICS ===
    {"name": "session_id",       "role": "id",        "semantic": "session_pk",          "samples": ["sess_abc123", "sess_def456"]},
    {"name": "page_views",       "role": "measure",   "semantic": "page_views",          "samples": ["12500", "8200"]},
    {"name": "pageviews",        "role": "measure",   "semantic": "page_views",          "samples": ["12500", "8200"]},
    {"name": "visits",           "role": "measure",   "semantic": "visits",              "samples": ["3500", "2100"]},
    {"name": "unique_visitors",  "role": "measure",   "semantic": "unique_visitors",     "samples": ["2800", "1750"]},
    {"name": "bounce_rate",      "role": "measure",   "semantic": "bounce_rate",         "samples": ["0.42", "0.35"]},
    {"name": "session_duration", "role": "measure",   "semantic": "duration_seconds",    "samples": ["185", "320"]},
    {"name": "event_name",       "role": "dimension", "semantic": "event_name",          "samples": ["page_view", "add_to_cart", "purchase"]},
    {"name": "event_type",       "role": "dimension", "semantic": "event_type",          "samples": ["click", "scroll", "submit"]},
    {"name": "page_url",         "role": "attribute", "semantic": "page_url",            "samples": ["/home", "/products/123"]},
    {"name": "referrer_url",     "role": "attribute", "semantic": "referrer_url",        "samples": ["https://google.com", "https://facebook.com"]},
    {"name": "device_type",      "role": "dimension", "semantic": "device_type",         "samples": ["desktop", "mobile", "tablet"]},
    {"name": "browser",          "role": "dimension", "semantic": "browser",             "samples": ["Chrome", "Safari", "Firefox"]},
    {"name": "os",               "role": "dimension", "semantic": "operating_system",    "samples": ["macOS", "Windows", "iOS"]},
    {"name": "dau",              "role": "measure",   "semantic": "daily_active_users",  "samples": ["12500", "13800"]},

    # === GEOGRAPHY ===
    {"name": "country",          "role": "dimension", "semantic": "geo_country",         "samples": ["United States", "France", "Japan"]},
    {"name": "country_code",     "role": "dimension", "semantic": "iso_country_code",    "samples": ["US", "FR", "JP"]},
    {"name": "country_iso3",     "role": "dimension", "semantic": "iso_country_code3",   "samples": ["USA", "FRA", "JPN"]},
    {"name": "region",           "role": "dimension", "semantic": "geo_region",          "samples": ["EMEA", "APAC", "Americas"]},
    {"name": "state",            "role": "dimension", "semantic": "geo_state",           "samples": ["California", "Texas", "New York"]},
    {"name": "state_code",       "role": "dimension", "semantic": "geo_state_code",      "samples": ["CA", "TX", "NY"]},
    {"name": "city",             "role": "dimension", "semantic": "geo_city",            "samples": ["Paris", "Tokyo", "Buenos Aires"]},
    {"name": "postal_code",      "role": "dimension", "semantic": "postal_code",         "samples": ["94025", "10001", "SW1A 1AA"]},
    {"name": "zip_code",         "role": "dimension", "semantic": "postal_code",         "samples": ["94025", "10001"]},
    {"name": "latitude",         "role": "dimension", "semantic": "latitude",            "samples": ["37.4419", "48.8566"]},
    {"name": "longitude",        "role": "dimension", "semantic": "longitude",           "samples": ["-122.1430", "2.3522"]},
    {"name": "lat",              "role": "dimension", "semantic": "latitude",            "samples": ["37.4419", "48.8566"]},
    {"name": "lng",              "role": "dimension", "semantic": "longitude",           "samples": ["-122.1430", "2.3522"]},
    {"name": "geohash",          "role": "dimension", "semantic": "geohash",             "samples": ["9q9hvu", "u09tunqu"]},
    {"name": "timezone",         "role": "dimension", "semantic": "timezone",            "samples": ["America/Los_Angeles", "Europe/Paris"]},

    # === TEMPORAL ===
    {"name": "created_at",       "role": "temporal",  "semantic": "audit_created",       "samples": ["2024-03-12T08:14:33Z"]},
    {"name": "creation_date",    "role": "temporal",  "semantic": "audit_created",       "samples": ["2024-03-12", "2024-03-13"]},
    {"name": "updated_at",       "role": "temporal",  "semantic": "audit_updated",       "samples": ["2024-04-01T10:00:00Z"]},
    {"name": "modified_at",      "role": "temporal",  "semantic": "audit_updated",       "samples": ["2024-04-01T10:00:00Z"]},
    {"name": "deleted_at",       "role": "temporal",  "semantic": "audit_deleted",       "samples": ["2024-05-15T12:00:00Z"]},
    {"name": "archived_at",      "role": "temporal",  "semantic": "audit_archived",      "samples": ["2024-05-20T08:00:00Z"]},
    {"name": "started_at",       "role": "temporal",  "semantic": "start_time",          "samples": ["2024-03-12T08:00:00Z"]},
    {"name": "ended_at",         "role": "temporal",  "semantic": "end_time",            "samples": ["2024-03-12T17:00:00Z"]},
    {"name": "completed_at",     "role": "temporal",  "semantic": "completion_time",     "samples": ["2024-03-12T16:30:00Z"]},
    {"name": "due_date",         "role": "temporal",  "semantic": "due_date",            "samples": ["2024-04-30", "2024-05-15"]},
    {"name": "expires_at",       "role": "temporal",  "semantic": "expiry",              "samples": ["2025-01-01T00:00:00Z"]},
    {"name": "last_login_at",    "role": "temporal",  "semantic": "last_login",          "samples": ["2024-04-30T18:42:11Z"]},
    {"name": "last_seen_at",     "role": "temporal",  "semantic": "last_seen",           "samples": ["2024-04-30T18:45:00Z"]},
    {"name": "signup_date",      "role": "temporal",  "semantic": "signup_date",         "samples": ["2023-08-15", "2023-09-22"]},
    {"name": "registered_at",    "role": "temporal",  "semantic": "registered_at",       "samples": ["2023-08-15T14:00:00Z"]},

    # === TECH/IDS/AUDIT ===
    {"name": "uuid",             "role": "id",        "semantic": "uuid",                "samples": ["550e8400-e29b-41d4-a716-446655440000"]},
    {"name": "guid",             "role": "id",        "semantic": "uuid",                "samples": ["550e8400-e29b-41d4-a716-446655440000"]},
    {"name": "request_id",       "role": "id",        "semantic": "request_id",          "samples": ["req_abc123", "req_def456"]},
    {"name": "trace_id",         "role": "id",        "semantic": "trace_id",            "samples": ["1-5f4d3e2c-0a1b2c3d4e5f"]},
    {"name": "span_id",          "role": "id",        "semantic": "span_id",             "samples": ["00f067aa0ba902b7"]},
    {"name": "correlation_id",   "role": "id",        "semantic": "correlation_id",      "samples": ["corr_abc123"]},
    {"name": "ip_address",       "role": "pii",       "semantic": "ip_address",          "samples": ["192.168.1.5", "8.8.8.8"]},
    {"name": "user_agent",       "role": "attribute", "semantic": "user_agent",          "samples": ["Mozilla/5.0 (Macintosh)", "curl/7.79.1"]},
    {"name": "status_code",      "role": "dimension", "semantic": "http_status_code",    "samples": ["200", "404", "500"]},
    {"name": "http_method",      "role": "dimension", "semantic": "http_method",         "samples": ["GET", "POST", "PUT"]},
    {"name": "endpoint",         "role": "dimension", "semantic": "api_endpoint",        "samples": ["/api/users", "/api/orders"]},
    {"name": "latency_ms",       "role": "measure",   "semantic": "duration_ms",         "samples": ["120", "850"]},
    {"name": "error_code",       "role": "dimension", "semantic": "error_code",          "samples": ["E1001", "AUTH_FAIL"]},
    {"name": "log_level",        "role": "dimension", "semantic": "log_level",           "samples": ["INFO", "WARN", "ERROR"]},
    {"name": "version",          "role": "dimension", "semantic": "version",             "samples": ["1.2.3", "2.0.0"]},

    # === ML FEATURES ===
    {"name": "feature_name",     "role": "dimension", "semantic": "ml_feature",          "samples": ["age", "income", "click_count_30d"]},
    {"name": "feature_value",    "role": "measure",   "semantic": "ml_feature_value",    "samples": ["0.42", "1250.00"]},
    {"name": "prediction",       "role": "measure",   "semantic": "ml_prediction",       "samples": ["0.87", "1240.50"]},
    {"name": "predicted_value",  "role": "measure",   "semantic": "ml_prediction",       "samples": ["1240.50", "560.00"]},
    {"name": "predicted_class",  "role": "dimension", "semantic": "ml_class",            "samples": ["churn", "retain"]},
    {"name": "probability",      "role": "measure",   "semantic": "probability",         "samples": ["0.87", "0.42"]},
    {"name": "score",            "role": "measure",   "semantic": "score",               "samples": ["0.87", "92.5"]},
    {"name": "model_version",    "role": "dimension", "semantic": "model_version",       "samples": ["v1.2.3", "v2.0.0"]},
    {"name": "model_name",       "role": "dimension", "semantic": "model_name",          "samples": ["churn_xgb", "ltv_lgbm"]},
    {"name": "confidence",       "role": "measure",   "semantic": "confidence_score",    "samples": ["0.92", "0.78"]},

    # === SECURITY & PII ===
    {"name": "email",            "role": "pii",       "semantic": "email",               "samples": ["alice@example.com", "bob@example.org"]},
    {"name": "email_address",    "role": "pii",       "semantic": "email",               "samples": ["alice@example.com"]},
    {"name": "phone",            "role": "pii",       "semantic": "phone",               "samples": ["+14155550123", "+447911123456"]},
    {"name": "phone_number",     "role": "pii",       "semantic": "phone",               "samples": ["+14155550123"]},
    {"name": "ssn",              "role": "pii",       "semantic": "ssn",                 "samples": ["123-45-6789"]},
    {"name": "ssn_last4",        "role": "pii",       "semantic": "ssn_last4",           "samples": ["6789", "1234"]},
    {"name": "address",          "role": "pii",       "semantic": "postal_address",      "samples": ["1 Infinite Loop, Cupertino, CA"]},
    {"name": "birth_date",       "role": "pii",       "semantic": "date_of_birth",       "samples": ["1985-04-12", "1992-11-30"]},
    {"name": "date_of_birth",    "role": "pii",       "semantic": "date_of_birth",       "samples": ["1985-04-12"]},
    {"name": "passport_number",  "role": "pii",       "semantic": "passport",            "samples": ["AB1234567", "CD7654321"]},

    # === HEALTHCARE ===
    {"name": "patient_id",       "role": "id",        "semantic": "patient_pk",          "samples": ["PT-2024-0001", "PT-2024-0002"]},
    {"name": "mrn",              "role": "id",        "semantic": "medical_record_number","samples": ["MRN-998123", "MRN-998124"]},
    {"name": "icd10_code",       "role": "dimension", "semantic": "icd10_code",          "samples": ["E11.9", "I10", "J45.909"]},
    {"name": "cpt_code",         "role": "dimension", "semantic": "cpt_code",            "samples": ["99213", "93000"]},
    {"name": "npi",              "role": "id",        "semantic": "npi_number",          "samples": ["1234567893", "1987654321"]},

    # === MISC ATTRIBUTES (kept from original) ===
    {"name": "transaction_id",   "role": "id",        "semantic": "transaction_pk",      "samples": ["txn_a8f9b3", "T-2024-7711"]},
    {"name": "user_id",          "role": "id",        "semantic": "user_pk",             "samples": ["U_98123", "42", "alice"]},
    {"name": "amount",           "role": "measure",   "semantic": "currency_amount",     "samples": ["49.99", "1200.00"]},
    {"name": "price",            "role": "measure",   "semantic": "currency_amount",     "samples": ["19.95", "99.00"]},
    {"name": "profit_margin",    "role": "measure",   "semantic": "percentage",          "samples": ["0.15", "0.32"]},
    {"name": "currency",         "role": "dimension", "semantic": "iso_currency",        "samples": ["USD", "EUR", "JPY"]},
    {"name": "status",           "role": "dimension", "semantic": "status",              "samples": ["active", "pending", "cancelled"]},
    {"name": "category",         "role": "dimension", "semantic": "category",            "samples": ["electronics", "apparel"]},
    {"name": "description",      "role": "attribute", "semantic": "free_text",           "samples": ["Premium widget, blue", "Imported from EU"]},
    {"name": "name",             "role": "attribute", "semantic": "free_text",           "samples": ["Acme Corp", "Widget XL"]},
    {"name": "title",            "role": "attribute", "semantic": "free_text",           "samples": ["Senior Engineer", "Chief Officer"]},
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def match_name(col_name: str) -> list[tuple[str, str, float]]:
    """Return list of ``(role, semantic, confidence)`` tuples for vocabulary
    keys that match ``col_name``.

    Match rules (case-insensitive):
        1. Exact match.
        2. ``col_name`` ends with key (e.g. ``order_at`` ↔ ``_at``).
        3. Key is a substring of ``col_name``.

    Multiple keys may match; caller is responsible for fusion (typically
    picking the highest-confidence entry).
    """
    if not col_name:
        return []
    lc = col_name.lower()
    out: list[tuple[str, str, float]] = []
    for key, hint in NAME_VOCABULARY.items():
        k = key.lower()
        if lc == k or lc.endswith(k) or k in lc:
            out.append(hint)
    return out
