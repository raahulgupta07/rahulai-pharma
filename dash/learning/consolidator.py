"""Consolidator — write VERIFIED hypotheses into permanent memory.

Routing rules per HypothesisType:
- definition / formula  -> dash_company_brain (category='formula' / 'glossary')
- threshold            -> dash_rules_db + dash_company_brain (category='threshold')
- causal / correlation -> dash_memories + dash_knowledge_triples (SPO triple)
- rule                 -> dash_rules_db (kind='rule')
- pattern              -> dash_memories + dash_knowledge_triples
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Optional

from dash.learning.base import (
    Hypothesis, ConsolidationResult, HypothesisType, VerificationStatus,
)

logger = logging.getLogger(__name__)


class Consolidator:
    def __init__(self, dash_engine=None, llm_call_fn=None):
        self.dash_engine = dash_engine
        self.llm_call_fn = llm_call_fn

    def consolidate(self, hypothesis: Hypothesis) -> ConsolidationResult:
        """Route VERIFIED hypothesis to appropriate target tables.

        Returns ConsolidationResult with target list + ids."""
        result = ConsolidationResult(hypothesis_id=hypothesis.id or 0)

        if hypothesis.verification_status != VerificationStatus.VERIFIED.value:
            result.error = f"not verified (status={hypothesis.verification_status})"
            return result

        # Dedup check
        if self._is_duplicate(hypothesis):
            result.duplicate_skipped = True
            return result

        htype = hypothesis.hypothesis_type

        try:
            if htype in (HypothesisType.DEFINITION.value, HypothesisType.FORMULA.value):
                self._to_brain(
                    hypothesis,
                    result,
                    category='formula' if htype == HypothesisType.FORMULA.value else 'glossary',
                )

            elif htype == HypothesisType.THRESHOLD.value:
                self._to_rules_db(hypothesis, result, kind='threshold')
                self._to_brain(hypothesis, result, category='threshold')

            elif htype == HypothesisType.RULE.value:
                self._to_rules_db(hypothesis, result, kind='rule')

            elif htype in (
                HypothesisType.CAUSAL.value,
                HypothesisType.CORRELATION.value,
                HypothesisType.PATTERN.value,
            ):
                self._to_memory(hypothesis, result)
                self._to_kg_triple(hypothesis, result)

            else:
                self._to_memory(hypothesis, result)

        except Exception as e:
            result.error = str(e)[:300]
            logger.warning(f"consolidate failed: {e}")

        return result

    def _is_duplicate(self, h: Hypothesis) -> bool:
        """Check if this hypothesis (by hash of statement) already exists."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            stmt_norm = h.statement.lower().strip()
            stmt_hash = hashlib.sha256(stmt_norm.encode()).hexdigest()[:16]
            with eng.connect() as conn:
                row = conn.execute(text(
                    "SELECT 1 FROM public.dash_memories "
                    "WHERE (metadata->>'stmt_hash' = :h OR fact = :stmt) "
                    "  AND (project_slug = :s OR (project_slug IS NULL AND :s IS NULL)) "
                    "  AND (archived IS NULL OR archived = FALSE) "
                    "LIMIT 1"
                ), {"h": stmt_hash, "stmt": h.statement, "s": h.project_slug}).fetchone()
                return row is not None
        except Exception:
            return False

    def _to_memory(self, h: Hypothesis, result: ConsolidationResult):
        """Insert into dash_memories with source='self_learned'."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            stmt_hash = hashlib.sha256(h.statement.lower().strip().encode()).hexdigest()[:16]
            with eng.connect() as conn:
                row = conn.execute(text(
                    "INSERT INTO public.dash_memories "
                    "(project_slug, source_id, fact, source, scope, "
                    " confidence_score, citation_count, archived, metadata) "
                    "VALUES (:slug, :sid, :fact, 'self_learned', :scope, "
                    " :conf, 0, FALSE, :meta) RETURNING id"
                ), {
                    "slug": h.project_slug, "sid": h.source_id,
                    "fact": h.statement,
                    "scope": "project" if h.project_slug else "global",
                    "conf": h.confidence,
                    "meta": json.dumps({
                        "stmt_hash": stmt_hash,
                        "hypothesis_id": h.id,
                        "type": h.hypothesis_type,
                        "citations": h.citations[:5],
                    }),
                }).fetchone()
                conn.commit()
            if row:
                result.memory_ids.append(int(row[0]))
                result.targets.append('memory')
        except Exception as e:
            logger.warning(f"_to_memory: {e}")

    def _to_kg_triple(self, h: Hypothesis, result: ConsolidationResult):
        """Extract SPO from statement (LLM if available, else regex split).
        Insert into dash_knowledge_triples."""
        spo = self._extract_spo(h.statement)
        if not spo:
            return
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            source_uri = f"self:hyp_{h.id}"
            with eng.connect() as conn:
                row = conn.execute(text(
                    "INSERT INTO public.dash_knowledge_triples "
                    "(subject, predicate, object, project_slug, source_uri, confidence) "
                    "VALUES (:s, :p, :o, :slug, :uri, :conf) RETURNING id"
                ), {
                    "s": spo[0][:200], "p": spo[1][:100], "o": spo[2][:200],
                    "slug": h.project_slug, "uri": source_uri,
                    "conf": h.confidence,
                }).fetchone()
                conn.commit()
            if row:
                result.triple_ids.append(int(row[0]))
                result.targets.append('kg')
        except Exception as e:
            logger.warning(f"_to_kg_triple: {e}")

    def _to_brain(self, h: Hypothesis, result: ConsolidationResult, category: str):
        """Insert into dash_company_brain."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            # Brain entry name: extract first noun phrase or use truncated statement
            name = self._extract_name(h.statement, category)
            with eng.connect() as conn:
                row = conn.execute(text(
                    "INSERT INTO public.dash_company_brain "
                    "(project_slug, source_id, name, definition, category, metadata) "
                    "VALUES (:slug, :sid, :name, :defn, :cat, :meta) RETURNING id"
                ), {
                    "slug": h.project_slug, "sid": h.source_id,
                    "name": name[:200], "defn": h.statement, "cat": category,
                    "meta": json.dumps({
                        "self_learned": True, "hypothesis_id": h.id,
                        "confidence": h.confidence,
                    }),
                }).fetchone()
                conn.commit()
            if row:
                result.brain_entry_ids.append(int(row[0]))
                result.targets.append('brain')
        except Exception as e:
            logger.warning(f"_to_brain: {e}")

    def _to_rules_db(self, h: Hypothesis, result: ConsolidationResult, kind: str):
        """Insert into dash_rules_db."""
        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            eng = self.dash_engine or get_sql_engine()
            name = self._extract_name(h.statement, kind)
            with eng.connect() as conn:
                conn.execute(text(
                    "INSERT INTO public.dash_rules_db "
                    "(project_slug, name, value, kind) "
                    "VALUES (:slug, :name, :val, :kind) ON CONFLICT DO NOTHING"
                ), {"slug": h.project_slug or "", "name": name[:200],
                     "val": h.statement, "kind": kind})
                conn.commit()
            result.targets.append('rules_db')
        except Exception as e:
            logger.warning(f"_to_rules_db: {e}")

    def _extract_spo(self, statement: str) -> Optional[tuple[str, str, str]]:
        """Try LLM extraction; fallback to regex 'X verbs Y' split."""
        if self.llm_call_fn:
            try:
                ans = self.llm_call_fn(
                    f"Extract SPO triple (subject, predicate, object) from:\n"
                    f"\"{statement}\"\n"
                    f"Output JSON: {{\"s\":\"...\",\"p\":\"...\",\"o\":\"...\"}}",
                    task='extraction',
                )
                data = json.loads(ans.strip().strip('`').replace('json\n', ''))
                if all(k in data for k in ('s', 'p', 'o')):
                    return (data['s'], data['p'], data['o'])
            except Exception:
                pass
        # Regex fallback: try to split on common verbs
        for verb in (' is ', ' has ', ' affects ', ' drives ', ' causes ',
                     ' correlates with ', ' depends on '):
            if verb in statement.lower():
                idx = statement.lower().index(verb)
                return (statement[:idx].strip(), verb.strip(),
                        statement[idx + len(verb):].strip())
        return None

    def _extract_name(self, statement: str, category: str) -> str:
        """First noun-y chunk for use as Brain entry name."""
        # Take first 5 words, title-cased
        words = re.findall(r'\w+', statement)[:5]
        if not words:
            return f"{category}_{hash(statement) % 10000}"
        return " ".join(words).title()
