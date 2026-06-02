"""
Semantic Search Layer
=====================
Unified search across all knowledge sources with reranking.

Sources: PgVector KB, Company Brain, Knowledge Graph, Grounded Facts
Reranking: Cohere via OpenRouter → keyword fallback → raw fallback
"""

import os
import json
import logging
from agno.tools import tool

logger = logging.getLogger(__name__)

# Rerank models to try in order (first success wins)
RERANK_MODELS = [
    "cohere/rerank-4-pro",
    "cohere/rerank-4-fast",
    "cohere/rerank-v3.5",
]


def _keyword_rerank(query: str, documents: list[dict], top_n: int = 5) -> list[dict]:
    """Fallback reranker — score by word overlap. No API, no model, never fails."""
    query_words = set(w.lower() for w in query.split() if len(w) > 2)
    if not query_words:
        return documents[:top_n]

    for doc in documents:
        text = doc.get("text", "").lower()
        doc_words = set(w for w in text.split() if len(w) > 2)
        overlap = len(query_words & doc_words)
        # Boost exact phrase match
        if query.lower() in text:
            overlap += 5
        # Boost source priority (facts > kb > brain > kg)
        source_boost = {"fact": 0.3, "kb": 0.2, "brain": 0.1, "kg": 0.05}.get(doc.get("source_type", ""), 0)
        doc["relevance_score"] = min((overlap / max(len(query_words), 1)) + source_boost, 1.0)

    documents.sort(key=lambda d: d.get("relevance_score", 0), reverse=True)
    return [d for d in documents[:top_n] if d.get("relevance_score", 0) > 0.1]


def _rerank(query: str, documents: list[dict], top_n: int = 5) -> list[dict]:
    """3-tier reranking: Cohere models → keyword → raw."""
    if not documents:
        return []

    doc_texts = [d.get("text", "")[:500] for d in documents]  # cap length for reranker
    api_key = os.getenv("OPENROUTER_API_KEY", "")

    if api_key:
        import httpx
        for model in RERANK_MODELS:
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/rerank",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": model, "query": query, "documents": doc_texts, "top_n": top_n},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    ranked = []
                    for r in results:
                        idx = r.get("index", 0)
                        if idx < len(documents):
                            doc = documents[idx].copy()
                            doc["relevance_score"] = r.get("relevance_score", 0)
                            ranked.append(doc)
                    logger.info(f"Reranked {len(documents)} → {len(ranked)} with {model}")
                    return [d for d in ranked if d.get("relevance_score", 0) > 0.1]
            except Exception as e:
                logger.warning(f"Rerank {model} failed: {e}")
                continue

    # All API models failed → keyword fallback
    logger.info("Rerank API unavailable, using keyword fallback")
    return _keyword_rerank(query, documents, top_n)


def _search_pgvector(query: str, project_slug: str | None = None) -> list[dict]:
    """Search PgVector knowledge base."""
    results = []
    try:
        from db.session import create_knowledge
        kb_table = f"knowledge_{project_slug}" if project_slug else "dash_knowledge"
        kb = create_knowledge(f"search_{project_slug or 'global'}", kb_table)
        # Use Agno's search
        docs = kb.search(query=query, num_documents=15)
        for doc in docs:
            content = doc.content if hasattr(doc, 'content') else str(doc)
            if content and len(content.strip()) > 10:
                results.append({
                    "text": content[:500],
                    "source_type": "kb",
                    "source": f"knowledge:{project_slug or 'global'}",
                })
    except Exception as e:
        logger.debug(f"PgVector search failed: {e}")
    return results


def _search_brain(query: str, project_slug: str | None = None) -> list[dict]:
    """Search Company Brain (glossary, aliases, formulas, org)."""
    results = []
    try:
        from sqlalchemy import text
        from db import get_sql_engine
        engine = get_sql_engine()

        # Extract key terms from query
        terms = [w.strip() for w in query.split() if len(w.strip()) > 2]
        if not terms:
            return []

        patterns = [f"%{t}%" for t in terms[:5]]
        placeholders_name = " OR ".join(f"name ILIKE :p{i}" for i in range(len(patterns)))
        placeholders_def = " OR ".join(f"definition ILIKE :p{i}" for i in range(len(patterns)))
        params = {f"p{i}": p for i, p in enumerate(patterns)}

        scope_filter = "(project_slug IS NULL"
        if project_slug:
            scope_filter += f" OR project_slug = :slug"
            params["slug"] = project_slug
        scope_filter += ")"

        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT category, name, definition, project_slug FROM public.dash_company_brain "
                f"WHERE {scope_filter} AND ({placeholders_name} OR {placeholders_def}) "
                f"ORDER BY CASE WHEN project_slug IS NOT NULL THEN 0 ELSE 1 END "
                f"LIMIT 10"
            ), params).fetchall()

            for r in rows:
                scope = f"[{r[3]}]" if r[3] else "[global]"
                results.append({
                    "text": f"[{r[0].upper()}] {r[1]}: {r[2]} {scope}",
                    "source_type": "brain",
                    "source": f"brain:{r[0]}",
                })
    except Exception as e:
        logger.debug(f"Brain search failed: {e}")
    return results


def _search_kg(query: str, project_slug: str | None = None) -> list[dict]:
    """Search Knowledge Graph triples."""
    results = []
    try:
        from sqlalchemy import text
        from db import get_sql_engine
        engine = get_sql_engine()

        terms = [w.strip() for w in query.split() if len(w.strip()) > 2]
        if not terms:
            return []

        patterns = [f"%{t}%" for t in terms[:5]]
        subject_match = " OR ".join(f"subject ILIKE :t{i}" for i in range(len(patterns)))
        object_match = " OR ".join(f"object ILIKE :t{i}" for i in range(len(patterns)))
        params = {f"t{i}": p for i, p in enumerate(patterns)}

        slug_filter = ""
        if project_slug:
            slug_filter = " AND project_slug = :slug"
            params["slug"] = project_slug

        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT subject, predicate, object, source FROM public.dash_knowledge_triples "
                f"WHERE ({subject_match} OR {object_match}){slug_filter} LIMIT 10"
            ), params).fetchall()

            for r in rows:
                results.append({
                    "text": f"{r[0]} → {r[1]} → {r[2]} (source: {r[3]})",
                    "source_type": "kg",
                    "source": f"kg:{r[3]}",
                })
    except Exception as e:
        logger.debug(f"KG search failed: {e}")
    return results


def _search_facts(query: str, project_slug: str | None = None) -> list[dict]:
    """Search grounded facts from LangExtract."""
    results = []
    try:
        from pathlib import Path
        facts_path = Path(f"knowledge/{project_slug}/grounded_facts.json") if project_slug else None
        if facts_path and facts_path.exists():
            facts = json.loads(facts_path.read_text())
            query_lower = query.lower()
            for fact in facts[:50]:  # Cap at 50
                text_val = fact.get("text", "") or fact.get("fact", "") or str(fact)
                if any(w.lower() in text_val.lower() for w in query.split() if len(w) > 2):
                    results.append({
                        "text": text_val[:300],
                        "source_type": "fact",
                        "source": f"fact:{fact.get('type', 'unknown')}",
                    })
                    if len(results) >= 5:
                        break
    except Exception as e:
        logger.debug(f"Facts search failed: {e}")
    return results


def create_search_all_tool(project_slug: str | None = None):
    """Create unified semantic search tool for an agent."""

    @tool(name="search_all", description="Search ALL knowledge sources — documents, brain, knowledge graph, grounded facts. Use when you need context beyond SQL data. Returns top results ranked by relevance. Args: query (str) — what to search for")
    def search_all(query: str) -> str:
        """Search all knowledge sources and return reranked results."""
        # Search 4 sources
        all_results = []
        all_results.extend(_search_pgvector(query, project_slug))
        all_results.extend(_search_brain(query, project_slug))
        all_results.extend(_search_kg(query, project_slug))
        all_results.extend(_search_facts(query, project_slug))

        if not all_results:
            return "No relevant knowledge found across any source."

        # Rerank (Cohere → keyword → raw)
        ranked = _rerank(query, all_results, top_n=7)

        if not ranked:
            return "No relevant results after filtering."

        # Format for agent
        lines = [f"SEMANTIC SEARCH RESULTS ({len(ranked)} relevant from {len(all_results)} total):"]
        for i, r in enumerate(ranked):
            score = f" (relevance: {r['relevance_score']:.2f})" if 'relevance_score' in r else ""
            lines.append(f"\n{i+1}. [{r.get('source_type', '?').upper()}]{score}\n   {r['text']}")

        return "\n".join(lines)

    return search_all
