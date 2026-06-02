"""ML-based NER for PII detection with regex fallback.

Tries spaCy `en_core_web_sm` for PERSON/ORG/LOC/DATE. Always runs regex layer
for structured PII (email, phone, SSN, credit card via Luhn, IBAN, MRN, IP,
passport, DOB, addresses). Returns unified detection list + scrub helper.

Usage:
    from dash.learning.pii_ner import detect_pii, scrub_pii, is_safe

    findings = detect_pii("Email me at john@x.com or call +1-415-555-1212")
    safe_text = scrub_pii(text, mode='mask')   # mask | redact | hash | token
    ok = is_safe(text, allowlist=['acme corp'])

Each finding: {type, value, span: (start, end), confidence, source}
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# spaCy lazy load
# ----------------------------------------------------------------------------
_NLP = None
_NLP_TRIED = False


def _get_nlp():
    global _NLP, _NLP_TRIED
    if _NLP_TRIED:
        return _NLP
    _NLP_TRIED = True
    try:
        import spacy  # type: ignore
        try:
            _NLP = spacy.load("en_core_web_sm")
            logger.info("pii_ner: loaded spaCy en_core_web_sm")
        except OSError:
            logger.info("pii_ner: spaCy installed but en_core_web_sm not downloaded; regex-only mode")
            _NLP = None
    except ImportError:
        logger.info("pii_ner: spaCy not installed; regex-only mode")
        _NLP = None
    return _NLP


# ----------------------------------------------------------------------------
# Regex catalog (enhanced beyond promotion._PII_BLOCKERS)
# ----------------------------------------------------------------------------
_REGEX_PATTERNS: list[tuple[str, re.Pattern, float]] = [
    ("EMAIL",   re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), 0.99),
    ("PHONE",   re.compile(r"\+?\d{1,3}[\s\-.]?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}"), 0.85),
    ("SSN",     re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0.97),
    ("CC_RAW",  re.compile(r"\b(?:\d[ -]*?){13,19}\b"), 0.60),  # Luhn-validated below
    ("IBAN",    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), 0.92),
    ("PASSPORT", re.compile(r"\b[A-Z]{1,2}\d{6,9}\b"), 0.55),
    ("MRN",     re.compile(r"\b(?:MRN|mrn|Medical\s+Record(?:\s+#)?)[:\s#]*([A-Z0-9-]{4,15})\b"), 0.90),
    ("IP",      re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"), 0.95),
    ("IPV6",    re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b"), 0.90),
    ("DATE_OF_BIRTH", re.compile(r"\b(?:DOB|dob|D\.O\.B\.?|born)[:\s]*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b"), 0.90),
    ("DATE",    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), 0.50),
    ("ADDRESS", re.compile(r"\b\d{1,6}\s+[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Way|Ct|Court|Pl|Place)\b\.?", re.IGNORECASE), 0.75),
    ("ZIP_US",  re.compile(r"\b\d{5}(?:-\d{4})?\b"), 0.40),
    ("PERSON_LIKELY", re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"), 0.50),
]


def _luhn_ok(num: str) -> bool:
    digits = [int(c) for c in num if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# spaCy entity label → our normalized type
_SPACY_MAP = {
    "PERSON":  "PERSON",
    "ORG":     "ORG",
    "GPE":     "LOC",
    "LOC":     "LOC",
    "FAC":     "LOC",
    "DATE":    "DATE",
    "TIME":    "DATE",
    "MONEY":   "MONEY",
    "NORP":    "ORG",
}


def detect_pii(text: str, lang: str = "en") -> list[dict[str, Any]]:
    """Return list of PII findings with type, value, span, confidence, source."""
    if not text:
        return []
    findings: list[dict[str, Any]] = []
    seen_spans: set[tuple[int, int]] = set()

    # --- NER pass ---------------------------------------------------------
    nlp = _get_nlp()
    if nlp is not None:
        try:
            doc = nlp(text[:100_000])  # cap to 100K chars
            for ent in doc.ents:
                t = _SPACY_MAP.get(ent.label_)
                if not t:
                    continue
                span = (ent.start_char, ent.end_char)
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                findings.append({
                    "type": t,
                    "value": ent.text,
                    "span": span,
                    "confidence": 0.88,
                    "source": "ner",
                })
        except Exception as e:
            logger.warning(f"pii_ner: spaCy failed: {e}")

    # --- Regex pass -------------------------------------------------------
    for label, pat, conf in _REGEX_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            val = m.group(0)
            # Special: validate credit card via Luhn
            if label == "CC_RAW":
                if not _luhn_ok(val):
                    continue
                label_emit = "CC"
                conf_emit = 0.95
            else:
                label_emit = label
                conf_emit = conf
            # Dedup vs NER spans (overlapping)
            overlap = any(not (span[1] <= s[0] or span[0] >= s[1]) for s in seen_spans)
            if overlap and label_emit in ("PERSON_LIKELY", "DATE"):
                continue
            seen_spans.add(span)
            findings.append({
                "type": label_emit,
                "value": val,
                "span": span,
                "confidence": conf_emit,
                "source": "regex",
            })

    findings.sort(key=lambda d: d["span"][0])
    return findings


# ----------------------------------------------------------------------------
# Scrubbing
# ----------------------------------------------------------------------------
def scrub_pii(text: str, mode: str = "mask", findings: list[dict] | None = None) -> str:
    """Scrub PII in text. mode: mask|redact|hash|token."""
    if not text:
        return text
    if findings is None:
        findings = detect_pii(text)
    if not findings:
        return text
    # Apply right-to-left so spans stay valid
    counters: dict[str, int] = {}
    out = text
    for f in sorted(findings, key=lambda d: d["span"][0], reverse=True):
        s, e = f["span"]
        t = f["type"]
        val = f["value"]
        if mode == "redact":
            repl = "[REDACTED]"
        elif mode == "hash":
            repl = hashlib.sha256(val.encode("utf-8")).hexdigest()[:8]
        elif mode == "token":
            counters[t] = counters.get(t, 0) + 1
            repl = f"{t}_{counters[t]}"
        else:  # mask
            repl = "*" * max(3, min(len(val), 8))
        out = out[:s] + repl + out[e:]
    return out


def is_safe(text: str, allowlist: list[str] | None = None) -> bool:
    """Return True if no PII detected (after removing allowlisted strings)."""
    if not text:
        return True
    scan = text
    if allowlist:
        for a in allowlist:
            if a:
                scan = re.sub(re.escape(a), "", scan, flags=re.IGNORECASE)
    return len(detect_pii(scan)) == 0


# ----------------------------------------------------------------------------
# Non-Latin script detection (Issue #23 — warn admins that regex-only PII
# scrub will miss names in Myanmar/Chinese/Arabic/Devanagari/etc. text).
# ----------------------------------------------------------------------------
# Unicode block ranges (start, end, name) — kept small & cheap. We only care
# whether *any* char from a non-Latin block appears.
_SCRIPT_BLOCKS: list[tuple[int, int, str]] = [
    (0x1000, 0x109F, "myanmar"),
    (0xAA60, 0xAA7F, "myanmar"),        # Myanmar Extended-A
    (0xA9E0, 0xA9FF, "myanmar"),        # Myanmar Extended-B
    (0x4E00, 0x9FFF, "chinese"),        # CJK Unified Ideographs
    (0x3400, 0x4DBF, "chinese"),        # CJK Extension A
    (0x3040, 0x309F, "japanese"),       # Hiragana
    (0x30A0, 0x30FF, "japanese"),       # Katakana
    (0xAC00, 0xD7AF, "korean"),         # Hangul Syllables
    (0x0600, 0x06FF, "arabic"),
    (0x0750, 0x077F, "arabic"),         # Arabic Supplement
    (0x0900, 0x097F, "devanagari"),
    (0x0E00, 0x0E7F, "thai"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0370, 0x03FF, "greek"),
]


def detect_script(text: str) -> str | None:
    """Return the first non-Latin script name detected in `text`, else None.

    Scans up to the first 5000 chars (cheap) and returns the script of the
    earliest matching codepoint. Latin (incl. Latin-1 Supplement / Extended)
    and ASCII / common punctuation never trigger.
    """
    if not text:
        return None
    sample = text[:5000]
    for ch in sample:
        cp = ord(ch)
        if cp < 0x0250:
            # Basic Latin + Latin-1 Supplement + Latin Extended-A — safe
            continue
        for start, end, name in _SCRIPT_BLOCKS:
            if start <= cp <= end:
                return name
    return None


# Throttle: 1 warning per (project, script) per day. In-memory cache — fine
# for single-worker scope; multi-worker will dedupe at the dash_notifications
# layer (insert is idempotent enough that one extra warn/day is acceptable).
_WARN_CACHE: dict[tuple[str, str, str], float] = {}
_WARN_TTL_SECONDS = 24 * 3600


def warn_non_latin_if_needed(
    text: str,
    project_slug: str | None = None,
    *,
    engine=None,
) -> dict | None:
    """If `text` contains non-Latin script AND spaCy is unavailable, emit a
    WARNING + insert a row into `dash_notifications` (throttled 1/project/day).

    Returns the warning dict (or None if no warning was emitted). Safe to call
    in hot paths — fail-soft on any DB issue, never raises.
    """
    if not text:
        return None
    script = detect_script(text)
    if not script:
        return None
    # If spaCy NER is loaded we have multilingual fallback (still imperfect but
    # better than regex-only). Only warn when we're regex-only.
    if _get_nlp() is not None:
        return None

    import time as _time
    today = _time.strftime("%Y-%m-%d")
    key = (project_slug or "__global__", script, today)
    now = _time.time()
    last = _WARN_CACHE.get(key)
    if last is not None and (now - last) < _WARN_TTL_SECONDS:
        return None
    _WARN_CACHE[key] = now

    msg = (
        f"⚠ Non-Latin text detected ({script}) — PII scan may miss names. "
        f"Install spaCy multilingual model for full coverage "
        f"(`python -m spacy download xx_ent_wiki_sm`)."
    )
    logger.warning(f"pii_ner: {msg} project={project_slug}")

    # Best-effort write to dash_notifications. Caller may pass an engine to
    # avoid an import cycle; otherwise try the standard session helper.
    try:
        if engine is None:
            try:
                from db.session import get_sql_engine
                engine = get_sql_engine()
            except Exception:
                engine = None
        if engine is not None:
            from sqlalchemy import text as _sa_text
            with engine.begin() as conn:
                conn.execute(
                    _sa_text(
                        "INSERT INTO public.dash_notifications "
                        "(user_id, project_slug, kind, severity, title, message, created_at) "
                        "VALUES (NULL, :slug, 'pii_script_warning', 'warn', :title, :msg, now())"
                    ),
                    {
                        "slug": project_slug,
                        "title": f"Non-Latin script ({script}) detected",
                        "msg": msg,
                    },
                )
    except Exception as e:
        logger.debug(f"pii_ner: dash_notifications insert failed (non-fatal): {e}")

    return {"script": script, "message": msg, "project_slug": project_slug}


__all__ = [
    "detect_pii",
    "scrub_pii",
    "is_safe",
    "detect_script",
    "warn_non_latin_if_needed",
]
