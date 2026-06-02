"""
Knowledge Graph Engine
======================

Extracts SPO (Subject-Predicate-Object) triples from ALL data sources,
standardizes entities, infers relationships, and stores them for agent
context injection. Called during training via build_knowledge_graph().
"""

import json
import logging
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import NullPool, create_engine, text

from dash.paths import KNOWLEDGE_DIR
from dash.settings import training_llm_call, DEEP_MODEL
from dash.utils.column_classifier import classify_text_column
from dash.utils.column_metadata import is_lineage_column, should_skip_by_name

from db import db_url

log = logging.getLogger(__name__)

_engine = create_engine(db_url, poolclass=NullPool)


# ── Triple helper ────────────────────────────────────────────────────────────

def _triple(subj: str, pred: str, obj: str, source_type: str = "unknown",
            source_id: str = "", confidence: float = 1.0, inferred: bool = False,
            community: int | None = None) -> dict:
    return {
        "subject": str(subj), "predicate": str(pred), "object": str(obj),
        "source_type": source_type, "source_id": source_id,
        "confidence": confidence, "inferred": inferred, "community": community,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def build_knowledge_graph(project_slug: str) -> dict:
    """Build full knowledge graph for a project. Returns stats dict."""
    log.info("KG: building knowledge graph for %s", project_slug)
    stats: dict = {"tables": 0, "documents": 0, "facts": 0, "inferred": 0,
                   "entities": 0, "aliases": 0, "triples": 0}

    # 1-3  Extract triples from all sources
    table_triples = _extract_table_triples(project_slug)
    doc_triples = _extract_document_triples(project_slug)
    fact_triples = _extract_fact_triples(project_slug)

    all_triples = table_triples + doc_triples + fact_triples
    stats["tables"] = len(table_triples)
    stats["documents"] = len(doc_triples)
    stats["facts"] = len(fact_triples)

    if not all_triples:
        log.info("KG: no triples extracted for %s", project_slug)
        return stats

    # 4  Standardize entities
    all_triples, alias_map = _standardize_entities(all_triples)
    stats["aliases"] = sum(len(v) for v in alias_map.values())

    # 5  Infer relationships
    inferred = _infer_relationships(all_triples)
    stats["inferred"] = len(inferred)
    all_triples.extend(inferred)

    # Collect unique entities
    entities = set()
    for t in all_triples:
        entities.add(t["subject"])
        entities.add(t["object"])
    stats["entities"] = len(entities)
    stats["triples"] = len(all_triples)

    # 6  Save
    _save_knowledge_graph(project_slug, all_triples, alias_map)

    log.info("KG: done for %s — %s", project_slug, stats)
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Table triples
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_table_triples(project_slug: str) -> list[dict]:
    triples: list[dict] = []
    schema = project_slug
    try:
        with _engine.connect() as conn:
            conn.execute(text(f"SET LOCAL search_path TO {schema}, public"))
            # Get tables
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
            ), {"s": schema}).fetchall()
            table_names = [r[0] for r in rows]

            # Column info per table
            col_map: dict[str, list[dict]] = {}
            for tbl in table_names:
                cols = conn.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ), {"s": schema, "t": tbl}).fetchall()
                col_map[tbl] = [{"name": c[0], "type": c[1]} for c in cols]

            # Text columns: extract distinct dimension values.
            # 2026-05-25 (Day 4): name-based skip + sample-value classification
            # delegated to dash.utils.column_metadata + column_classifier so the
            # KG, QA generator, business rules engine, and dashboards share one
            # source of truth.
            import re as _re
            _date_pattern = _re.compile(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')
            _pure_number = _re.compile(r'^[\d,.\-+%$]+$')

            # Phase 9: batch metadata read replaces N per-col classify_text_column
            # calls with ONE query per table via column_stats_cache (TTL-cached).
            # Fail-soft: empty metadata → fall back to per-col classify.
            try:
                from dash.utils.column_stats_cache import get_column_metadata
            except Exception:
                get_column_metadata = None  # type: ignore

            for tbl, cols in col_map.items():
                text_cols = [
                    c["name"] for c in cols
                    if c["type"] in ("text", "character varying", "character")
                    and not is_lineage_column(c["name"])
                    and not should_skip_by_name(c["name"])
                ]
                _meta = {}
                if get_column_metadata is not None:
                    try:
                        _meta = get_column_metadata(conn, schema, tbl) or {}
                    except Exception:
                        _meta = {}
                for col in text_cols:
                    # Prefer batch metadata; fall back to per-col classifier
                    # when metadata empty (back-compat).
                    if _meta and col in _meta:
                        cls = _meta[col].get("role")
                    else:
                        cls = classify_text_column(conn, schema, tbl, col)
                    if cls != "dimension":
                        continue
                    try:
                        vals = conn.execute(text(
                            f'SELECT DISTINCT "{col}" FROM {schema}."{tbl}" '
                            f'WHERE "{col}" IS NOT NULL AND LENGTH("{col}") BETWEEN 2 AND 60 LIMIT 30'
                        )).fetchall()
                        for v in vals:
                            val = str(v[0]).strip()
                            if not val or len(val) < 2 or len(val) > 60:
                                continue
                            if _date_pattern.match(val) or _pure_number.match(val):
                                continue
                            triples.append(_triple(
                                val, "found_in_column", f"{tbl}.{col}",
                                "table", tbl, 1.0,
                            ))
                    except Exception:
                        continue

                # Numeric metric columns
                metric_kws = ("revenue", "sales", "amount", "cost", "price",
                              "profit", "margin", "budget", "count", "quantity")
                for c in cols:
                    if any(kw in c["name"].lower() for kw in metric_kws):
                        triples.append(_triple(
                            tbl, "has_metric", c["name"],
                            "table", tbl, 1.0,
                        ))

            # FK-like relationships: same column name across tables
            col_to_tables: dict[str, list[str]] = defaultdict(list)
            for tbl, cols in col_map.items():
                for c in cols:
                    col_to_tables[c["name"]].append(tbl)
            for col_name, tbls in col_to_tables.items():
                if len(tbls) > 1 and col_name not in ("id", "created_at", "updated_at"):
                    for i, t1 in enumerate(tbls):
                        for t2 in tbls[i + 1:]:
                            triples.append(_triple(
                                t1, "joins_with", t2,
                                "table", col_name, 0.8,
                            ))
    except Exception as exc:
        log.warning("KG: table extraction error for %s: %s", project_slug, exc)
    return triples


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Document triples
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_document_triples(project_slug: str) -> list[dict]:
    triples: list[dict] = []
    docs_dir = KNOWLEDGE_DIR / project_slug / "docs"
    if not docs_dir.exists():
        return triples

    for fp in docs_dir.iterdir():
        if fp.is_dir() or fp.name.startswith("."):
            continue
        try:
            doc_text = fp.read_text(errors="ignore")[:5000]
        except Exception:
            continue
        if len(doc_text.strip()) < 50:
            continue

        prompt = (
            "Extract all entities and relationships from this business document "
            "as a JSON array of triples:\n"
            '[{"subject": "...", "predicate": "...", "object": "...", "source": "' + fp.name + '"}]\n\n'
            "Focus on: company names, store names, brands, people, metrics "
            "(revenue, growth %, counts), causal relationships (X caused Y), "
            "temporal events (X opened on date), hierarchies (X owns Y).\n"
            "Extract 20-40 triples. Be precise with numbers.\n\n"
            f"DOCUMENT ({fp.name}):\n{doc_text}"
        )
        try:
            raw = training_llm_call(prompt, "deep_analysis")
            if not raw:
                continue
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "subject" in item and "object" in item:
                        triples.append(_triple(
                            item["subject"],
                            item.get("predicate", "related_to"),
                            item["object"],
                            "document", fp.name, 0.9,
                        ))
        except (json.JSONDecodeError, Exception) as exc:
            log.warning("KG: doc triple extraction failed for %s: %s", fp.name, exc)
    return triples


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Grounded fact triples
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_fact_triples(project_slug: str) -> list[dict]:
    triples: list[dict] = []
    facts_path = KNOWLEDGE_DIR / project_slug / "training" / "grounded_facts.json"
    if not facts_path.exists():
        return triples

    try:
        facts = json.loads(facts_path.read_text())
    except Exception:
        return triples

    if not isinstance(facts, list):
        facts = facts.get("facts", []) if isinstance(facts, dict) else []

    predicate_map = {
        "KPI": "has_kpi", "METRIC": "has_metric",
        "DECISION": "decided", "RISK": "has_risk",
    }

    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_type = fact.get("type", fact.get("category", "METRIC")).upper()
        entity = fact.get("entity", fact.get("subject", "unknown"))
        value = fact.get("value", fact.get("text", fact.get("description", "")))
        pred = predicate_map.get(fact_type, "has_fact")
        source_id = fact.get("source", fact.get("id", "grounded_facts"))

        if entity and value:
            triples.append(_triple(
                str(entity), pred, str(value),
                "fact", str(source_id), 0.95,
            ))
    return triples


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Entity standardization
# ═══════════════════════════════════════════════════════════════════════════════

def _standardize_entities(triples: list[dict]) -> tuple[list[dict], dict]:
    # Collect unique entity strings
    entities: set[str] = set()
    for t in triples:
        entities.add(t["subject"])
        entities.add(t["object"])
    entity_list = sorted(entities)

    if len(entity_list) < 3:
        return triples, {}

    # Group by fuzzy match
    groups: list[list[str]] = []
    used: set[str] = set()
    for i, a in enumerate(entity_list):
        if a in used:
            continue
        group = [a]
        used.add(a)
        for b in entity_list[i + 1:]:
            if b in used:
                continue
            if SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.8:
                group.append(b)
                used.add(b)
        if len(group) > 1:
            groups.append(group)

    if not groups:
        return triples, {}

    # Ask LLM to pick canonical names
    alias_map: dict[str, list[str]] = {}
    rename: dict[str, str] = {}

    # Batch groups (max 15 at a time to fit context)
    for batch_start in range(0, len(groups), 15):
        batch = groups[batch_start:batch_start + 15]
        prompt = (
            "These entity groups may contain duplicates. For each group, "
            "pick the best canonical name.\n"
            "Return JSON array:\n"
            '[{"canonical": "Best Name", "aliases": ["alt1", "alt2"]}]\n\n'
            f"Groups:\n{json.dumps(batch)}"
        )
        try:
            raw = training_llm_call(prompt, "extraction")
            if not raw:
                continue
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                for item in parsed:
                    canonical = item.get("canonical", "")
                    aliases = item.get("aliases", [])
                    if canonical and aliases:
                        alias_map[canonical] = aliases
                        for alias in aliases:
                            rename[alias] = canonical
        except (json.JSONDecodeError, Exception) as exc:
            log.warning("KG: entity standardization LLM error: %s", exc)

    # Apply renames
    if rename:
        for t in triples:
            if t["subject"] in rename:
                t["subject"] = rename[t["subject"]]
            if t["object"] in rename:
                t["object"] = rename[t["object"]]

    return triples, alias_map


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Relationship inference
# ═══════════════════════════════════════════════════════════════════════════════

def _infer_relationships(triples: list[dict]) -> list[dict]:
    inferred: list[dict] = []

    # Build adjacency: subject -> [(predicate, object, source_type)]
    adj: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for t in triples:
        adj[t["subject"]].append((t["predicate"], t["object"], t["source_type"]))

    # Transitive: parent_of chains
    parent_preds = {"parent_of", "owns", "subsidiary_of"}
    metric_preds = {"has_revenue", "has_metric", "has_kpi"}
    for subj, edges in adj.items():
        parents = [obj for pred, obj, _ in edges if pred in parent_preds]
        metrics = [(pred, obj) for pred, obj, _ in edges if pred in metric_preds]
        for parent in parents:
            for m_pred, m_obj in metrics:
                inferred.append(_triple(
                    parent, f"subsidiary_{m_pred.replace('has_', '')}",
                    m_obj, "inferred", f"transitive:{subj}", 0.6, True,
                ))

    # Cross-source verification
    entity_sources: dict[str, set[str]] = defaultdict(set)
    for t in triples:
        entity_sources[t["subject"]].add(t["source_type"])
        entity_sources[t["object"]].add(t["source_type"])
    for entity, sources in entity_sources.items():
        if len(sources) > 1:
            inferred.append(_triple(
                entity, "verified_across", " + ".join(sorted(sources)),
                "inferred", "cross_source", 0.9, True,
            ))

    # Community detection via BFS
    # Build undirected graph
    neighbors: dict[str, set[str]] = defaultdict(set)
    for t in triples:
        neighbors[t["subject"]].add(t["object"])
        neighbors[t["object"]].add(t["subject"])

    visited: set[str] = set()
    community_id = 0
    community_map: dict[str, int] = {}
    for node in neighbors:
        if node in visited:
            continue
        # BFS
        queue = [node]
        visited.add(node)
        members: list[str] = []
        while queue:
            current = queue.pop(0)
            members.append(current)
            community_map[current] = community_id
            for nb in neighbors[current]:
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        community_id += 1

    # Tag original triples with community IDs
    for t in triples:
        t["community"] = community_map.get(t["subject"])

    # Tag inferred triples
    for t in inferred:
        t["community"] = community_map.get(t["subject"])

    return inferred


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Save to DB + JSON
# ═══════════════════════════════════════════════════════════════════════════════

def _save_knowledge_graph(project_slug: str, triples: list[dict], alias_map: dict) -> None:
    try:
        with _engine.connect() as conn:
            # Bootstrap table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS public.dash_knowledge_triples (
                    id SERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    source_type TEXT,
                    source_id TEXT,
                    confidence FLOAT DEFAULT 1.0,
                    inferred BOOLEAN DEFAULT FALSE,
                    community INTEGER,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    extractor TEXT DEFAULT 'llm',
                    extraction_cost_usd NUMERIC(10,6) DEFAULT 0
                )
            """))
            # Idempotent column add for existing tables (pre-migration 075).
            conn.execute(text(
                "ALTER TABLE public.dash_knowledge_triples "
                "  ADD COLUMN IF NOT EXISTS extractor TEXT DEFAULT 'llm', "
                "  ADD COLUMN IF NOT EXISTS extraction_cost_usd NUMERIC(10,6) DEFAULT 0"
            ))
            # Delete old triples
            conn.execute(text(
                "DELETE FROM public.dash_knowledge_triples WHERE project_slug = :s"
            ), {"s": project_slug})

            # Batch insert
            for t in triples:
                conn.execute(text(
                    "INSERT INTO public.dash_knowledge_triples "
                    "(project_slug, subject, predicate, object, source_type, source_id, "
                    "confidence, inferred, community) "
                    "VALUES (:slug, :subj, :pred, :obj, :st, :sid, :conf, :inf, :comm)"
                ), {
                    "slug": project_slug,
                    "subj": t["subject"], "pred": t["predicate"], "obj": t["object"],
                    "st": t.get("source_type", "unknown"),
                    "sid": t.get("source_id", ""),
                    "conf": t.get("confidence", 1.0),
                    "inf": t.get("inferred", False),
                    "comm": t.get("community"),
                })
            conn.commit()
    except Exception as exc:
        log.error("KG: DB save error for %s: %s", project_slug, exc)

    # Vector sync — re-embed each triple into dash_vectors (best-effort).
    # Per-call source_id dedup: full KG rebuild can pass the same triple twice
    # in one batch; this set guards against double-enqueue within the call.
    # Across calls, vector_sync's text_hash ON CONFLICT keeps the upsert idempotent.
    try:
        import asyncio as _asyncio_vs
        from dash.tools.vector_sync import VECTOR_SYNC as _VS

        try:
            _loop = _asyncio_vs.get_running_loop()
        except RuntimeError:
            _loop = None

        _seen_sids: set[str] = set()
        for _t in triples:
            _spo = f"{_t.get('subject','')} {_t.get('predicate','')} {_t.get('object','')}".strip()
            if not _spo:
                continue
            _sid = f"{_t.get('subject','')}|{_t.get('predicate','')}|{_t.get('object','')}"[:255]
            if _sid in _seen_sids:
                continue
            _seen_sids.add(_sid)
            if _loop is not None:
                _loop.create_task(_VS.enqueue(project_slug, "kg", _sid, _spo, {}))
            # else: no running loop (e.g. background thread) — skip; reembed_loop
            #       will catch up on the next 6h sweep.
    except Exception:
        log.debug("vector_sync enqueue (kg) skipped", exc_info=True)

    # Save JSON artifacts
    training_dir = KNOWLEDGE_DIR / project_slug / "training"
    training_dir.mkdir(parents=True, exist_ok=True)

    # Alias map
    alias_path = training_dir / "entity_aliases.json"
    try:
        alias_path.write_text(json.dumps(alias_map, indent=2, default=str))
    except Exception as exc:
        log.warning("KG: alias save error: %s", exc)

    # Triples summary
    kg_path = training_dir / "knowledge_graph.json"
    summary = {
        "project_slug": project_slug,
        "triple_count": len(triples),
        "entity_count": len({t["subject"] for t in triples} | {t["object"] for t in triples}),
        "source_types": list({t.get("source_type", "unknown") for t in triples}),
        "communities": max((t.get("community") or 0 for t in triples), default=0) + 1,
        "triples": triples[:500],  # cap for file size
    }
    try:
        kg_path.write_text(json.dumps(summary, indent=2, default=str))
    except Exception as exc:
        log.warning("KG: JSON save error: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Context injection for agents
# ═══════════════════════════════════════════════════════════════════════════════

def get_knowledge_graph_context(
    project_slug: str,
    for_agent: str = "analyst",
    active_source_uris: list[str] | None = None,
) -> str:
    """Return formatted KG context for injection into agent prompts.

    ``active_source_uris`` (Phase 3): when supplied, triples are filtered to
    those whose ``source_uri`` is NULL (legacy/project-wide) OR matches one
    of the active provider URIs. Defaults to None = no extra filter.
    """
    uris = active_source_uris or []
    try:
        with _engine.connect() as conn:
            if uris:
                rows = conn.execute(text(
                    "SELECT subject, predicate, object, source_type, source_id, "
                    "confidence, inferred, community "
                    "FROM public.dash_knowledge_triples WHERE project_slug = :s "
                    "AND (source_uri IS NULL OR source_uri = ANY(:uris)) "
                    "ORDER BY confidence DESC LIMIT 300"
                ), {"s": project_slug, "uris": uris}).fetchall()
            else:
                rows = conn.execute(text(
                    "SELECT subject, predicate, object, source_type, source_id, "
                    "confidence, inferred, community "
                    "FROM public.dash_knowledge_triples WHERE project_slug = :s "
                    "ORDER BY confidence DESC LIMIT 300"
                ), {"s": project_slug}).fetchall()
    except Exception:
        return ""

    if not rows:
        return ""

    # Load alias map
    alias_map: dict[str, list[str]] = {}
    alias_path = KNOWLEDGE_DIR / project_slug / "training" / "entity_aliases.json"
    if alias_path.exists():
        try:
            alias_map = json.loads(alias_path.read_text())
        except Exception:
            pass

    triples = [
        {"subject": r[0], "predicate": r[1], "object": r[2],
         "source_type": r[3], "source_id": r[4], "confidence": r[5],
         "inferred": r[6], "community": r[7]}
        for r in rows
    ]

    if for_agent == "analyst":
        return _context_analyst(triples, alias_map)
    elif for_agent == "researcher":
        return _context_researcher(triples, alias_map)
    else:
        return _context_leader(triples, alias_map)


def _context_analyst(triples: list[dict], alias_map: dict) -> str:
    lines: list[str] = []

    # Entity-to-table map
    entity_cols: dict[str, list[str]] = defaultdict(list)
    for t in triples:
        if t["predicate"] == "found_in_column" and t["source_type"] == "table":
            entity_cols[t["subject"]].append(t["object"])
    if entity_cols:
        lines.append("KNOWLEDGE GRAPH — ENTITY-TO-TABLE MAP:")
        for entity, cols in sorted(entity_cols.items())[:30]:
            lines.append(f"  {entity} -> {', '.join(cols[:3])} (found {len(cols)} times)")

    # Aliases
    if alias_map:
        lines.append("\nENTITY ALIASES (use in WHERE clauses):")
        for canonical, aliases in sorted(alias_map.items())[:20]:
            alias_str = " = ".join(f"'{a}'" for a in aliases)
            lines.append(f'  "{canonical}" = {alias_str}')

    # Verified metrics
    verified = [t for t in triples
                if t["predicate"] == "verified_across" and t["inferred"]]
    if verified:
        lines.append("\nVERIFIED METRICS:")
        for t in verified[:15]:
            lines.append(f"  {t['subject']}: confirmed in {t['object']}")

    return "\n".join(lines) if lines else ""


def _context_researcher(triples: list[dict], alias_map: dict) -> str:
    lines: list[str] = []

    # Entity-to-document map
    entity_docs: dict[str, list[str]] = defaultdict(list)
    for t in triples:
        if t["source_type"] == "document":
            entity_docs[t["subject"]].append(t["source_id"])
    if entity_docs:
        lines.append("KNOWLEDGE GRAPH — ENTITY-TO-DOCUMENT MAP:")
        for entity, docs in sorted(entity_docs.items())[:25]:
            lines.append(f"  {entity} -> {', '.join(sorted(set(docs))[:3])}")

    # Causal relationships
    causal_preds = {"caused", "led_to", "resulted_in", "decided", "triggered"}
    causals = [t for t in triples if t["predicate"] in causal_preds]
    if causals:
        lines.append("\nCAUSAL RELATIONSHIPS:")
        for t in causals[:15]:
            lines.append(f"  {t['subject']} -> {t['predicate']} -> {t['object']}")

    # Communities
    communities: dict[int, list[str]] = defaultdict(list)
    for t in triples:
        if t["community"] is not None:
            communities[t["community"]].append(t["subject"])
    if communities:
        lines.append("\nBUSINESS COMMUNITIES:")
        for cid in sorted(communities)[:8]:
            members = sorted(set(communities[cid]))[:6]
            lines.append(f"  Community {cid + 1}: {', '.join(members)}")

    return "\n".join(lines) if lines else ""


def _context_leader(triples: list[dict], alias_map: dict) -> str:
    lines: list[str] = []

    # Entity routing
    entity_sources: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for t in triples:
        st = t["source_type"]
        if st == "table":
            entity_sources[t["subject"]]["tables"].append(t["source_id"])
        elif st == "document":
            entity_sources[t["subject"]]["documents"].append(t["source_id"])
        elif st == "fact":
            entity_sources[t["subject"]]["facts"].append(t["source_id"])

    if entity_sources:
        lines.append("KNOWLEDGE GRAPH ROUTING:")
        for entity, sources in sorted(entity_sources.items())[:20]:
            parts: list[str] = []
            for stype in ("tables", "documents", "facts"):
                items = sources.get(stype, [])
                if items:
                    parts.append(f"{stype} in {', '.join(sorted(set(items))[:3])}")
            if parts:
                lines.append(f"  {entity}: {'; '.join(parts)}")

    # Summary stats
    entities = set()
    for t in triples:
        entities.add(t["subject"])
        entities.add(t["object"])
    n_communities = len({t["community"] for t in triples if t["community"] is not None})
    lines.append(f"  Entity count: {len(entities)}, "
                 f"Relationship count: {len(triples)}, "
                 f"Communities: {n_communities}")

    return "\n".join(lines) if lines else ""


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Continuous KG Learning — extract triples from every chat
# ═══════════════════════════════════════════════════════════════════════════════

def extract_chat_triples(project_slug: str, question: str, answer: str, source_uri: str | None = None) -> dict:
    """Extract triples from a chat Q&A and add to knowledge graph.
    Called in background after every chat. KG grows with every conversation.

    ``source_uri`` (Phase 3): tags persisted triples with the active provider
    source URI. NULL = legacy/project-wide.

    Tries the fast zero-LLM entity linker FIRST (regex + dict + per-project
    entity match). Only escalates to the LLM path when ``should_fallback_to_llm``
    signals the text is too dense for regex coverage. Each persisted triple is
    tagged with ``extractor`` (regex|llm) for cost analytics.
    """
    if not question or not answer or len(answer) < 50:
        return {"triples": 0}

    # ── Fast path ───────────────────────────────────────────────────────────
    fast_triples: list[dict] = []
    fast_entities: list[dict] = []
    fallback_needed = True
    try:
        from dash.learning.entity_linker import (
            extract_entities as _fast_entities,
            extract_triples_fast as _fast_triples,
            should_fallback_to_llm as _should_fallback,
        )
        combined = f"{question}\n{answer}"
        fast_entities = _fast_entities(combined, project_slug=project_slug)
        fast_triples = _fast_triples(combined, project_slug=project_slug)
        fallback_needed = _should_fallback(combined, fast_entities, fast_triples)
    except Exception as exc:
        log.debug("KG: fast entity_linker skipped: %s", exc)

    # Persist whatever the fast path got (capped at 20).
    persisted_fast = 0
    if fast_triples:
        try:
            with _engine.connect() as conn:
                for t in fast_triples[:20]:
                    conn.execute(text(
                        "INSERT INTO public.dash_knowledge_triples "
                        "(project_slug, subject, predicate, object, source_type, "
                        " source_id, confidence, inferred, source_uri, "
                        " extractor, extraction_cost_usd) "
                        "VALUES (:slug, :s, :p, :o, :st, :si, :conf, FALSE, :suri, "
                        " 'regex', 0)"
                    ), {
                        "slug": project_slug,
                        "s": t["subject"], "p": t["predicate"], "o": t["object"],
                        "st": "chat", "si": project_slug,
                        "conf": float(t.get("confidence", 0.7)),
                        "suri": source_uri,
                    })
                    persisted_fast += 1
                conn.commit()
        except Exception as exc:
            log.warning("KG: fast triple insert failed: %s", exc)

    if not fallback_needed:
        log.info(
            "KG: extracted %d chat triples (regex) for %s (entities=%d, llm_skipped)",
            persisted_fast, project_slug, len(fast_entities),
        )
        return {"triples": persisted_fast, "extractor": "regex"}

    # ── LLM fallback path ───────────────────────────────────────────────────
    prompt = (
        "Extract factual relationships from this data analysis conversation as JSON array of triples.\n"
        "Only extract FACTS (not opinions). Each triple:\n"
        '{"subject": "...", "predicate": "...", "object": "..."}\n\n'
        "Focus on: entity names, metric values confirmed by data, causal relationships,\n"
        "connections between entities. Extract 3-10 triples. Be precise.\n"
        "Return ONLY valid JSON array.\n\n"
        f"Q: {question[:500]}\nA: {answer[:2500]}"
    )
    try:
        import re as _re
        result = training_llm_call(prompt, "extraction")
        if not result:
            return {"triples": 0}
        match = _re.search(r'\[.*\]', result, _re.DOTALL)
        if not match:
            return {"triples": 0}
        triples_raw = json.loads(match.group())
        if not isinstance(triples_raw, list):
            return {"triples": 0}

        triples = []
        for t in triples_raw[:10]:
            subj = str(t.get("subject", "")).strip()
            pred = str(t.get("predicate", "")).strip()
            obj = str(t.get("object", "")).strip()
            if subj and pred and obj and len(subj) > 1 and len(obj) > 1:
                triples.append(_triple(subj, pred, obj, "chat", project_slug, 0.8))

        if not triples:
            return {"triples": 0}

        with _engine.connect() as conn:
            for t in triples:
                conn.execute(text(
                    "INSERT INTO public.dash_knowledge_triples "
                    "(project_slug, subject, predicate, object, source_type, source_id, "
                    " confidence, inferred, source_uri, extractor, extraction_cost_usd) "
                    "VALUES (:slug, :s, :p, :o, :st, :si, :conf, FALSE, :suri, "
                    " 'llm', 0.0001)"
                ), {"slug": project_slug, "s": t["subject"], "p": t["predicate"],
                    "o": t["object"], "st": "chat", "si": project_slug, "conf": 0.8,
                    "suri": source_uri})
            conn.commit()

        log.info(
            "KG: extracted %d chat triples (llm + %d regex) for %s",
            len(triples), persisted_fast, project_slug,
        )
        return {"triples": len(triples) + persisted_fast, "extractor": "llm"}
    except Exception as exc:
        log.warning("KG: chat triple extraction failed: %s", exc)
        return {"triples": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Auto-Memory Promotion — save facts without user approval
# ═══════════════════════════════════════════════════════════════════════════════

def auto_promote_facts(project_slug: str, question: str, answer: str, source_id: int | None = None) -> dict:
    """Auto-save high-confidence factual observations to memories. No approval needed.

    ``source_id`` (Phase 3): tag the persisted memory with the active provider
    source. NULL = legacy/project-wide.
    """
    if not answer or len(answer) < 100:
        return {"promoted": 0}

    prompt = (
        "From this data analysis, extract 1-3 FACTUAL observations to remember.\n"
        "Only FACTS confirmed by data (not opinions, not recommendations).\n"
        "Examples: 'store NHLTIS has highest achievement', 'revenue uses net sales minus returns'\n"
        "Return JSON array of strings. Return [] if no clear facts.\n\n"
        f"Q: {question[:500]}\nA: {answer[:2000]}"
    )
    try:
        import re as _re
        result = training_llm_call(prompt, "extraction")
        if not result:
            return {"promoted": 0}
        match = _re.search(r'\[.*\]', result, _re.DOTALL)
        if not match:
            return {"promoted": 0}
        facts = json.loads(match.group())
        if not isinstance(facts, list):
            return {"promoted": 0}

        promoted = 0
        with _engine.connect() as conn:
            for fact in facts[:3]:
                fact_text = str(fact).strip().strip('"')
                if len(fact_text) < 10 or len(fact_text) > 300:
                    continue
                existing = conn.execute(text(
                    "SELECT COUNT(*) FROM public.dash_memories "
                    "WHERE project_slug = :slug AND fact = :fact"
                ), {"slug": project_slug, "fact": fact_text}).scalar()
                if existing and existing > 0:
                    continue
                conn.execute(text(
                    "INSERT INTO public.dash_memories (project_slug, fact, source, scope, source_id) "
                    "VALUES (:slug, :fact, 'auto_learned', 'project', :sid)"
                ), {"slug": project_slug, "fact": fact_text, "sid": source_id})
                promoted += 1
            conn.commit()

        if promoted > 0:
            log.info("KG: auto-promoted %d facts for %s", promoted, project_slug)
        return {"promoted": promoted}
    except Exception as exc:
        log.warning("KG: auto-promote failed: %s", exc)
        return {"promoted": 0}


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Rich User Preference Tracking (Mem0-style)
# ═══════════════════════════════════════════════════════════════════════════════

def track_user_preferences(project_slug: str, user_id: int | None, question: str, answer: str) -> None:
    """Track deeper user preferences: analysis style, favorite metrics, report format.
    Goes beyond chart clicks — learns HOW the user likes to consume data."""
    if not user_id or not question or len(question) < 10:
        return
    try:
        import re as _re
        prefs: dict = {}

        # Detect preferred metrics from questions
        metric_words = _re.findall(r'\b(revenue|sales|margin|growth|cost|profit|ebitda|attrition|budget|tc|gm)\b', question.lower())
        if metric_words:
            prefs["favorite_metrics"] = metric_words

        # Detect analysis depth preference
        if any(w in question.lower() for w in ["detail", "break down", "drill", "decompose", "by store", "by month"]):
            prefs["prefers_detail"] = True
        elif any(w in question.lower() for w in ["quick", "summary", "just", "simple", "headline"]):
            prefs["prefers_summary"] = True

        # Detect comparison preference
        if any(w in question.lower() for w in ["vs", "compare", "last year", "last month", "yoy", "mom"]):
            prefs["likes_comparisons"] = True

        # Detect format preference from answer engagement
        if any(w in question.lower() for w in ["chart", "graph", "visual", "plot"]):
            prefs["prefers_visual"] = True
        if any(w in question.lower() for w in ["table", "data", "numbers", "list", "export"]):
            prefs["prefers_tabular"] = True

        if not prefs:
            return

        # Merge into dash_user_preferences JSONB
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT preferences FROM public.dash_user_preferences WHERE user_id = :uid AND project_slug = :slug"
            ), {"uid": user_id, "slug": project_slug}).fetchone()

            existing = {}
            if row and row[0]:
                existing = row[0] if isinstance(row[0], dict) else json.loads(row[0])

            # Merge: increment counters for repeated preferences
            for key, val in prefs.items():
                if isinstance(val, list):
                    existing_list = existing.get(key, [])
                    if isinstance(existing_list, list):
                        existing[key] = list(set(existing_list + val))[:20]
                    else:
                        existing[key] = val
                elif isinstance(val, bool) and val:
                    existing[key] = existing.get(key, 0) + 1 if isinstance(existing.get(key), int) else 1

            if row:
                conn.execute(text(
                    "UPDATE public.dash_user_preferences SET preferences = CAST(:p AS jsonb) WHERE user_id = :uid AND project_slug = :slug"
                ), {"p": json.dumps(existing), "uid": user_id, "slug": project_slug})
            else:
                conn.execute(text(
                    "INSERT INTO public.dash_user_preferences (user_id, project_slug, preferences) VALUES (:uid, :slug, CAST(:p AS jsonb))"
                ), {"uid": user_id, "slug": project_slug, "p": json.dumps(existing)})
            conn.commit()
    except Exception as exc:
        log.debug("Preference tracking skipped: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Episodic Memory — save user reactions as timestamped events
# ═══════════════════════════════════════════════════════════════════════════════

def extract_episodic_memory(project_slug: str, question: str, answer: str) -> dict:
    """Detect noteworthy user reactions and save as episodic events.
    'User was surprised by X', 'User corrected Y', 'User asked about X repeatedly'."""
    if not question or len(question) < 15:
        return {"events": 0}
    try:
        import re as _re
        import time

        events: list[str] = []
        q = question.lower()

        # Detect surprise/concern
        if any(w in q for w in ["surprising", "unexpected", "weird", "strange", "doesn't make sense", "wrong", "impossible"]):
            events.append(f"User questioned data accuracy: '{question[:80]}'")

        # Detect corrections
        if any(w in q for w in ["actually", "no that's wrong", "incorrect", "should be", "not right", "fix"]):
            events.append(f"User corrected agent: '{question[:80]}'")

        # Detect repeated interest (same topic asked again)
        if any(w in q for w in ["again", "one more time", "show me again", "same as before", "like last time"]):
            events.append(f"User revisited topic: '{question[:80]}'")

        # Detect high-priority questions
        if any(w in q for w in ["urgent", "critical", "important", "asap", "board meeting", "ceo wants"]):
            events.append(f"High-priority question: '{question[:80]}'")

        if not events:
            return {"events": 0}

        ts = time.strftime("%Y-%m-%d %H:%M")
        with _engine.connect() as conn:
            for event in events[:2]:
                conn.execute(text(
                    "INSERT INTO public.dash_memories (project_slug, fact, source, scope) "
                    "VALUES (:slug, :fact, 'episodic', 'project')"
                ), {"slug": project_slug, "fact": f"[{ts}] {event}"})
            conn.commit()

        log.info("KG: saved %d episodic events for %s", len(events), project_slug)
        return {"events": len(events)}
    except Exception as exc:
        log.debug("Episodic memory skipped: %s", exc)
        return {"events": 0}
