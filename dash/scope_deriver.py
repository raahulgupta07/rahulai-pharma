"""Scope deriver — auto-generate the agent's allowed/denied domain from project signals.

Called once during training (after persona/domain knowledge are built). Output is
persisted via `feature_config.set_scope()` and consumed by the guardrail layer
to refuse off-topic questions.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Wall-clock ceiling on any single blocking step inside scope derivation
# (primarily the LLM call which can hang on httpx if OpenRouter is unresponsive).
PER_STEP_TIMEOUT_S = 90

# Hard ceilings on output sizes — keep prompts small and refusals fast.
_MAX_TOPICS = 10
_MAX_ENTITIES = 10
_MAX_ALLOWED = 8
_MAX_DENIED = 8
_MAX_SIGNAL_CHARS = 3000  # total truncation budget for the LLM prompt body

_ALWAYS_DENY = ["general knowledge", "politics", "celebrities", "code generation"]


def _empty_scope() -> dict:
    """Default empty scope used when LLM call times out or crashes.

    Keeps the training pipeline moving — guardrail layer just won't refuse
    anything beyond the always-deny floor until the next training run.
    """
    return {
        "topics": [],
        "core_entities": [],
        "allowed_intents": [],
        "denied_intents": list(_ALWAYS_DENY),
        "refusal_message": (
            "I'm a focused data agent for this project — I can help with the data and "
            "documents you've loaded, but not general/off-topic questions."
        ),
        "_auto": False,
        "_derived_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Signal collection
# ─────────────────────────────────────────────────────────────────────────────
def _collect_signals(project_slug: str) -> dict:
    """Best-effort gather of every available source. Each source is independent —
    a failure in one never blocks the others."""
    from dash.tools.skill_refinery import _get_engine

    sig: dict[str, Any] = {
        "persona": {},
        "table_catalog": [],
        "doc_titles": [],
        "kg_entities": [],
        "glossary_terms": [],
        "memories_sample": [],
    }

    eng = _get_engine()

    # Persona — DB row first, then optional persona.json overlay
    try:
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT agent_name, agent_role, agent_personality FROM public.dash_projects WHERE slug = :s"
            ), {"s": project_slug}).fetchone()
            if row:
                sig["persona"] = {
                    "agent_name": row[0] or "",
                    "agent_role": row[1] or "",
                    "agent_personality": row[2] or "",
                }
    except Exception as e:
        logger.debug(f"persona DB fetch failed: {e}")

    try:
        from dash.paths import KNOWLEDGE_DIR
        pf = KNOWLEDGE_DIR / project_slug / "persona.json"
        if pf.exists():
            with open(pf) as f:
                pjson = json.load(f)
            if isinstance(pjson, dict):
                sig["persona"].setdefault("file", {})
                # keep the small bits
                for k in ("name", "role", "tagline", "domain", "expertise", "personality"):
                    if pjson.get(k):
                        sig["persona"]["file"][k] = pjson[k]
    except Exception as e:
        logger.debug(f"persona file overlay failed: {e}")

    # Table catalog (table_name + description + a handful of column names)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_name, metadata FROM public.dash_table_metadata "
                "WHERE project_slug = :s LIMIT 30"
            ), {"s": project_slug}).fetchall()
        for tbl, meta in rows:
            entry: dict[str, Any] = {"table": tbl}
            if isinstance(meta, dict):
                desc = meta.get("description") or meta.get("table_description") or ""
                if desc:
                    entry["desc"] = str(desc)[:200]
                cols = meta.get("table_columns") or []
                if isinstance(cols, list):
                    entry["columns"] = [
                        c.get("name") for c in cols[:12]
                        if isinstance(c, dict) and c.get("name")
                    ]
            sig["table_catalog"].append(entry)
    except Exception as e:
        logger.debug(f"table catalog fetch failed: {e}")

    # Document titles
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT filename FROM public.dash_documents WHERE project_slug = :s LIMIT 50"
            ), {"s": project_slug}).fetchall()
        sig["doc_titles"] = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.debug(f"doc titles fetch failed: {e}")

    # KG entities
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT subject, COUNT(*) c FROM public.dash_knowledge_triples "
                "WHERE project_slug = :s GROUP BY subject ORDER BY c DESC LIMIT 30"
            ), {"s": project_slug}).fetchall()
        sig["kg_entities"] = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.debug(f"kg entities fetch failed: {e}")

    # Glossary (Brain category='glossary', project-scoped)
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT name FROM public.dash_company_brain "
                "WHERE project_slug = :s AND category = 'glossary' LIMIT 50"
            ), {"s": project_slug}).fetchall()
        sig["glossary_terms"] = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.debug(f"glossary fetch failed: {e}")

    # Memories sample
    try:
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT fact FROM public.dash_memories WHERE project_slug = :s "
                "ORDER BY id DESC LIMIT 20"
            ), {"s": project_slug}).fetchall()
        sig["memories_sample"] = [r[0] for r in rows if r and r[0]]
    except Exception as e:
        logger.debug(f"memories fetch failed: {e}")

    return sig


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt + parsing
# ─────────────────────────────────────────────────────────────────────────────
def _format_signals(signals: dict) -> dict:
    """Compact signals into short string blobs, with a global character budget."""
    persona = signals.get("persona") or {}
    persona_str = json.dumps(persona, default=str)[:600]

    table_lines = []
    for t in signals.get("table_catalog", []):
        cols = ", ".join(t.get("columns", [])[:8])
        line = f"- {t.get('table', '')}"
        if t.get("desc"):
            line += f": {t['desc']}"
        if cols:
            line += f" [cols: {cols}]"
        table_lines.append(line)
    tables_str = "\n".join(table_lines)[:1000]

    docs_str = ", ".join(signals.get("doc_titles", []))[:400]
    kg_str = ", ".join(signals.get("kg_entities", []))[:400]
    gloss_str = ", ".join(signals.get("glossary_terms", []))[:400]

    mems_str = "\n".join(f"- {m}" for m in signals.get("memories_sample", []))[:600]

    blob = {
        "persona": persona_str,
        "tables": tables_str or "(none)",
        "docs": docs_str or "(none)",
        "kg": kg_str or "(none)",
        "glossary": gloss_str or "(none)",
        "memories": mems_str or "(none)",
    }
    # global cap
    total = sum(len(v) for v in blob.values())
    if total > _MAX_SIGNAL_CHARS:
        # Trim memories first, then tables
        over = total - _MAX_SIGNAL_CHARS
        if len(blob["memories"]) > over:
            blob["memories"] = blob["memories"][: max(100, len(blob["memories"]) - over)]
        else:
            over -= len(blob["memories"])
            blob["memories"] = ""
            blob["tables"] = blob["tables"][: max(200, len(blob["tables"]) - over)]
    return blob


_PROMPT = """You are SCOPE-DERIVER for a Dash data agent.

Input describes a project's data + docs + persona. Your job: derive the agent's domain so we can refuse off-topic questions.

PROJECT SIGNALS:
- Persona: {persona}
- Tables:
{tables}
- Document titles: {docs}
- Top KG entities: {kg}
- Glossary: {glossary}
- Sample memories:
{memories}

OUTPUT a single JSON object with these EXACT keys:
- topics (list[str], 5-10 short phrases describing what this agent handles)
- core_entities (list[str], 5-10 main nouns)
- allowed_intents (list[str], 5-8 specific user intents like "query stock levels", "compare stores")
- denied_intents (list[str], 5-8 things outside scope; ALWAYS include "general knowledge", "politics", "celebrities", "code generation")
- refusal_message (string, 1-2 sentences. Friendly, lists 3-4 example things user CAN ask)

Output ONLY the JSON object. No markdown fences, no commentary."""


def _extract_json(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    # Strip code fences
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    # Find first balanced-looking {...}
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        # try lenient: replace single quotes
        try:
            return json.loads(m.group(0).replace("'", '"'))
        except Exception:
            return None


def _call_llm(signals: dict) -> dict | None:
    try:
        from dash.settings import training_llm_call
    except Exception as e:
        logger.warning(f"settings import failed: {e}")
        return None

    blob = _format_signals(signals)
    prompt = _PROMPT.format(**blob)

    # Wall-clock timeout — training_llm_call uses httpx without a guaranteed
    # caller-side timeout, so a hung OpenRouter request can stall the whole
    # training pipeline. Run in a worker thread and bail after PER_STEP_TIMEOUT_S.
    #
    # NOTE: we do NOT use `with ThreadPoolExecutor() as ex:` here. The context
    # manager's __exit__ calls shutdown(wait=True), which blocks until the
    # submitted thread finishes — defeating the timeout when the thread is hung.
    # Instead we shut down with wait=False so a stalled httpx call can keep
    # running as a daemon worker in the background while the pipeline moves on.
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(training_llm_call, prompt, task="deep_analysis")
        try:
            raw = fut.result(timeout=PER_STEP_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            logger.warning(
                f"scope LLM call exceeded {PER_STEP_TIMEOUT_S}s timeout — "
                "abandoning and falling back to empty scope"
            )
            # Do NOT block on the hung thread — abandon it and return.
            ex.shutdown(wait=False, cancel_futures=True)
            return None
    except Exception as e:
        logger.warning(f"scope LLM call failed: {e}")
        return None
    finally:
        # Success/error path: release executor without blocking forever.
        # Safe/no-op if already shut down on the timeout path above.
        ex.shutdown(wait=False)

    if not raw:
        return None

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        logger.warning("scope LLM output unparseable")
        return None
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Validation / coercion
# ─────────────────────────────────────────────────────────────────────────────
def _norm_str_list(v: Any, cap: int) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in v:
        if not isinstance(item, str):
            try:
                item = str(item)
            except Exception:
                continue
        s = item.strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _validate_scope(d: dict | None) -> dict:
    d = d or {}
    topics = _norm_str_list(d.get("topics"), _MAX_TOPICS)
    core_entities = _norm_str_list(d.get("core_entities"), _MAX_ENTITIES)
    allowed_intents = _norm_str_list(d.get("allowed_intents"), _MAX_ALLOWED)
    denied_intents = _norm_str_list(d.get("denied_intents"), _MAX_DENIED)

    # Always-deny floor
    for must in _ALWAYS_DENY:
        if must not in denied_intents:
            if len(denied_intents) >= _MAX_DENIED:
                denied_intents.pop()
            denied_intents.append(must)

    refusal = d.get("refusal_message")
    if not isinstance(refusal, str) or not refusal.strip():
        refusal = (
            "I'm a focused data agent for this project — I can help with the data and "
            "documents you've loaded, but not general/off-topic questions."
        )
    refusal = refusal.strip()[:500]

    return {
        "topics": topics,
        "core_entities": core_entities,
        "allowed_intents": allowed_intents,
        "denied_intents": denied_intents,
        "refusal_message": refusal,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fallback (no LLM)
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_scope(signals: dict) -> dict:
    persona = signals.get("persona") or {}
    name = persona.get("agent_name") or persona.get("file", {}).get("name") or "this"
    role = persona.get("agent_role") or persona.get("file", {}).get("role") or "data agent"

    tables = [t.get("table", "") for t in signals.get("table_catalog", []) if t.get("table")]
    topics = [role.lower()] if role else []
    for t in tables[:6]:
        topics.append(str(t).replace("_", " ").lower())

    entities = [str(t).replace("_", " ").lower() for t in tables[:8]]

    sample_topics = ", ".join(tables[:3]) or "this project's data"
    refusal = (
        f"I'm {name} — your {role}. I can help with questions about "
        f"{sample_topics}, but not unrelated topics."
    )

    return _validate_scope({
        "topics": topics,
        "core_entities": entities,
        "allowed_intents": [
            "query the loaded tables",
            "summarize uploaded documents",
            "compare values across the data",
            "explain trends in the data",
        ],
        "denied_intents": list(_ALWAYS_DENY),
        "refusal_message": refusal,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def _write_run_progress(project_slug: str, step: str, progress: int) -> None:
    """Best-effort write of current_step + stage_progress to the active training run.

    Targets the most recent running training run for the project. Fail-soft —
    never raises, so scope derivation continues even if the columns don't
    exist (older DBs) or the table is locked.
    """
    try:
        from dash.tools.skill_refinery import _get_engine
        eng = _get_engine()
        with eng.connect() as conn:
            conn.execute(
                text(
                    "UPDATE public.dash_training_runs SET current_step = :s, "
                    "stage_progress = :p WHERE project_slug = :slug AND status = 'running'"
                ),
                {"s": step, "p": progress, "slug": project_slug},
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"_write_run_progress no-op: {e}")


def derive_scope(project_slug: str) -> dict:
    """Top-level: gather → LLM → validate → return.

    Never raises. On any failure (timeout, LLM error, signal collection
    crash), returns a fallback scope derived from persona + table names
    alone. On full crash, returns an empty default scope so the training
    pipeline can still move on.
    """
    try:
        _write_run_progress(project_slug, "scope_derivation", 0)

        try:
            signals = _collect_signals(project_slug)
        except Exception as e:
            logger.warning(f"scope signal collection failed for {project_slug}: {e}")
            signals = {}

        _write_run_progress(project_slug, "scope_derivation", 50)

        parsed = None
        try:
            parsed = _call_llm(signals)
        except Exception as e:
            logger.warning(f"scope LLM step crashed: {e}")

        _write_run_progress(project_slug, "scope_derivation", 100)

        if not parsed:
            return _fallback_scope(signals)

        return _validate_scope(parsed)
    except Exception as e:
        # Last-resort guard: derive_scope MUST NOT block training under any
        # circumstance. Log and return an empty default scope.
        logger.warning(
            f"derive_scope crashed unexpectedly for {project_slug}: {e} — "
            "falling back to empty default scope"
        )
        return _empty_scope()
