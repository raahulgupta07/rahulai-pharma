"""Multi-signal column classifier — fuses 5 detectors into rich semantic
classification per column.

Operates exclusively on existing on-disk training artefacts:

    knowledge/{slug}/source_{id}/profile/{table}.json
    knowledge/{slug}/source_{id}/dimensions/{table}.json
    knowledge/{slug}/source_{id}/catalog.json

and emits one new artefact:

    knowledge/{slug}/source_{id}/column_classification.json

The five detectors:

    1. ``StatisticalFingerprint`` — pure-stats heuristics from profile JSON.
    2. ``RegexClassifier``        — pattern matching on top sample values.
    3. ``NameClassifier``         — vocabulary lookup on column name.
    4. ``LLMTyper``               — batched LLM call (optional; skipped if
                                    no callable supplied).
    5. ``EmbeddingMatcher``       — cosine similarity vs. Brain seed
                                    columns (optional).

Each detector emits one or more :class:`Signal` objects. The fusion step
performs a confidence-weighted vote over roles, with PII signals winning
deterministically when their confidence exceeds 0.9.

The public entrypoint is :func:`classify_source`. It catches per-table
exceptions and never raises — failures are logged and the table is
skipped so that broken metadata for one table never poisons the rest of
the pipeline.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from dash.providers.column_priors import (
    REGEX_PATTERNS,
    NAME_VOCABULARY,
    BRAIN_SEED_COLUMNS,
    match_name,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Signal:
    """One detector's per-column verdict."""

    detector: str  # 'stats' | 'regex' | 'name' | 'llm' | 'embedding'
    role: str      # 'id' | 'measure' | 'dimension' | 'temporal' | 'pii' | 'attribute' | 'unknown'
    semantic: str  # specific tag like 'email', 'currency_usd', 'geo_region'
    confidence: float  # 0..1
    evidence: dict = field(default_factory=dict)


@dataclass
class ColumnClassification:
    """Fused multi-signal classification for one column."""

    table: str
    col: str
    type: str  # raw SQL type
    role: str  # fused
    semantic: str  # fused
    pii: bool
    pii_class: Optional[str] = None  # 'direct' | 'quasi' | None
    cardinality: str = "unknown"  # 'unique' | 'near_unique' | 'low' | 'high'
    ndv_estimated: int = 0
    null_pct: float = 0.0
    is_dimension: bool = False
    is_measure: bool = False
    is_id: bool = False
    is_temporal: bool = False
    fk_candidate_for: Optional[str] = None
    value_distribution: str = "unknown"  # 'normal'|'long_tail'|'bimodal'|'monotonic'|'unknown'
    masking_recommended: Optional[str] = None
    confidence_overall: float = 0.0
    signals: list[Signal] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Profile / sample helpers
# ---------------------------------------------------------------------------


_NUMERIC_TYPE_HINTS = (
    "int",
    "bigint",
    "smallint",
    "numeric",
    "decimal",
    "real",
    "double",
    "float",
    "money",
)
_TEMPORAL_TYPE_HINTS = ("date", "time", "timestamp", "datetime", "interval")
_TEXT_TYPE_HINTS = ("char", "text", "string", "varchar", "nvarchar", "clob")


def _is_numeric_type(col_type: str) -> bool:
    if not col_type:
        return False
    t = col_type.lower()
    return any(h in t for h in _NUMERIC_TYPE_HINTS)


def _is_temporal_type(col_type: str) -> bool:
    if not col_type:
        return False
    t = col_type.lower()
    return any(h in t for h in _TEMPORAL_TYPE_HINTS)


def _is_text_type(col_type: str) -> bool:
    if not col_type:
        return False
    t = col_type.lower()
    return any(h in t for h in _TEXT_TYPE_HINTS)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _extract_col_profile(profile: dict, col: str) -> dict:
    """Return the per-column dict from a profile JSON, accepting common shapes."""
    if not isinstance(profile, dict):
        return {}
    cols = profile.get("columns")
    if isinstance(cols, dict) and col in cols:
        return cols[col] or {}
    if isinstance(cols, list):
        for entry in cols:
            if isinstance(entry, dict) and (entry.get("name") == col or entry.get("column") == col):
                return entry
    if col in profile and isinstance(profile[col], dict):
        return profile[col]
    return {}


def _extract_samples(col_profile: dict, dim_entry: Any) -> list[str]:
    """Extract up to 50 sample string values from profile + dimension catalog."""
    out: list[str] = []
    top_values = col_profile.get("top_values") or col_profile.get("top") or []
    if isinstance(top_values, list):
        for v in top_values:
            if isinstance(v, dict):
                val = v.get("value", v.get("v"))
            else:
                val = v
            if val is not None:
                out.append(str(val))
    if isinstance(dim_entry, dict):
        vals = dim_entry.get("values") or dim_entry.get("top_values") or []
        if isinstance(vals, list):
            for v in vals:
                if isinstance(v, dict):
                    val = v.get("value", v.get("v"))
                else:
                    val = v
                if val is not None:
                    out.append(str(val))
    elif isinstance(dim_entry, list):
        for v in dim_entry:
            if isinstance(v, dict):
                val = v.get("value", v.get("v"))
            else:
                val = v
            if val is not None:
                out.append(str(val))
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s in seen:
            continue
        seen.add(s)
        deduped.append(s)
        if len(deduped) >= 50:
            break
    return deduped


def _extract_dim_entry(dim_catalog: dict, col: str) -> Any:
    if not isinstance(dim_catalog, dict):
        return None
    cols = dim_catalog.get("columns")
    if isinstance(cols, dict) and col in cols:
        return cols[col]
    if col in dim_catalog:
        return dim_catalog[col]
    return None


# ---------------------------------------------------------------------------
# Detector 1: Statistical fingerprint
# ---------------------------------------------------------------------------


class StatisticalFingerprint:
    """Pure-stats detector. Reads the per-column profile dict and emits a
    role/semantic guess from cardinality + numeric/temporal hints. ~10 ms."""

    def classify(self, col_name: str, col_type: str, col_profile: dict) -> Signal:
        evidence: dict = {}
        row_count = _safe_int(col_profile.get("count") or col_profile.get("row_count"))
        nulls = _safe_int(col_profile.get("nulls") or col_profile.get("null_count"))
        ndv = _safe_int(col_profile.get("ndv") or col_profile.get("distinct"))
        non_null = max(row_count - nulls, 0)
        ratio = (ndv / non_null) if non_null > 0 else 0.0
        null_pct = (nulls / row_count) if row_count > 0 else 0.0

        evidence["ndv"] = ndv
        evidence["row_count"] = row_count
        evidence["null_pct"] = round(null_pct, 4)
        evidence["ndv_ratio"] = round(ratio, 4)

        # Cardinality bucket
        if ratio >= 0.98 and ndv >= 50:
            cardinality = "unique"
        elif ratio >= 0.85:
            cardinality = "near_unique"
        elif ndv <= 50:
            cardinality = "low"
        else:
            cardinality = "high"
        evidence["cardinality"] = cardinality

        # Temporal types win immediately
        if _is_temporal_type(col_type):
            return Signal(
                detector="stats",
                role="temporal",
                semantic="datetime" if "time" in col_type.lower() else "date",
                confidence=0.85,
                evidence=evidence,
            )

        # Numeric: measure unless it's a unique/near-unique integer (likely an id)
        if _is_numeric_type(col_type):
            if cardinality in ("unique", "near_unique") and "int" in col_type.lower():
                return Signal(
                    detector="stats",
                    role="id",
                    semantic="numeric_id",
                    confidence=0.75,
                    evidence=evidence,
                )
            avg = _safe_float(col_profile.get("avg") or col_profile.get("mean"))
            stddev = _safe_float(col_profile.get("stddev"))
            evidence["avg"] = avg
            evidence["stddev"] = stddev
            distribution = "unknown"
            mn = _safe_float(col_profile.get("min"))
            mx = _safe_float(col_profile.get("max"))
            if 0 <= mn and mx <= 1 and stddev > 0:
                distribution = "normal"
            elif stddev > 0 and avg != 0 and stddev > 2 * abs(avg):
                distribution = "long_tail"
            evidence["distribution"] = distribution
            return Signal(
                detector="stats",
                role="measure",
                semantic="numeric_measure",
                confidence=0.70,
                evidence=evidence,
            )

        # Text-ish: low cardinality → dimension, very high → free text attribute
        if cardinality in ("low",):
            return Signal(
                detector="stats",
                role="dimension",
                semantic="categorical",
                confidence=0.75,
                evidence=evidence,
            )
        if cardinality == "unique":
            return Signal(
                detector="stats",
                role="id",
                semantic="text_id",
                confidence=0.65,
                evidence=evidence,
            )
        if cardinality == "near_unique":
            return Signal(
                detector="stats",
                role="attribute",
                semantic="free_text",
                confidence=0.55,
                evidence=evidence,
            )
        return Signal(
            detector="stats",
            role="dimension",
            semantic="categorical",
            confidence=0.50,
            evidence=evidence,
        )


# ---------------------------------------------------------------------------
# Detector 2: Regex classifier
# ---------------------------------------------------------------------------


class RegexClassifier:
    """Tries each pattern in REGEX_PATTERNS against sample values."""

    def __init__(self) -> None:
        self._compiled: list[tuple[re.Pattern[str], str, bool, float]] = []
        for pat, semantic, pii, conf in REGEX_PATTERNS:
            try:
                self._compiled.append((re.compile(pat), semantic, pii, conf))
            except re.error as exc:  # pragma: no cover - bad pattern
                logger.warning("Skipping invalid regex %r: %s", pat, exc)

    def classify(self, col_name: str, sample_values: list[str]) -> Signal:
        clean = [s for s in (sample_values or []) if s and isinstance(s, str)]
        if not clean:
            return Signal(detector="regex", role="unknown", semantic="", confidence=0.0)

        best: Optional[tuple[float, str, bool, float, int]] = None
        total = len(clean)
        for pattern, semantic, pii, base_conf in self._compiled:
            hits = sum(1 for s in clean if pattern.match(s))
            if hits == 0:
                continue
            hit_pct = hits / total
            if hit_pct < 0.6:
                continue
            score = hit_pct * base_conf
            if best is None or score > best[0]:
                best = (score, semantic, pii, base_conf, hits)

        if not best:
            return Signal(detector="regex", role="unknown", semantic="", confidence=0.0)

        score, semantic, is_pii, base_conf, hits = best
        role = "pii" if is_pii else _semantic_to_role(semantic)
        return Signal(
            detector="regex",
            role=role,
            semantic=semantic,
            confidence=min(score, base_conf),
            evidence={"hits": hits, "total": total, "hit_pct": round(hits / total, 3)},
        )


# ---------------------------------------------------------------------------
# Detector 3: Name classifier
# ---------------------------------------------------------------------------


class NameClassifier:
    """Vocabulary lookup on column name."""

    def classify(self, col_name: str) -> Signal:
        matches = match_name(col_name or "")
        if not matches:
            return Signal(detector="name", role="unknown", semantic="", confidence=0.0)
        # Pick highest-confidence match
        role, semantic, conf = max(matches, key=lambda m: m[2])
        return Signal(
            detector="name",
            role=role,
            semantic=semantic,
            confidence=conf,
            evidence={"matches": len(matches)},
        )


# ---------------------------------------------------------------------------
# Detector 4: LLM typer (optional)
# ---------------------------------------------------------------------------


class LLMTyper:
    """Batched LLM call. Caller decides whether to invoke by passing a
    callable; default ``None`` means this tier is skipped."""

    def __init__(self, llm_call_fn: Optional[Callable[..., Any]] = None) -> None:
        self.llm_call = llm_call_fn

    def classify_batch(
        self,
        table: str,
        cols_with_profiles: list[dict],
    ) -> list[Signal]:
        if self.llm_call is None or not cols_with_profiles:
            return []

        prompt = self._build_prompt(table, cols_with_profiles)
        try:
            raw = self.llm_call(prompt, task="extraction")
        except Exception as exc:  # pragma: no cover - depends on caller
            logger.warning("LLM typer failed for table %s: %s", table, exc)
            return []

        if not raw:
            return []
        parsed = self._parse_response(raw)
        signals: list[Signal] = []
        wanted = {c["name"] for c in cols_with_profiles}
        for entry in parsed:
            name = entry.get("col") or entry.get("name")
            if name not in wanted:
                continue
            role = entry.get("role") or "unknown"
            semantic = entry.get("semantic") or ""
            try:
                conf = float(entry.get("confidence", 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            signals.append(
                Signal(
                    detector="llm",
                    role=role,
                    semantic=semantic,
                    confidence=max(0.0, min(conf, 1.0)),
                    evidence={"col": name},
                )
            )
        return signals

    @staticmethod
    def _build_prompt(table: str, cols: list[dict]) -> str:
        lines = [
            "Classify each column. Reply with a JSON array of objects with keys:",
            "col, role, semantic, confidence.",
            "role must be one of: id, measure, dimension, temporal, pii, attribute, unknown.",
            f"Table: {table}",
            "Columns:",
        ]
        for c in cols:
            lines.append(
                f"- {c.get('name')} (type={c.get('type','')}, ndv={c.get('ndv','?')}, "
                f"sample={c.get('sample',[])[:5]})"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str) -> list[dict]:
        if not isinstance(raw, str):
            return []
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[: -3]
        # Try parsing the whole blob, then fall back to first JSON array slice
        try:
            obj = json.loads(text)
            if isinstance(obj, list):
                return [o for o in obj if isinstance(o, dict)]
            if isinstance(obj, dict) and isinstance(obj.get("columns"), list):
                return [o for o in obj["columns"] if isinstance(o, dict)]
        except json.JSONDecodeError:
            pass
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                if isinstance(obj, list):
                    return [o for o in obj if isinstance(o, dict)]
            except json.JSONDecodeError:
                return []
        return []


# ---------------------------------------------------------------------------
# Detector 5: Embedding matcher (optional)
# ---------------------------------------------------------------------------


class EmbeddingMatcher:
    """Cosine similarity of (col_name + samples) vs. Brain seed columns."""

    def __init__(
        self,
        embed_fn: Optional[Callable[[str], list[float]]] = None,
        brain_columns: Optional[list[dict]] = None,
    ) -> None:
        self.embed = embed_fn
        self.brain = brain_columns or BRAIN_SEED_COLUMNS
        self._brain_vecs: Optional[list[tuple[dict, list[float]]]] = None

    def _ensure_brain_vecs(self) -> list[tuple[dict, list[float]]]:
        if self._brain_vecs is not None:
            return self._brain_vecs
        out: list[tuple[dict, list[float]]] = []
        if self.embed is None:
            self._brain_vecs = out
            return out
        for entry in self.brain:
            text = entry.get("name", "") + " " + " ".join(entry.get("samples", []))
            try:
                vec = self.embed(text)
            except Exception as exc:  # pragma: no cover
                logger.warning("Brain embedding failed for %s: %s", entry.get("name"), exc)
                continue
            if vec:
                out.append((entry, list(vec)))
        self._brain_vecs = out
        return out

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def classify(self, col_name: str, sample_values: list[str]) -> Signal:
        if self.embed is None:
            return Signal(detector="embedding", role="unknown", semantic="", confidence=0.0)
        try:
            text = (col_name or "") + " " + " ".join((sample_values or [])[:5])
            qvec = self.embed(text)
        except Exception as exc:  # pragma: no cover
            logger.warning("Embedding failed for column %s: %s", col_name, exc)
            return Signal(detector="embedding", role="unknown", semantic="", confidence=0.0)
        if not qvec:
            return Signal(detector="embedding", role="unknown", semantic="", confidence=0.0)
        brain_vecs = self._ensure_brain_vecs()
        if not brain_vecs:
            return Signal(detector="embedding", role="unknown", semantic="", confidence=0.0)
        best_entry: Optional[dict] = None
        best_sim = 0.0
        for entry, bvec in brain_vecs:
            sim = self._cosine(list(qvec), bvec)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry
        if best_entry is None or best_sim < 0.5:
            return Signal(detector="embedding", role="unknown", semantic="", confidence=0.0)
        return Signal(
            detector="embedding",
            role=best_entry.get("role", "unknown"),
            semantic=best_entry.get("semantic", ""),
            confidence=float(best_sim),
            evidence={"matched": best_entry.get("name"), "similarity": round(best_sim, 4)},
        )


# ---------------------------------------------------------------------------
# Semantic → role mapping (regex output → role)
# ---------------------------------------------------------------------------


_REGEX_SEMANTIC_TO_ROLE: dict[str, str] = {
    "uuid": "id",
    "md5": "id",
    "sha1": "id",
    "sha256": "id",
    "opaque_token": "id",
    "url": "attribute",
    "url_ftp": "attribute",
    "domain": "attribute",
    "iso_date": "temporal",
    "iso_datetime": "temporal",
    "us_date": "temporal",
    "eu_date": "temporal",
    "time_of_day": "temporal",
    "epoch_seconds": "temporal",
    "epoch_millis": "temporal",
    "zip_us": "dimension",
    "postal_ca": "dimension",
    "postal_uk": "dimension",
    "postal_generic": "dimension",
    "lat_lng": "attribute",
    "latitude": "measure",
    "longitude": "measure",
    "iso_code3": "dimension",
    "iso_code2": "dimension",
    "iso_subdivision": "dimension",
    "isin": "id",
    "currency_usd_str": "measure",
    "currency_str": "measure",
    "integer_str": "measure",
    "decimal_str": "measure",
    "percentage_str": "measure",
    "boolean_str": "dimension",
    "vin": "id",
    "license_plate": "id",
    "file_path": "attribute",
    "filename": "attribute",
}


def _semantic_to_role(semantic: str) -> str:
    return _REGEX_SEMANTIC_TO_ROLE.get(semantic, "attribute")


# ---------------------------------------------------------------------------
# PII detection
# ---------------------------------------------------------------------------


_DIRECT_PII_SEMANTICS = {
    "email",
    "phone",
    "phone_us",
    "phone_intl",
    "ssn",
    "ssn_us",
    "credit_card",
    "credit_card_visa",
    "credit_card_mc",
    "credit_card_amex",
    "credit_card_disc",
    "iban",
    "passport",
    "tax_id",
    "tax_id_us",
    "secret",
    "cvv",
    "person_name",
    "given_name",
    "family_name",
    "date_of_birth",
    "postal_address",
    "street_address",
}

_QUASI_PII_SEMANTICS = {
    "ip_address",
    "ip_v4",
    "ip_v6",
    "mac_address",
    "device_id",
    "session_id",
    "zip_us",
    "postal_ca",
    "postal_uk",
    "postal_generic",
    "gender",
    "race",
    "ethnicity",
    "nationality",
    "religion",
    "drivers_license",
    "license_number",
}


def detect_pii(
    role: str,
    semantic: str,
    regex_hits: list[str],
) -> tuple[bool, Optional[str], Optional[str]]:
    """Return ``(is_pii, pii_class, masking_recommended)``.

    Direct PII (email, phone, ssn, credit_card, iban, passport) is masked
    via hash/redact. Quasi PII (zip+age+gender combos, IP) is generalised
    or k-anonymised.
    """
    semantic = (semantic or "").lower()
    hits = {h.lower() for h in regex_hits or []}

    if role == "pii" or semantic in _DIRECT_PII_SEMANTICS or hits & _DIRECT_PII_SEMANTICS:
        return True, "direct", "hash_or_redact"
    if semantic in _QUASI_PII_SEMANTICS or hits & _QUASI_PII_SEMANTICS:
        return True, "quasi", "generalize_or_kanonymize"
    return False, None, None


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def fuse_signals(signals: list[Signal]) -> tuple[str, str, float, dict]:
    """Confidence-weighted vote across signals.

    Returns ``(role, semantic, overall_confidence, evidence)``.

    Rules:
        * If any signal flags ``role='pii'`` with ``confidence >= 0.9``,
          PII wins outright.
        * Otherwise: weighted vote on role.
        * Within winning role: pick the semantic with the highest summed
          weight; fall back to the highest-confidence signal's semantic.
        * Overall confidence = average of agreeing signals' confidences.
    """
    if not signals:
        return "unknown", "", 0.0, {}

    pii_strong = [s for s in signals if s.role == "pii" and s.confidence >= 0.9]
    if pii_strong:
        s = max(pii_strong, key=lambda x: x.confidence)
        return (
            "pii",
            s.semantic or "pii",
            s.confidence,
            {"reason": "pii_override", "by": s.detector, "all_pii": [p.detector for p in pii_strong]},
        )

    role_score: dict[str, float] = {}
    for s in signals:
        if s.role == "unknown" or s.confidence <= 0:
            continue
        role_score[s.role] = role_score.get(s.role, 0.0) + s.confidence

    if not role_score:
        return "unknown", "", 0.0, {}

    winning_role = max(role_score.items(), key=lambda kv: kv[1])[0]

    sem_score: dict[str, float] = {}
    agreeing = [s for s in signals if s.role == winning_role]
    for s in agreeing:
        if not s.semantic:
            continue
        sem_score[s.semantic] = sem_score.get(s.semantic, 0.0) + s.confidence

    if sem_score:
        winning_semantic = max(sem_score.items(), key=lambda kv: kv[1])[0]
    else:
        winning_semantic = max(agreeing, key=lambda s: s.confidence).semantic or ""

    overall = sum(s.confidence for s in agreeing) / len(agreeing)
    evidence = {
        "role_scores": {k: round(v, 4) for k, v in role_score.items()},
        "semantic_scores": {k: round(v, 4) for k, v in sem_score.items()},
        "agreeing_detectors": [s.detector for s in agreeing],
    }
    return winning_role, winning_semantic, overall, evidence


# ---------------------------------------------------------------------------
# Per-table orchestrator
# ---------------------------------------------------------------------------


def _columns_from_catalog(catalog: dict, table_name: str) -> list[dict]:
    """Return a list of ``{name, type}`` dicts for a table from catalog."""
    out: list[dict] = []
    if not isinstance(catalog, dict):
        return out
    tables = catalog.get("tables") or []
    target: Optional[dict] = None
    for t in tables:
        if isinstance(t, dict):
            tn = t.get("name") or t.get("table_name")
            if tn == table_name:
                target = t
                break
    if target is None:
        return out
    cols = target.get("columns") or []
    for c in cols:
        if isinstance(c, dict):
            out.append({"name": c.get("name") or c.get("column"), "type": c.get("type") or c.get("data_type") or ""})
        elif isinstance(c, str):
            out.append({"name": c, "type": ""})
    return [c for c in out if c.get("name")]


def _detect_fk_candidate(col_name: str, role: str) -> Optional[str]:
    if role != "id":
        return None
    lc = (col_name or "").lower()
    if lc.endswith("_id") and lc != "id":
        target = lc[:-3]
        return target or None
    if lc.endswith("id") and len(lc) > 2 and lc != "id":
        return lc[:-2].rstrip("_") or None
    return None


def classify_table(
    table_name: str,
    catalog: dict,
    profile: dict,
    dim_catalog: dict,
    llm_call_fn: Optional[Callable[..., Any]] = None,
    embed_fn: Optional[Callable[[str], list[float]]] = None,
    brain_columns: Optional[list[dict]] = None,
) -> dict[str, ColumnClassification]:
    """Run all detectors per column and fuse. Returns ``{col: ColumnClassification}``."""
    # Admin gate — disable LLM/embedding tiers if turned off in settings
    try:
        from dash.admin.settings import get_setting
        if not get_setting("enable_llm_typing"):
            llm_call_fn = None
        if not get_setting("enable_embedding_matcher"):
            embed_fn = None
    except Exception:
        pass

    cols = _columns_from_catalog(catalog, table_name)
    if not cols:
        logger.info("No columns in catalog for table %s; skipping", table_name)
        return {}

    stats_d = StatisticalFingerprint()
    regex_d = RegexClassifier()
    name_d = NameClassifier()
    llm_d = LLMTyper(llm_call_fn)
    embed_d = EmbeddingMatcher(embed_fn, brain_columns)

    # Per-column tier 1-3 + 5
    per_col_signals: dict[str, list[Signal]] = {}
    per_col_meta: dict[str, dict] = {}
    llm_candidates: list[dict] = []

    for c in cols:
        col_name = c["name"]
        col_type = c.get("type", "") or ""
        col_profile = _extract_col_profile(profile, col_name)
        dim_entry = _extract_dim_entry(dim_catalog, col_name)
        samples = _extract_samples(col_profile, dim_entry)

        signals: list[Signal] = []
        try:
            signals.append(stats_d.classify(col_name, col_type, col_profile))
        except Exception as exc:  # pragma: no cover
            logger.warning("stats detector failed on %s.%s: %s", table_name, col_name, exc)
        try:
            signals.append(name_d.classify(col_name))
        except Exception as exc:  # pragma: no cover
            logger.warning("name detector failed on %s.%s: %s", table_name, col_name, exc)
        try:
            signals.append(regex_d.classify(col_name, samples))
        except Exception as exc:  # pragma: no cover
            logger.warning("regex detector failed on %s.%s: %s", table_name, col_name, exc)
        try:
            signals.append(embed_d.classify(col_name, samples))
        except Exception as exc:  # pragma: no cover
            logger.warning("embedding detector failed on %s.%s: %s", table_name, col_name, exc)

        signals = [s for s in signals if s is not None]
        per_col_signals[col_name] = signals
        per_col_meta[col_name] = {
            "type": col_type,
            "profile": col_profile,
            "samples": samples,
        }

        # Promote to LLM tier when tier-1-3 are weak or disagree
        roles = {s.role for s in signals if s.role != "unknown"}
        max_conf = max((s.confidence for s in signals), default=0.0)
        if max_conf < 0.7 or len(roles) > 1:
            llm_candidates.append(
                {
                    "name": col_name,
                    "type": col_type,
                    "ndv": _safe_int(col_profile.get("ndv") or col_profile.get("distinct")),
                    "sample": samples[:5],
                }
            )

    # Tier 4: LLM (batched)
    if llm_call_fn is not None and llm_candidates:
        try:
            for batch_start in range(0, len(llm_candidates), 10):
                batch = llm_candidates[batch_start : batch_start + 10]
                for sig in llm_d.classify_batch(table_name, batch):
                    name = sig.evidence.get("col")
                    if name and name in per_col_signals:
                        per_col_signals[name].append(sig)
        except Exception as exc:  # pragma: no cover
            logger.warning("LLM batch failed for table %s: %s", table_name, exc)

    # Fuse
    out: dict[str, ColumnClassification] = {}
    for c in cols:
        col_name = c["name"]
        meta = per_col_meta.get(col_name, {})
        col_type = meta.get("type", "")
        col_profile = meta.get("profile", {}) or {}
        samples = meta.get("samples", []) or []
        signals = per_col_signals.get(col_name, [])

        role, semantic, overall, evidence = fuse_signals(signals)

        regex_hits = [s.semantic for s in signals if s.detector == "regex" and s.semantic]
        is_pii, pii_class, mask = detect_pii(role, semantic, regex_hits)

        # Stats-derived facts
        stats_sig = next((s for s in signals if s.detector == "stats"), None)
        cardinality = "unknown"
        ndv_estimated = 0
        null_pct = 0.0
        distribution = "unknown"
        if stats_sig:
            cardinality = stats_sig.evidence.get("cardinality", "unknown")
            ndv_estimated = _safe_int(stats_sig.evidence.get("ndv"))
            null_pct = _safe_float(stats_sig.evidence.get("null_pct"))
            distribution = stats_sig.evidence.get("distribution", "unknown")

        # If PII overrode, ensure boolean role flags reflect role='pii'
        cls = ColumnClassification(
            table=table_name,
            col=col_name,
            type=col_type,
            role=role,
            semantic=semantic,
            pii=is_pii,
            pii_class=pii_class,
            cardinality=cardinality,
            ndv_estimated=ndv_estimated,
            null_pct=null_pct,
            is_dimension=role == "dimension",
            is_measure=role == "measure",
            is_id=role == "id",
            is_temporal=role == "temporal",
            fk_candidate_for=_detect_fk_candidate(col_name, role),
            value_distribution=distribution,
            masking_recommended=mask,
            confidence_overall=round(overall, 4),
            signals=signals,
        )
        cls_evidence_signal = Signal(
            detector="fusion",
            role=role,
            semantic=semantic,
            confidence=overall,
            evidence=evidence,
        )
        cls.signals.append(cls_evidence_signal)
        out[col_name] = cls
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        logger.warning("Corrupt JSON at %s: %s", path, exc)
        return None
    except OSError as exc:  # pragma: no cover
        logger.warning("I/O error reading %s: %s", path, exc)
        return None


def classify_source(
    knowledge_dir: Path,
    project_slug: str,
    source_id: int,
    llm_call_fn: Optional[Callable[..., Any]] = None,
    embed_fn: Optional[Callable[[str], list[float]]] = None,
    brain_columns: Optional[list[dict]] = None,
) -> Path:
    """Read profile + dim + catalog from disk for the given project/source,
    run :func:`classify_table` per table, and write
    ``column_classification.json``. Returns the output path.

    Per-table errors are caught and logged; broken tables are simply
    omitted from the output rather than aborting the run.
    """
    knowledge_dir = Path(knowledge_dir)
    base = knowledge_dir / project_slug / f"source_{source_id}"
    catalog = _read_json(base / "catalog.json") or {}

    out: dict[str, dict] = {}
    tables_field = catalog.get("tables") or []
    for tbl_meta in tables_field:
        if isinstance(tbl_meta, str):
            tbl = tbl_meta
        elif isinstance(tbl_meta, dict):
            tbl = tbl_meta.get("name") or tbl_meta.get("table_name")
        else:
            continue
        if not tbl:
            continue
        try:
            profile = _read_json(base / "profile" / f"{tbl}.json") or {}
            dims = _read_json(base / "dimensions" / f"{tbl}.json") or {}
            result = classify_table(
                tbl,
                catalog,
                profile,
                dims,
                llm_call_fn=llm_call_fn,
                embed_fn=embed_fn,
                brain_columns=brain_columns,
            )
            out[tbl] = {col: asdict(c) for col, c in result.items()}
        except Exception as exc:
            logger.exception("Failed to classify table %s: %s", tbl, exc)
            continue

    out_path = base / "column_classification.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, default=str))
    except OSError as exc:  # pragma: no cover
        logger.error("Failed to write %s: %s", out_path, exc)
        raise
    return out_path


__all__ = [
    "Signal",
    "ColumnClassification",
    "StatisticalFingerprint",
    "RegexClassifier",
    "NameClassifier",
    "LLMTyper",
    "EmbeddingMatcher",
    "detect_pii",
    "fuse_signals",
    "classify_table",
    "classify_source",
]
