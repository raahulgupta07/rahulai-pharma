"""
Auto sub-agent synthesis for Dash AgentOS training pipeline.

During TRAIN ALL, clusters training Q&A + table catalog + brain KPIs to propose
2-5 specialist sub-agents per project. Writes drafts (enabled=false) into
existing `dash.dash_custom_agents` table. No new schema, fail-soft.

Single entrypoint: synthesize_subagents(project_slug, logger=None) -> dict
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Callable, Optional

_logger = logging.getLogger(__name__)

MAX_SUBAGENTS_PER_RUN = 5
MIN_QUESTIONS_PER_CLUSTER = 3
MAX_LLM_CALLS = 6
MAX_QA_ROWS = 100
ALLOWED_TOOLS = ["run_sql", "make_chart", "send_alert", "run_pareto", "detect_anomalies_ml"]


def _safe_log(logger: Optional[Callable[[str], None]], msg: str) -> None:
    try:
        if logger:
            logger(msg)
        else:
            _logger.info(msg)
    except Exception:
        pass


def _parse_json_4tier(raw: str) -> Optional[dict]:
    """4-tier JSON parse: direct → strip fences → regex extract → trailing-comma repair."""
    if not raw or not isinstance(raw, str):
        return None
    # Tier 1: direct
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Tier 2: strip ```fences
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
        try:
            return json.loads(s)
        except Exception:
            pass
    # Tier 3: regex extract first {...}
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            # Tier 4: trailing comma repair
            repaired = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                return json.loads(repaired)
            except Exception:
                return None
    return None


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Regex-extract table names from FROM/JOIN clauses."""
    if not sql:
        return []
    tables = set()
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_\.]*)", sql, re.IGNORECASE):
        t = m.group(1).split(".")[-1].strip().lower()
        if t and t not in {"select", "where", "group", "order", "having"}:
            tables.add(t)
    return sorted(tables)


def _cluster_qa_sklearn(qa_rows: list[dict]) -> Optional[list[list[int]]]:
    """TF-IDF + KMeans clustering with silhouette score k=2..6. Returns list of index clusters.

    scikit-learn (ML) has been removed from this build to shrink the image.
    This always returns None so the caller falls back to the table-bucket
    clustering in ``_cluster_qa_fallback``.
    """
    return None
    try:  # pragma: no cover - dead code, ML clustering disabled in this build
        texts = [(r.get("question") or "") + " " + (r.get("sql") or "") for r in qa_rows]
        if len(texts) < MIN_QUESTIONS_PER_CLUSTER * 2:
            return None
        vec = TfidfVectorizer(max_features=200, stop_words="english", ngram_range=(1, 2))
        X = vec.fit_transform(texts)
        if X.shape[0] < 4:
            return None
        best_k = 2
        best_score = -1.0
        best_labels = None
        max_k = min(6, X.shape[0] - 1)
        for k in range(2, max_k + 1):
            try:
                km = KMeans(n_clusters=k, n_init=5, random_state=42)
                labels = km.fit_predict(X)
                if len(set(labels)) < 2:
                    continue
                score = silhouette_score(X, labels)
                if score > best_score:
                    best_score = score
                    best_k = k
                    best_labels = labels
            except Exception:
                continue
        if best_labels is None:
            return None
        clusters: dict[int, list[int]] = {}
        for idx, lbl in enumerate(best_labels):
            clusters.setdefault(int(lbl), []).append(idx)
        return [v for v in clusters.values() if len(v) >= MIN_QUESTIONS_PER_CLUSTER]
    except Exception:
        return None


def _cluster_qa_fallback(qa_rows: list[dict]) -> list[list[int]]:
    """Bucket-by-first-table fallback when sklearn unavailable."""
    buckets: dict[str, list[int]] = {}
    for i, r in enumerate(qa_rows):
        tables = _extract_tables_from_sql(r.get("sql") or "")
        key = tables[0] if tables else "_misc"
        buckets.setdefault(key, []).append(i)
    return [v for v in buckets.values() if len(v) >= MIN_QUESTIONS_PER_CLUSTER]


def _build_cluster_prompt(
    project_slug: str,
    cluster_qa: list[dict],
    tables_touched: list[str],
    kpis_mentioned: list[str],
    catalog_summary: str,
) -> str:
    themes = [q.get("question", "")[:200] for q in cluster_qa[:3]]
    sample_sql = [q.get("sql", "")[:300] for q in cluster_qa[:2] if q.get("sql")]
    return f"""You are designing a specialist sub-agent for the data project '{project_slug}'.

Below is a cluster of related training Q&A. Propose ONE specialist sub-agent that
covers this cluster well.

QUESTION THEMES (top 3):
{json.dumps(themes, indent=2)}

SAMPLE SQL:
{json.dumps(sample_sql, indent=2)}

TABLES TOUCHED: {", ".join(tables_touched) if tables_touched else "(unknown)"}

KPIs/METRICS MENTIONED: {", ".join(kpis_mentioned) if kpis_mentioned else "(none)"}

TABLE CATALOG SUMMARY:
{catalog_summary[:1500]}

Return STRICT JSON only (no fences, no prose), schema:
{{
  "name": "snake_case_specialist_name (3-4 words max)",
  "purpose": "one sentence on when to use this sub-agent",
  "description": "2-3 sentences on what this sub-agent does",
  "persona": "one-line persona statement (e.g. 'Sales analytics expert focused on pipeline health')",
  "scoped_tools": ["subset of {ALLOWED_TOOLS}"],
  "agent_md": "markdown <2000 chars with exactly 4 sections: ## Scope, ## Tools, ## Examples, ## Out-of-scope"
}}
"""


def _dedup_name(name: str, existing_names: set[str]) -> bool:
    """Returns True if name is a duplicate (case-insensitive)."""
    return (name or "").strip().lower() in existing_names


def synthesize_subagents(project_slug: str, logger=None) -> dict:
    """
    Cluster training Q&A and propose 2-5 specialist sub-agents per project.
    Fail-soft: never raises. Returns dict with created/skipped/clusters/error.
    """
    result = {"created": 0, "skipped": 0, "clusters": 0, "error": None}

    # Feature config gate
    try:
        from dash.feature_config import get_feature_config
        cfg = get_feature_config(project_slug) or {}
        if not cfg.get("tools", {}).get("auto_subagent_synthesis", True):
            _safe_log(logger, "subagent synthesis disabled by feature config")
            result["reason"] = "disabled_by_config"
            return result
    except Exception as e:
        _safe_log(logger, f"⚠ feature_config import failed, assuming enabled: {str(e)[:80]}")

    try:
        from sqlalchemy import text
        from sqlalchemy.pool import NullPool
        from db.session import get_sql_engine
    except Exception as e:
        result["error"] = f"db import failed: {str(e)[:120]}"
        _safe_log(logger, f"✗ {result['error']}")
        return result

    engine = None
    try:
        engine = get_sql_engine()
    except Exception as e:
        result["error"] = f"engine init failed: {str(e)[:120]}"
        _safe_log(logger, f"✗ {result['error']}")
        return result

    try:
        # Step 1: fetch training Q&A
        qa_rows: list[dict] = []
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT question, sql, COALESCE(answer_template, '') AS answer "
                    "FROM public.dash_training_qa WHERE project_slug = :s "
                    "ORDER BY id DESC LIMIT :lim"
                ), {"s": project_slug, "lim": MAX_QA_ROWS}).mappings().all()
                qa_rows = [dict(r) for r in rows]
        except Exception as e:
            result["error"] = f"qa fetch failed: {str(e)[:120]}"
            _safe_log(logger, f"✗ {result['error']}")
            return result

        _safe_log(logger, f"sub-agent synthesis: fetched {len(qa_rows)} training Q&A rows")
        if len(qa_rows) < 5:
            result["reason"] = "insufficient_qa"
            _safe_log(logger, "· skipping: <5 Q&A rows")
            return result

        # Step 2: fetch table catalog
        catalog_lines: list[str] = []
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, metadata FROM public.dash_table_metadata "
                    "WHERE project_slug = :s LIMIT 50"
                ), {"s": project_slug}).mappings().all()
                for r in rows:
                    md = r.get("metadata") or {}
                    if isinstance(md, str):
                        try:
                            md = json.loads(md)
                        except Exception:
                            md = {}
                    desc = (md.get("description") or md.get("purpose") or "")[:120]
                    cols = md.get("table_columns") or md.get("columns") or []
                    col_names = []
                    if isinstance(cols, list):
                        for c in cols[:8]:
                            if isinstance(c, dict):
                                col_names.append(c.get("name", ""))
                            elif isinstance(c, str):
                                col_names.append(c)
                    catalog_lines.append(f"- {r['table_name']}: {desc} (cols: {', '.join(col_names)})")
        except Exception as e:
            _safe_log(logger, f"⚠ catalog fetch failed: {str(e)[:80]}")
        catalog_summary = "\n".join(catalog_lines) if catalog_lines else "(no catalog)"

        # Step 3: fetch brain KPIs/glossary
        brain_terms: list[str] = []
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM public.dash_company_brain "
                    "WHERE project_slug = :s AND category IN ('kpi','formula','glossary') "
                    "LIMIT 20"
                ), {"s": project_slug}).mappings().all()
                brain_terms = [r["name"] for r in rows if r.get("name")]
        except Exception as e:
            _safe_log(logger, f"⚠ brain fetch failed: {str(e)[:80]}")

        # Step 4: cluster
        clusters = _cluster_qa_sklearn(qa_rows)
        cluster_method = "tfidf-kmeans"
        if not clusters:
            clusters = _cluster_qa_fallback(qa_rows)
            cluster_method = "table-bucket-fallback"
        if not clusters:
            result["reason"] = "no_clusters"
            _safe_log(logger, "· no viable clusters formed")
            return result

        result["clusters"] = len(clusters)
        _safe_log(logger, f"clustered {len(qa_rows)} Q&A into {len(clusters)} groups via {cluster_method}")

        # Step 5+6: per-cluster LLM proposal
        try:
            from dash.settings import training_llm_call
        except Exception as e:
            result["error"] = f"llm import failed: {str(e)[:120]}"
            _safe_log(logger, f"✗ {result['error']}")
            return result

        # Fetch existing names for dedup
        existing_names: set[str] = set()
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name FROM public.dash_custom_agents WHERE project_slug = :s"
                ), {"s": project_slug}).mappings().all()
                existing_names = {(r["name"] or "").strip().lower() for r in rows}
        except Exception:
            # table may live in dash schema
            try:
                with engine.connect() as conn:
                    rows = conn.execute(text(
                        "SELECT name FROM dash.dash_custom_agents WHERE project_slug = :s"
                    ), {"s": project_slug}).mappings().all()
                    existing_names = {(r["name"] or "").strip().lower() for r in rows}
            except Exception as e:
                _safe_log(logger, f"⚠ existing-agents lookup failed: {str(e)[:80]}")

        llm_calls = 0
        proposals: list[dict] = []
        for ci, idx_list in enumerate(clusters):
            if llm_calls >= MAX_LLM_CALLS:
                _safe_log(logger, f"· hit MAX_LLM_CALLS={MAX_LLM_CALLS}, stopping")
                break
            if len(proposals) >= MAX_SUBAGENTS_PER_RUN:
                break

            cluster_qa = [qa_rows[i] for i in idx_list]
            tables_touched: set[str] = set()
            for q in cluster_qa:
                for t in _extract_tables_from_sql(q.get("sql") or ""):
                    tables_touched.add(t)
            kpis_mentioned: list[str] = []
            joined_text = " ".join((q.get("question") or "") for q in cluster_qa).lower()
            for term in brain_terms:
                if term and term.lower() in joined_text:
                    kpis_mentioned.append(term)

            prompt = _build_cluster_prompt(
                project_slug, cluster_qa, sorted(tables_touched), kpis_mentioned[:5], catalog_summary
            )

            parsed = None
            last_raw = ""
            for attempt in range(2):
                if llm_calls >= MAX_LLM_CALLS:
                    break
                try:
                    raw = training_llm_call(prompt, "extraction")
                    llm_calls += 1
                except Exception as e:
                    _safe_log(logger, f"⚠ cluster {ci} LLM call failed: {str(e)[:80]}")
                    break
                if not raw:
                    continue
                last_raw = raw
                parsed = _parse_json_4tier(raw)
                if parsed and isinstance(parsed, dict) and parsed.get("name"):
                    break
                parsed = None

            if not parsed:
                _safe_log(logger, f"⚠ cluster {ci}: LLM parse failed (raw len={len(last_raw)}), skipping")
                result["skipped"] += 1
                continue

            # Validate + sanitize
            name = (parsed.get("name") or "").strip()[:80]
            if not name:
                result["skipped"] += 1
                continue
            if _dedup_name(name, existing_names):
                _safe_log(logger, f"· cluster {ci}: '{name}' is duplicate, skipping")
                result["skipped"] += 1
                continue

            tools_raw = parsed.get("scoped_tools") or []
            if not isinstance(tools_raw, list):
                tools_raw = []
            scoped_tools = [t for t in tools_raw if t in ALLOWED_TOOLS][:len(ALLOWED_TOOLS)]

            agent_md = (parsed.get("agent_md") or "")[:2000]
            persona = (parsed.get("persona") or "")[:300]
            purpose = (parsed.get("purpose") or "")[:500]
            description = (parsed.get("description") or "")[:1000]

            proposals.append({
                "name": name,
                "purpose": purpose,
                "description": description,
                "persona": persona,
                "scoped_tools": scoped_tools,
                "agent_md": agent_md,
            })
            existing_names.add(name.lower())

        if not proposals:
            _safe_log(logger, "· no valid proposals generated")
            return result

        # Step 9: INSERT
        for p in proposals:
            inserted = False
            agent_id = f"auto_{uuid.uuid4().hex[:12]}"
            agent_md = p["agent_md"] or f"## Scope\n\n{p['purpose']}\n\n## Tools\n\n{', '.join(p['scoped_tools']) or '(none)'}\n\n## Examples\n\n(see project training Q&A)\n\n## Out-of-scope\n\nQuestions outside this agent's scope."
            last_err = ""
            for schema in ("dash", "public"):
                try:
                    with engine.begin() as conn:
                        conn.execute(text(
                            f"INSERT INTO {schema}.dash_custom_agents "
                            "(id, project_slug, name, description, purpose, base_agent, agent_md, persona, "
                            " scoped_skills, scoped_tools, source, usage_count, enabled, created_by_agent) "
                            "VALUES (:id, :slug, :name, :desc, :purpose, :base, :md, :persona, "
                            " CAST(:skills AS jsonb), CAST(:tools AS jsonb), 'auto_trained', 0, false, 'subagent_synthesis')"
                        ), {
                            "id": agent_id,
                            "slug": project_slug,
                            "name": p["name"],
                            "desc": p["description"],
                            "purpose": p["purpose"],
                            "base": "analyst",
                            "md": agent_md,
                            "persona": p["persona"],
                            "skills": json.dumps([]),
                            "tools": json.dumps(p["scoped_tools"]),
                        })
                    inserted = True
                    break
                except Exception as e:
                    last_err = str(e)[:200]
                    continue
            if inserted:
                result["created"] += 1
                _safe_log(logger, f"✓ created sub-agent '{p['name']}' (draft, enabled=false)")
            else:
                result["skipped"] += 1
                _safe_log(logger, f"⚠ insert failed for '{p['name']}': {last_err}")

        _safe_log(logger, f"sub-agent synthesis complete: created={result['created']} skipped={result['skipped']} clusters={result['clusters']}")
        return result

    except Exception as e:
        result["error"] = f"unexpected: {str(e)[:200]}"
        _safe_log(logger, f"✗ subagent_synthesis error: {result['error']}")
        return result
    finally:
        try:
            if engine is not None:
                # Don't dispose shared engine from get_sql_engine() — it's cached.
                pass
        except Exception:
            pass
