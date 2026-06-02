"""
Zero-LLM Entity Linker
======================

Regex + dictionary based entity + triple extraction. Run at write-time
BEFORE invoking LLM-based extraction. LLM only fires when fast path fails
(``should_fallback_to_llm()`` returns True) — dense unstructured text.

Goal: cut KG extraction LLM cost by ~80%.

Public API:
    extract_entities(text, project_slug=None) -> list[dict]
    extract_triples_fast(text, project_slug) -> list[dict]
    should_fallback_to_llm(text, fast_entities, fast_triples) -> bool

Each entity dict:
    {type, value, span: [start, end], confidence, source}
        source in {"regex", "dict", "llm_fallback"}

Each triple dict:
    {subject, predicate, object, confidence, source}
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from sqlalchemy import NullPool, create_engine, text

from db import db_url

log = logging.getLogger(__name__)

_engine = create_engine(db_url, poolclass=NullPool)

_DATA_DIR = Path(__file__).parent / "data"


# ═══════════════════════════════════════════════════════════════════════════════
# Regex patterns
# ═══════════════════════════════════════════════════════════════════════════════

# Order matters — more specific patterns first to avoid double-matching.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("url", re.compile(r"https?://[^\s<>\"']+|www\.[A-Za-z0-9.\-]+\.[A-Za-z]{2,}[^\s<>\"']*")),
    # E.164 first then US fallback.
    ("phone_e164", re.compile(r"\+\d{1,3}[\s\-]?\d{1,4}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b")),
    ("phone_us", re.compile(r"\b(?:\(\d{3}\)\s?|\d{3}[\s\-.])\d{3}[\s\-.]\d{4}\b")),
    # Dates: ISO, US, EU, written month
    ("date_iso", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("date_slash", re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")),
    ("date_dash", re.compile(r"\b\d{1,2}-\d{1,2}-\d{2,4}\b")),
    ("date_written", re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}\b",
        re.IGNORECASE,
    )),
    # Currency: $1,234.56, €100, ¥50000, £75.5, INR 500, USD 1000
    ("currency", re.compile(
        r"(?:[\$€¥£₹]|USD|EUR|GBP|JPY|INR|CNY|CAD|AUD)\s?\d[\d,]*(?:\.\d+)?(?:[kKmMbB])?\b"
        r"|\b\d[\d,]*(?:\.\d+)?\s?(?:USD|EUR|GBP|JPY|INR|CNY|CAD|AUD|dollars?|euros?|yen|pounds?|rupees?)\b",
        re.IGNORECASE,
    )),
    ("percentage", re.compile(r"\b\d+(?:\.\d+)?\s?%")),
    # Store IDs
    ("store_id", re.compile(r"\b(?:S\d{3,}|STORE[-_]?\d+|ST[-_]?\d{3,})\b", re.IGNORECASE)),
    # Order IDs — before SKU to avoid SKU swallowing #123456
    ("order_id", re.compile(r"\b(?:ORD[-_]?\d+|ORDER[-_]?\d+|#\d{6,})\b", re.IGNORECASE)),
    # SKUs
    ("sku", re.compile(r"\b(?:SKU[-_]?\d+|[A-Z]{2,4}[-_]\d{4,})\b")),
]

# Org suffixes
_ORG_SUFFIXES = (
    "Inc", "Inc.", "LLC", "L.L.C.", "Ltd", "Ltd.", "Limited",
    "Corp", "Corp.", "Corporation", "Co", "Co.", "Company",
    "GmbH", "AG", "PLC", "plc", "S.A.", "SA", "BV", "NV", "Pty",
)
_ORG_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9&'\-]*(?:\s+[A-Z][A-Za-z0-9&'\-]*){0,4})\s+"
    r"(" + "|".join(re.escape(s) for s in _ORG_SUFFIXES) + r")\b"
)

# Person names: 2+ Title Case words (validated against blocklist below)
_PERSON_RE = re.compile(
    r"\b([A-Z][a-z]{1,15}(?:\s+(?:de|von|van|del|la|le|du|der)\s+)?(?:\s+[A-Z][a-z]{1,20}){1,3})\b"
)

# Blocklist: common Title Case phrases that aren't people
_PERSON_BLOCKLIST: set[str] = {
    "United States", "United Kingdom", "New York", "Los Angeles", "San Francisco",
    "North America", "South America", "Latin America", "Middle East",
    "Wall Street", "Main Street", "Silicon Valley", "Cape Town",
    "World War", "Cold War", "Civil War",
    "Black Friday", "Cyber Monday", "Good Friday", "Easter Sunday",
    "Microsoft Office", "Google Drive", "Apple Pay", "Google Pay",
    "Knowledge Graph", "Data Science", "Machine Learning", "Artificial Intelligence",
    "Customer Service", "Human Resources", "Sales Team", "Marketing Team",
    "Project Manager", "Product Manager", "Software Engineer", "Data Engineer",
    "Chief Executive", "Chief Financial", "Vice President", "Senior Vice",
    "Board Meeting", "Annual Report", "Quarterly Report", "Monthly Report",
    "Last Quarter", "Last Year", "This Year", "Next Year", "Last Month", "Next Month",
    "Best Practice", "Best Practices", "Key Performance", "Performance Indicator",
    "First Quarter", "Second Quarter", "Third Quarter", "Fourth Quarter",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Triple verb patterns (subject VERB object)
# ═══════════════════════════════════════════════════════════════════════════════

# Each: (predicate, regex). The regex MUST have two capture groups: subject, object.
# Subject/object captured as Title Case noun phrases (1-5 words) — generous,
# downstream Brain/KG dedupe handles aliases.
_NP = r"([A-Z][A-Za-z0-9&'\-]*(?:\s+[A-Za-z0-9&'\-]+){0,4})"

_VERB_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("works_at", re.compile(rf"{_NP}\s+works?\s+(?:at|for)\s+{_NP}")),
    ("works_at", re.compile(rf"{_NP}\s+(?:is|was)\s+employed\s+by\s+{_NP}")),
    ("founded", re.compile(rf"{_NP}\s+founded\s+{_NP}")),
    ("founded", re.compile(rf"{_NP}\s+co-?founded\s+{_NP}")),
    ("ceo_of", re.compile(rf"{_NP}\s+(?:is|was)\s+(?:the\s+)?CEO\s+of\s+{_NP}")),
    ("led_by", re.compile(rf"{_NP}\s+(?:is\s+)?led\s+by\s+{_NP}")),
    ("manages", re.compile(rf"{_NP}\s+manages\s+{_NP}")),
    ("reports_to", re.compile(rf"{_NP}\s+reports\s+to\s+{_NP}")),
    ("invested_in", re.compile(rf"{_NP}\s+invested\s+(?:in|into)\s+{_NP}")),
    ("acquired", re.compile(rf"{_NP}\s+acquired\s+{_NP}")),
    ("merged_with", re.compile(rf"{_NP}\s+merged\s+with\s+{_NP}")),
    ("partnered_with", re.compile(rf"{_NP}\s+partnered\s+with\s+{_NP}")),
    ("located_in", re.compile(rf"{_NP}\s+(?:is\s+)?(?:located|based|headquartered)\s+in\s+{_NP}")),
    ("owns", re.compile(rf"{_NP}\s+owns\s+{_NP}")),
    ("subsidiary_of", re.compile(rf"{_NP}\s+(?:is\s+)?(?:a\s+)?subsidiary\s+of\s+{_NP}")),
    ("part_of", re.compile(rf"{_NP}\s+is\s+part\s+of\s+{_NP}")),
    ("supplies", re.compile(rf"{_NP}\s+supplies\s+{_NP}")),
    ("uses", re.compile(rf"{_NP}\s+uses\s+{_NP}")),
    ("produces", re.compile(rf"{_NP}\s+(?:produces|makes|manufactures)\s+{_NP}")),
    ("sells", re.compile(rf"{_NP}\s+sells\s+{_NP}")),
    ("competes_with", re.compile(rf"{_NP}\s+competes\s+with\s+{_NP}")),
    ("caused", re.compile(rf"{_NP}\s+caused\s+{_NP}")),
    ("led_to", re.compile(rf"{_NP}\s+led\s+to\s+{_NP}")),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Dictionary loaders
# ═══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_drug_names() -> set[str]:
    fp = _DATA_DIR / "drug_names.txt"
    if not fp.exists():
        return set()
    try:
        return {
            line.strip().lower()
            for line in fp.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    except Exception as exc:
        log.warning("entity_linker: drug_names load failed: %s", exc)
        return set()


@lru_cache(maxsize=128)
def _load_project_entities(project_slug: str) -> frozenset[str]:
    """Load distinct known entity strings (subjects + objects) for a project.

    Cached per slug. Cache reset is best-effort via lru_cache eviction; for
    long-running processes the new entities surface on next slug rotation.
    """
    if not project_slug:
        return frozenset()
    try:
        # Try both schemas — table location varies by install.
        for tbl in ("dash.dash_knowledge_triples", "public.dash_knowledge_triples"):
            try:
                with _engine.connect() as conn:
                    rows = conn.execute(text(
                        f"SELECT DISTINCT subject FROM {tbl} "
                        f"WHERE project_slug = :s "
                        f"UNION SELECT DISTINCT object FROM {tbl} "
                        f"WHERE project_slug = :s LIMIT 5000"
                    ), {"s": project_slug}).fetchall()
                vals = {str(r[0]).strip() for r in rows if r[0]}
                # Drop ultra-short / pure-number tokens (low entity value).
                vals = {v for v in vals if len(v) >= 3 and not v.replace(".", "").replace(",", "").isdigit()}
                return frozenset(vals)
            except Exception:
                continue
    except Exception as exc:
        log.debug("entity_linker: project entity load skipped: %s", exc)
    return frozenset()


# ═══════════════════════════════════════════════════════════════════════════════
# Entity extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _is_blocklisted_person(name: str) -> bool:
    if name in _PERSON_BLOCKLIST:
        return True
    # Single-word "name" (shouldn't match _PERSON_RE but defensive)
    if len(name.split()) < 2:
        return True
    return False


def _dedupe_spans(entities: list[dict]) -> list[dict]:
    """Drop entities whose span is fully contained in a higher-confidence one."""
    if not entities:
        return entities
    entities = sorted(entities, key=lambda e: (-e["confidence"], e["span"][0]))
    kept: list[dict] = []
    used_ranges: list[tuple[int, int]] = []
    for ent in entities:
        s, e = ent["span"]
        overlaps = False
        for us, ue in used_ranges:
            # If this span is fully inside an existing one → drop
            if s >= us and e <= ue:
                overlaps = True
                break
            # If fully contains an existing — keep larger (already kept), skip
            if us >= s and ue <= e and (us, ue) != (s, e):
                overlaps = True
                break
        if not overlaps:
            kept.append(ent)
            used_ranges.append((s, e))
    # Restore positional order for stable downstream consumption.
    kept.sort(key=lambda e: e["span"][0])
    return kept


def extract_entities(text_in: str, project_slug: str | None = None) -> list[dict]:
    """Extract entities from text using regex + dictionary lookup.

    Returns list of ``{type, value, span: [start, end], confidence, source}``.
    """
    if not text_in or not isinstance(text_in, str):
        return []

    out: list[dict] = []

    # 1. Regex patterns
    for etype, pat in _PATTERNS:
        for m in pat.finditer(text_in):
            out.append({
                "type": etype,
                "value": m.group(0).strip(),
                "span": [m.start(), m.end()],
                "confidence": 0.95,
                "source": "regex",
            })

    # 2. Organizations
    for m in _ORG_RE.finditer(text_in):
        out.append({
            "type": "org",
            "value": m.group(0).strip(),
            "span": [m.start(), m.end()],
            "confidence": 0.9,
            "source": "regex",
        })

    # 3. People (filtered by blocklist)
    for m in _PERSON_RE.finditer(text_in):
        name = m.group(1).strip()
        if _is_blocklisted_person(name):
            continue
        out.append({
            "type": "person",
            "value": name,
            "span": [m.start(1), m.end(1)],
            "confidence": 0.7,
            "source": "regex",
        })

    # 4. Drug names (dictionary lookup, case-insensitive whole-word)
    drugs = _load_drug_names()
    if drugs:
        lower = text_in.lower()
        for drug in drugs:
            start = 0
            while True:
                idx = lower.find(drug, start)
                if idx < 0:
                    break
                # Word boundary check
                before_ok = idx == 0 or not lower[idx - 1].isalnum()
                end = idx + len(drug)
                after_ok = end >= len(lower) or not lower[end].isalnum()
                if before_ok and after_ok:
                    out.append({
                        "type": "drug",
                        "value": text_in[idx:end],
                        "span": [idx, end],
                        "confidence": 0.95,
                        "source": "dict",
                    })
                start = end

    # 5. Per-project known entity dictionary
    if project_slug:
        known = _load_project_entities(project_slug)
        if known:
            lower = text_in.lower()
            for ent in known:
                el = ent.lower()
                if len(el) < 3:
                    continue
                start = 0
                while True:
                    idx = lower.find(el, start)
                    if idx < 0:
                        break
                    before_ok = idx == 0 or not lower[idx - 1].isalnum()
                    end = idx + len(el)
                    after_ok = end >= len(lower) or not lower[end].isalnum()
                    if before_ok and after_ok:
                        out.append({
                            "type": "project_entity",
                            "value": text_in[idx:end],
                            "span": [idx, end],
                            "confidence": 0.9,
                            "source": "dict",
                        })
                    start = end

    return _dedupe_spans(out)


# ═══════════════════════════════════════════════════════════════════════════════
# Triple extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _co_occurrence_triples(entities: list[dict], max_chars: int = 50) -> list[dict]:
    """Build low-confidence ``related_to`` triples for entities within N chars."""
    triples: list[dict] = []
    # Limit pair generation to keep cost bounded.
    notable = [
        e for e in entities
        if e["type"] in ("person", "org", "drug", "project_entity")
    ]
    notable.sort(key=lambda e: e["span"][0])
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(notable):
        for b in notable[i + 1:i + 8]:
            if b["span"][0] - a["span"][1] > max_chars:
                break
            if a["value"].lower() == b["value"].lower():
                continue
            key = (a["value"].lower(), b["value"].lower())
            if key in seen:
                continue
            seen.add(key)
            triples.append({
                "subject": a["value"],
                "predicate": "related_to",
                "object": b["value"],
                "confidence": 0.4,
                "source": "regex_cooccurrence",
            })
    return triples


def extract_triples_fast(text_in: str, project_slug: str | None = None) -> list[dict]:
    """Extract SPO triples via verb patterns + co-occurrence (no LLM)."""
    if not text_in or not isinstance(text_in, str):
        return []

    triples: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for predicate, pat in _VERB_PATTERNS:
        for m in pat.finditer(text_in):
            subj = m.group(1).strip()
            obj = m.group(2).strip()
            if len(subj) < 2 or len(obj) < 2:
                continue
            key = (subj.lower(), predicate, obj.lower())
            if key in seen:
                continue
            seen.add(key)
            triples.append({
                "subject": subj,
                "predicate": predicate,
                "object": obj,
                "confidence": 0.85,
                "source": "regex",
            })

    # Co-occurrence triples (low confidence)
    entities = extract_entities(text_in, project_slug=project_slug)
    for t in _co_occurrence_triples(entities):
        key = (t["subject"].lower(), t["predicate"], t["object"].lower())
        if key in seen:
            continue
        seen.add(key)
        triples.append(t)

    return triples


# ═══════════════════════════════════════════════════════════════════════════════
# Fallback decision
# ═══════════════════════════════════════════════════════════════════════════════

def should_fallback_to_llm(
    text_in: str,
    fast_entities: Iterable[dict] | None = None,
    fast_triples: Iterable[dict] | None = None,
) -> bool:
    """Return True when the fast extractor signal is too thin.

    Heuristic: dense text (>200 chars) with <2 high-confidence entities
    strongly suggests unstructured prose that needs LLM understanding.
    """
    if not text_in or len(text_in) <= 200:
        return False
    ents = list(fast_entities or [])
    high_conf = [e for e in ents if e.get("confidence", 0) >= 0.7]
    if len(high_conf) >= 2:
        return False
    # Also bail out to LLM if we got zero triples on a long passage.
    triples = list(fast_triples or [])
    if len(triples) == 0:
        return True
    return len(high_conf) < 2


__all__ = [
    "extract_entities",
    "extract_triples_fast",
    "should_fallback_to_llm",
]
