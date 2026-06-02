"""Heuristic semantic type detector (no LLM).

Returns one of:
  CURRENCY | DATE | BARCODE | EMAIL | PHONE | URL | LANG-MY | LANG-EN
  | ENUM | FREE-TEXT | BOOLEAN | ID | NUMERIC | TEXT
"""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"^https?://", re.I)
_PHONE_RE = re.compile(r"^\+?\d[\d\s\-\(\)]{7,}$")
_BARCODE_RE = re.compile(r"^\d{12,14}$")
_DATE_RE_1 = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DATE_RE_2 = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")
_MY_RE = re.compile(r"[က-႟]")  # Myanmar Unicode block က-႟
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_CURRENCY_RE = re.compile(r"^\s*[\$€£¥₹]\s*-?\d|(\bUSD\b|\bEUR\b|\bMMK\b|\bMMKKS\b|\bKYAT\b|\bKS\b)", re.I)

_BOOLISH = {"true", "false", "yes", "no", "y", "n", "0", "1", "t", "f"}
_ID_NAME_HINTS = ("_id", "id", "uuid", "guid", "code")
_CURRENCY_NAME_HINTS = ("price", "cost", "amount", "revenue", "salary", "wage",
                        "value", "fee", "total", "subtotal", "balance", "mmk",
                        "usd", "kyat")


def _name_hits(col_name: str, hints) -> bool:
    n = (col_name or "").lower()
    return any(h in n for h in hints)


def _all(samples, pred) -> bool:
    real = [s for s in samples if s is not None and str(s).strip() != ""]
    if not real:
        return False
    return all(pred(str(s).strip()) for s in real)


def _any(samples, pred) -> bool:
    return any(pred(str(s).strip()) for s in samples if s is not None and str(s).strip() != "")


def detect_semantic_type(col_name: str, dtype: str, samples: list) -> str:
    """Classify a column from name + dtype + a few sample values."""
    col = (col_name or "").lower()
    dt = (dtype or "").lower()
    samples = [s for s in (samples or []) if s is not None and str(s).strip() != ""]
    str_samples = [str(s).strip() for s in samples]

    # Boolean — very small distinct set
    distinct = {s.lower() for s in str_samples}
    if 0 < len(distinct) <= 2 and distinct.issubset(_BOOLISH):
        return "BOOLEAN"

    # Barcode (all-numeric 12–14 digits across all samples)
    if str_samples and _all(str_samples, lambda s: bool(_BARCODE_RE.match(s))):
        return "BARCODE"

    # Email
    if str_samples and _any(str_samples, lambda s: bool(_EMAIL_RE.search(s))):
        return "EMAIL"

    # URL
    if str_samples and _any(str_samples, lambda s: bool(_URL_RE.match(s))):
        return "URL"

    # Date — by dtype OR by sample pattern (BEFORE phone — '2026-01-01' would otherwise
    # match the phone regex due to digits + hyphens)
    if "date" in dt or "time" in dt or "timestamp" in dt:
        return "DATE"
    if str_samples and _any(str_samples, lambda s: bool(_DATE_RE_1.match(s) or _DATE_RE_2.match(s))):
        return "DATE"

    # Phone
    if str_samples and _any(str_samples, lambda s: bool(_PHONE_RE.match(s))):
        return "PHONE"

    # Myanmar text
    if str_samples and _any(str_samples, lambda s: bool(_MY_RE.search(s))):
        return "LANG-MY"

    # Currency — name hint OR symbol/code in samples
    if _name_hits(col, _CURRENCY_NAME_HINTS):
        return "CURRENCY"
    if str_samples and _any(str_samples, lambda s: bool(_CURRENCY_RE.search(s))):
        return "CURRENCY"

    # Numeric dtype OR all-numeric samples
    is_numeric_dtype = any(t in dt for t in ("int", "float", "numeric", "double", "decimal", "real"))
    all_numeric_samples = bool(str_samples) and _all(str_samples, lambda s: bool(_NUMERIC_RE.match(s)))

    # ID — name ends with _id OR equals 'id'
    if col.endswith("_id") or col == "id":
        if is_numeric_dtype or all_numeric_samples or col.endswith("_id"):
            return "ID"
    if _name_hits(col, _ID_NAME_HINTS) and (is_numeric_dtype or all_numeric_samples):
        return "ID"

    if is_numeric_dtype or all_numeric_samples:
        return "NUMERIC"

    # ENUM — low distinct count, non-numeric
    if str_samples and len(distinct) <= 50 and len(distinct) < max(2, len(str_samples)):
        # Treat as ENUM if average length is short-ish
        avg_len = sum(len(s) for s in str_samples) / len(str_samples)
        if avg_len <= 32:
            return "ENUM"

    # English text fallback (mostly ASCII letters)
    if str_samples and _any(str_samples, lambda s: bool(re.search(r"[A-Za-z]", s))):
        # Long free text vs short label
        avg_len = sum(len(s) for s in str_samples) / max(1, len(str_samples))
        if avg_len > 40:
            return "FREE-TEXT"
        return "LANG-EN"

    return "TEXT"
