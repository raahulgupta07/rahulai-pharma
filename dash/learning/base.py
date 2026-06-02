"""Self-learning subsystem — base types, enums, dataclasses.

Other modules (curiosity, researcher, hypothesis, verifier, consolidator,
forgetting, promotion) import from here.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class QuestionStatus(str, enum.Enum):
    PENDING = "pending"
    RESEARCHING = "researching"
    ANSWERED = "answered"
    FAILED = "failed"
    ARCHIVED = "archived"


class QuestionReason(str, enum.Enum):
    KG_HOLE = "kg_hole"
    DRIFT = "drift"
    FAILED_QA = "failed_qa"
    THUMBS_DOWN = "thumbs_down"
    ANOMALY = "anomaly"
    UNDERUSED_TABLE = "underused_table"
    GAP = "gap"
    USER_REQUEST = "user_request"
    CYCLE_FOLLOWUP = "cycle_followup"
    CROSS_SOURCE = "cross_source"


class HypothesisType(str, enum.Enum):
    CAUSAL = "causal"
    CORRELATION = "correlation"
    RULE = "rule"
    FORMULA = "formula"
    THRESHOLD = "threshold"
    DEFINITION = "definition"
    PATTERN = "pattern"


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class ResearchTier(str, enum.Enum):
    INTERNAL_DB = "internal_db"
    INTERNAL_KG = "internal_kg"
    INTERNAL_BRAIN = "internal_brain"
    INTERNAL_MEMORY = "internal_memory"
    LLM_DEEP_THINK = "llm_deep_think"
    WEB_SEARCH = "web_search"
    EXTERNAL_API = "external_api"


class PromotionMethod(str, enum.Enum):
    AUTO_LLM = "auto_llm"
    AUTO_TRIANGULATION = "auto_triangulation"
    USER = "user"
    ADMIN = "admin"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Question:
    """A curiosity question awaiting research."""
    id: Optional[int] = None
    project_slug: Optional[str] = None     # None = central
    source_id: Optional[int] = None
    question: str = ""
    topic: Optional[str] = None
    reason: str = QuestionReason.GAP.value
    priority: int = 50
    status: str = QuestionStatus.PENDING.value
    cycle_num: int = 0
    domain: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None


@dataclass
class ResearchSource:
    """One piece of evidence from one source."""
    tier: str                           # ResearchTier value
    source: str                         # 'fred', 'wikipedia', 'sql', 'kg', 'brain', etc.
    url: Optional[str] = None
    snippet: str = ""
    confidence: float = 0.5
    cost_usd: float = 0.0
    fetched_at: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ResearchDossier:
    """Aggregated research for one question across all sources."""
    question_id: Optional[int] = None
    question_text: str = ""
    sources: list[ResearchSource] = field(default_factory=list)
    triangulation_count: int = 0          # # tiers that agreed
    summary: str = ""                     # LLM-synthesized
    total_cost_usd: float = 0.0

    def by_tier(self) -> dict[str, list[ResearchSource]]:
        out: dict[str, list[ResearchSource]] = {}
        for s in self.sources:
            out.setdefault(s.tier, []).append(s)
        return out

    def best_evidence(self, n: int = 3) -> list[ResearchSource]:
        return sorted(self.sources, key=lambda s: s.confidence, reverse=True)[:n]


@dataclass
class Hypothesis:
    """A theory formed from research, awaiting verification."""
    id: Optional[int] = None
    project_slug: Optional[str] = None
    source_id: Optional[int] = None
    question_id: Optional[int] = None
    statement: str = ""
    hypothesis_type: str = HypothesisType.PATTERN.value
    sources_consulted: list[dict] = field(default_factory=list)
    triangulation_count: int = 0
    confidence: float = 0.5                # 0..1
    verification_status: str = VerificationStatus.PENDING.value
    verified_by: Optional[str] = None       # 'sql' | 'cross_source' | 'llm_review' | 'user'
    verified_at: Optional[datetime] = None
    failed_reason: Optional[str] = None
    citations: list[str] = field(default_factory=list)
    promoted_to_central: bool = False
    metadata: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None


@dataclass
class VerificationResult:
    """Outcome of testing a hypothesis."""
    hypothesis_id: int
    status: str                          # VerificationStatus value
    method: str                          # 'sql' | 'cross_source' | 'llm_review' | 'eval'
    evidence: dict = field(default_factory=dict)
    confidence_delta: float = 0.0        # add to hypothesis confidence
    failed_reason: Optional[str] = None


@dataclass
class ConsolidationResult:
    """Outcome of writing a verified hypothesis to long-term memory."""
    hypothesis_id: int
    targets: list[str] = field(default_factory=list)  # ['memory','kg','brain','rules']
    memory_ids: list[int] = field(default_factory=list)
    triple_ids: list[int] = field(default_factory=list)
    brain_entry_ids: list[int] = field(default_factory=list)
    duplicate_skipped: bool = False
    error: Optional[str] = None


@dataclass
class CycleResult:
    """Outcome of one full daily cycle."""
    project_slug: Optional[str] = None
    cycle_num: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    questions_generated: int = 0
    questions_answered: int = 0
    hypotheses_formed: int = 0
    hypotheses_verified: int = 0
    hypotheses_failed: int = 0
    facts_consolidated: int = 0
    facts_promoted: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None


@dataclass
class PromotionCandidate:
    """A verified hypothesis being considered for central pool promotion."""
    hypothesis_id: int
    source_project_slug: str
    fact_text: str
    fact_type: str                        # 'definition'|'formula'|'pattern'|'threshold'|'kg_triple'
    triangulation_count: int = 0
    pii_safe: bool = False
    contains_data_values: bool = False
    rejection_reason: Optional[str] = None
