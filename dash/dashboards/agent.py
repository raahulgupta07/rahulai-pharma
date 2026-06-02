"""Analyst agent loop: deepen a base dashboard spec via LLM follow-ups + detectors."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator

import pandas as pd
from sqlalchemy import text

from dash.dashboards.insights import detect_all

logger = logging.getLogger(__name__)

# SQL-VALIDATED: central validator import (fail-soft)
try:
    from dash.tools.sql_validator import validate_and_fix as _sql_validate_and_fix  # type: ignore
    from dash.tools.llm_sql_helper import _postgres_sql_rules as _sql_pg_rules, get_schema_hint as _sql_schema_hint  # type: ignore
    _SQL_VALIDATOR_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    logger.warning(f"sql_validator unavailable, falling back to inline EXPLAIN: {_e}")
    _sql_validate_and_fix = None  # type: ignore
    _sql_pg_rules = None  # type: ignore
    _sql_schema_hint = None  # type: ignore
    _SQL_VALIDATOR_AVAILABLE = False


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\[.*\]|\{.*\}", s, re.DOTALL)
    return m.group(0) if m else s


def _run_sql(project_slug: str, sql: str) -> pd.DataFrame | None:
    """Execute read-only SELECT against project schema; return DataFrame or None."""
    if not sql or not sql.strip():
        return None
    if not re.match(r"^\s*(WITH|SELECT)\b", sql.strip(), re.IGNORECASE):
        return None
    try:
        from db.session import get_project_readonly_engine
        eng = get_project_readonly_engine(project_slug)
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:
        logger.debug(f"agent sql failed: {e}")
        return None


_FOLLOWUP_PROMPT = """You are an analyst extending a dashboard. Based on what we already explored, suggest follow-up questions that would surface NEW insights.

PROJECT: {slug}
PERSONA: {persona}
TABLES (name: purpose):
{tables}
ALREADY ASKED:
{history}
LATEST INSIGHTS:
{insights}

Output ONLY a JSON array of objects with EXACTLY these fields:
- question (string, specific & answerable from the tables)
- score (int 0-100 = impact * confidence * novelty, higher=better)
- sql_hint (string, a SELECT statement using ONLY tables above; LIMIT 200)

Rules: 3-6 items. Skip questions similar to ALREADY ASKED. Use only listed table names."""


class DashboardAgent:
    def __init__(self, project_slug: str, base_spec: dict, persona: str = "", budget: dict | None = None):
        self.project_slug = project_slug
        self.spec = dict(base_spec or {})
        self.spec.setdefault("cells", [])
        self.spec.setdefault("insights", [])
        self.persona = persona or self.spec.get("persona", "")
        self.history: list[str] = []
        self.insights: list[dict] = []
        self.budget = budget or {"rounds": 4, "cells": 12, "tokens_max": 50000}
        self.tokens_used = 0

    # ---------- internals ----------
    def _tables_str(self) -> str:
        try:
            from dash.tools.skill_refinery import _get_engine
            eng = _get_engine()
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, metadata FROM public.dash_table_metadata "
                    "WHERE project_slug=:s LIMIT 25"
                ), {"s": self.project_slug}).fetchall()
            lines = []
            for tbl, meta in rows:
                p = ""
                if isinstance(meta, dict):
                    p = str(meta.get("purpose") or meta.get("description") or "")[:120]
                lines.append(f"- {tbl}: {p}")
            return "\n".join(lines) or "(none)"
        except Exception:
            return "(none)"

    def _generate_followups(self, tables_str: str) -> list[dict]:
        prompt = _FOLLOWUP_PROMPT.format(
            slug=self.project_slug,
            persona=self.persona or "(none)",
            tables=tables_str[:1500],
            history="\n".join(f"- {h}" for h in self.history[-12:]) or "(none)",
            insights="\n".join(f"- {i.get('finding','')}" for i in self.insights[-6:]) or "(none)",
        )
        try:
            from dash.settings import training_llm_call
            raw = training_llm_call(prompt, task="extraction")
            self.tokens_used += len(prompt) // 4 + len(raw or "") // 4
        except Exception as e:
            logger.debug(f"followup LLM failed: {e}")
            return []
        if not raw:
            return []
        try:
            arr = json.loads(_strip_fences(raw))
            if not isinstance(arr, list):
                return []
            out = []
            for it in arr:
                if not isinstance(it, dict):
                    continue
                q = str(it.get("question", "")).strip()
                sql = str(it.get("sql_hint", "")).strip()
                score = int(it.get("score", 0) or 0)
                if q and sql:
                    out.append({"question": q, "sql_hint": sql, "score": score})
            return out
        except Exception as e:
            logger.debug(f"followup parse failed: {e}")
            return []

    def _add_insight_cells(self, insight: dict, df: pd.DataFrame, question: str, sql: str):
        cells = self.spec.setdefault("cells", [])
        if len(cells) >= self.budget["cells"]:
            return
        # row position = bottom of grid
        row = max((c.get("grid", [0, 0, 0, 0])[1] + c.get("grid", [0, 0, 0, 0])[3] for c in cells), default=0)
        banner_id = f"ins_{uuid.uuid4().hex[:6]}"
        chart_id = f"ch_{uuid.uuid4().hex[:6]}"
        sev = insight.get("severity", "medium")
        cells.append({
            "id": banner_id, "type": "insight",
            "grid": [0, row, 12, 1],
            "title": insight.get("finding", "")[:120],
            "config": {"severity": sev, "type": insight.get("type"), "evidence": insight.get("evidence", {}),
                       "question": question},
        })
        sc = insight.get("suggested_chart") or {}
        cells.append({
            "id": chart_id, "type": "chart",
            "grid": [0, row + 1, 6, 3],
            "title": question[:80],
            "config": {
                "chart_type": sc.get("type", "bar"),
                "x": sc.get("x"), "y": sc.get("y"),
                "sql": sql, "data_preview": df.head(20).to_dict(orient="records"),
            },
        })
        self.spec.setdefault("insights", []).append({
            **insight, "question": question, "sql": sql,
        })
        self.insights.append(insight)

    def _round(self) -> dict:
        tables_str = self._tables_str()
        cands = self._generate_followups(tables_str)
        # Filter: score>30, not duplicate of history
        seen = {h.lower() for h in self.history}
        cands = [c for c in cands if c["score"] > 30 and c["question"].lower() not in seen]
        cands.sort(key=lambda c: c["score"], reverse=True)
        cands = cands[:3]
        if not cands:
            return {"asked": 0, "insights": 0, "stopped": True}
        round_insights = 0
        for c in cands:
            self.history.append(c["question"])
            df = _run_sql(self.project_slug, c["sql_hint"])
            if df is None or df.empty:
                continue
            for ins in detect_all(df):
                if ins.get("severity") in ("high", "medium"):
                    self._add_insight_cells(ins, df, c["question"], c["sql_hint"])
                    round_insights += 1
                    if len(self.spec["cells"]) >= self.budget["cells"]:
                        break
            if len(self.spec["cells"]) >= self.budget["cells"]:
                break
        return {"asked": len(cands), "insights": round_insights, "stopped": False}

    # ---------- public ----------
    def run_sync(self) -> dict:
        rounds_done = 0
        for _ in range(self.budget["rounds"]):
            if self.tokens_used > self.budget["tokens_max"]:
                break
            if len(self.spec.get("cells", [])) >= self.budget["cells"]:
                break
            r = self._round()
            rounds_done += 1
            if r.get("stopped"):
                break
        return {
            "spec": self.spec,
            "insights": self.insights,
            "rounds": rounds_done,
            "tokens_used": self.tokens_used,
        }

    async def stream(self) -> AsyncGenerator[dict, None]:
        for rn in range(self.budget["rounds"]):
            if self.tokens_used > self.budget["tokens_max"] or len(self.spec.get("cells", [])) >= self.budget["cells"]:
                break
            yield {"type": "thinking", "round": rn + 1}
            before = len(self.spec.get("cells", []))
            r = self._round()
            for ins in self.insights[-r.get("insights", 0):] if r.get("insights") else []:
                yield {"type": "insight", "insight": ins}
            added = len(self.spec.get("cells", [])) - before
            if added:
                yield {"type": "cell_added", "count": added}
            if r.get("stopped"):
                break
        yield {"type": "done", "spec": self.spec, "insights": self.insights, "tokens_used": self.tokens_used}


# ============================================================
# DeepDashAgent — 9-stage Pydantic-typed pipeline
# ============================================================
#
#   1 Intent           Haiku 4.5      DashboardIntent
#   2 Schema RAG       pgvector       SchemaContext (top-k tables + samples)
#   3 Panel Plan       Sonnet 4.7     list[PanelPlan]
#   4 SQL Gen          Sonnet 4.7     list[PanelSQL] (parallel via asyncio)
#   5 EXPLAIN Gate     Postgres       no LLM, rejects bad SQL, retry once
#   6 Execute+Profile  Python         list[PanelData]
#   7 Chart Spec Gen   Sonnet 4.7     list[EChartsPanelSpec] (Pydantic validated)
#   8 Judge            DIFFERENT MODEL (Gemini/GPT)  Critique + JsonPatchOp
#   9 Layout           Sonnet 4.7     DeepDashSpec (12-col grid + mobile breakpoints)
#
# Iteration: chat edit → router → is_edit=True → apply JsonPatchOp to panel,
# never full rebuild.

import asyncio
import time
from sqlalchemy.exc import SQLAlchemyError

from dash.dashboards.spec import (
    DashboardIntent,
    SchemaContext,
    TableContext,
    PanelPlan,
    PanelSQL,
    PanelData,
    EChartsPanelSpec,
    Critique,
    CritiqueIssue,
    JsonPatchOp,
    DeepDashSpec,
)


# ------------------------------------------------------------
# Skill prompt loader (Option B wiring)
# Loads skill `instructions` text from dash_skills registry. TTL-cached so each
# pipeline run hits DB at most once per skill. Returns "" silently if missing
# so hardcoded prompts remain authoritative fallback.
# ------------------------------------------------------------
_SKILL_CACHE: dict[tuple[str, str], tuple[float, str, int]] = {}
_SKILL_TTL_S = 300.0  # 5 min — refresh if SkillRefinery edits via UI


def invalidate_skill_cache(skill_id: str | None = None) -> int:
    """Drop cached skill-prefix entries so subsequent reads pick up fresh
    instructions from the registry. If `skill_id` is given, evicts only that
    entry; otherwise clears the entire cache. Returns the number of entries
    removed. Safe to call from any thread (single GIL-protected dict op).

    Wired into `dash/skills/registry.py::register_skill` so SkillRefinery /
    marketplace edits take effect immediately, and exposed via
    POST /api/admin/skills/cache/invalidate for ad-hoc super-admin flushes.
    """
    global _SKILL_CACHE
    if skill_id is None:
        n = len(_SKILL_CACHE)
        _SKILL_CACHE = {}
        return n
    # Evict every cache key matching this skill_id regardless of project_slug.
    keys = [k for k in _SKILL_CACHE if k[1] == skill_id]
    for k in keys:
        _SKILL_CACHE.pop(k, None)
    return len(keys)


# Thread-local-ish (per-run) tracker: skill_id -> version used.
# Populated as a side-effect of _skill_prefix() so _persist_dashboard_audit()
# at end-of-run can record skill_versions accurately. Best-effort; reset by
# the orchestrator at the start of each run.
_SKILL_VERSIONS_RUN: dict[str, int] = {}


def _reset_skill_versions_for_run() -> None:
    """Clear the per-run skill_versions tracker. Called at orchestrator start."""
    _SKILL_VERSIONS_RUN.clear()


def _skill_prefix(skill_id: str, project_slug: str | None = None) -> str:
    """Return formatted skill-instruction preamble, or '' if not found.

    Resolution order:
      1. Per-tenant override in `public.dash_skill_overrides` (scoped by
         project_slug). Lets a customer ship a hand-tuned variant of a
         pipeline skill without touching the global registry.
      2. Global `dash_skills` registry entry (default for all tenants).

    Cache key includes project_slug so per-tenant overrides never bleed into
    other tenants. Side-effects: records skill_id -> version used into
    `_SKILL_VERSIONS_RUN` for end-of-run audit persistence.
    """
    import time as _t
    now = _t.time()
    cache_key = ((project_slug or ""), skill_id)
    cached = _SKILL_CACHE.get(cache_key)
    if cached and now - cached[0] < _SKILL_TTL_S:
        # Re-record version on cache hit so audit captures all skills used.
        if cached[2]:
            _SKILL_VERSIONS_RUN[skill_id] = cached[2]
        return cached[1]
    text_block = ""
    version_int = 0

    # Helper: preprocess skill instructions (substitute ${VAR}, run !`SELECT...`).
    # Fail-soft — returns raw text on any error.
    def _prep(raw: str) -> str:
        try:
            from dash.skills.preprocess import preprocess as _pp
            return _pp(raw, project_slug=project_slug)
        except Exception:
            return raw

    # Layer 1 — per-tenant override beats global registry.
    if project_slug:
        try:
            from dash.learning.skill_refinery_cycle import _get_skill_override
            override_ins = _get_skill_override(project_slug, skill_id)
            if override_ins and override_ins.strip():
                processed = _prep(override_ins.strip())
                text_block = f"# SKILL: {skill_id} (project override)\n{processed[:2500]}\n\n---\n\n"
                # Override versions aren't tracked in the global registry;
                # use -1 sentinel so audit can distinguish overridden skills.
                version_int = -1
        except Exception as e:
            logger.debug(f"_get_skill_override({project_slug}, {skill_id}) failed: {e}")

    # Layer 2 — fall through to the global skill registry.
    if not text_block:
        try:
            from dash.skills.registry import load_skill
            # Issue #13: pipeline calls are predictable (one per stage per build);
            # stage-level audit already lives in dash_training_runs.
            res = load_skill(skill_id, audit=False)
            if res.get("ok") is not False:
                ins = (res.get("instructions") or "").strip()
                if ins:
                    processed = _prep(ins)
                    # Trim to avoid context bloat; full prompt + ours still ~< budget.
                    text_block = f"# SKILL: {skill_id}\n{processed[:2500]}\n\n---\n\n"
                try:
                    version_int = int(res.get("version") or 0)
                except Exception:
                    version_int = 0
        except Exception as e:
            logger.debug(f"_skill_prefix({skill_id}) failed: {e}")

    _SKILL_CACHE[cache_key] = (now, text_block, version_int)
    if version_int:
        _SKILL_VERSIONS_RUN[skill_id] = version_int
    return text_block


# ============================================================
# Telemetry persistence — per-stage skill runs + per-dashboard audit.
# Fail-soft: any DB error logs at DEBUG and the pipeline continues.
# ============================================================

def _persist_skill_run(
    project_slug: str,
    dashboard_id: str,
    skill_id: str,
    skill_version: int,
    stage: str,
    panel_count: int = 0,
    verified_cell_count: int = 0,
    judge_score: int | None = None,
    latency_ms: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Append one row to `public.dash_dashboard_skill_runs` per stage execution.

    Fail-soft. Writes via `get_write_engine()` (NEVER `get_sql_engine` — that's
    read-only on public). Used by stage 3/4/5/6/7/7.5/8 + refine/announce to
    feed SkillRefinery the per-stage telemetry it needs to close the loop on
    skill quality scoring.
    """
    if not skill_id:
        return
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_dashboard_skill_runs ("
                "  project_slug, dashboard_id, skill_id, skill_version, stage, "
                "  panel_count, verified_cell_count, judge_score, latency_ms, "
                "  cost_usd, ran_at"
                ") VALUES ("
                "  :slug, :did, :sid, :sv, :stage, :pc, :vc, :js, :lat, :cost, NOW()"
                ")"
            ), {
                "slug": project_slug or "",
                "did": dashboard_id or "",
                "sid": skill_id,
                "sv": int(skill_version or 0),
                "stage": (stage or "")[:64],
                "pc": int(panel_count or 0),
                "vc": int(verified_cell_count or 0),
                "js": (int(judge_score) if judge_score is not None else None),
                "lat": int(latency_ms or 0),
                "cost": float(cost_usd or 0.0),
            })
    except Exception as e:
        logger.debug(f"_persist_skill_run({skill_id}, {stage}) failed: {e}")


def _persist_dashboard_audit(
    dashboard_id: str,
    skill_versions: dict,
    verified_cell_pct: float,
) -> None:
    """Append one row to `public.dash_dashboard_audit` per finished run.

    `skill_versions` = {skill_id: version_int} captured from `_skill_prefix`
    calls during the run. `verified_cell_pct` = percent of EChartsPanelSpec
    cells where `verified is True`.

    Fail-soft. Uses `CAST(:m AS jsonb)` for the JSONB column (never `:m::jsonb`
    — see CLAUDE.md PgBouncer rule).
    """
    try:
        from db.session import get_write_engine
        eng = get_write_engine()
        payload = json.dumps(skill_versions or {})
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_dashboard_audit ("
                "  dashboard_id, skill_versions, verified_cell_pct, created_at"
                ") VALUES ("
                "  :did, CAST(:m AS jsonb), :pct, NOW()"
                ")"
            ), {
                "did": dashboard_id or "",
                "m": payload,
                "pct": float(verified_cell_pct or 0.0),
            })
    except Exception as e:
        logger.debug(f"_persist_dashboard_audit({dashboard_id}) failed: {e}")


def _llm(prompt: str, task: str = "extraction", model: str | None = None) -> str:
    """Single LLM helper. task routes to settings.TRAINING_CONFIGS; model overrides."""
    try:
        from dash.settings import training_llm_call
        kwargs: dict[str, Any] = {"task": task}
        if model:
            kwargs["model"] = model
        return training_llm_call(prompt, **kwargs) or ""
    except Exception as e:
        logger.warning(f"llm call failed (task={task}): {e}")
        return ""


def _parse_json(raw: str, fallback: Any = None) -> Any:
    try:
        return json.loads(_strip_fences(raw))
    except Exception:
        return fallback


class DeepDashAgent:
    """9-stage dashboard pipeline. Pydantic contracts at every stage.

    Different-model judge at stage 8 kills self-bias (TACL paper).
    EXPLAIN gate at stage 5 kills hallucinated columns (Wren pattern).
    JSON Patch at edit time kills full-regen drift (v0/Lovable failure mode).
    """

    def __init__(
        self,
        project_slug: str,
        question: str,
        *,
        persona: str = "",
        audience: str = "executive",
        n_panels: int = 8,
        gen_model: str | None = None,        # stages 3,4,7,9
        judge_model: str | None = None,      # stage 8 — MUST differ from gen_model
        budget: dict | None = None,
        session_id: str | None = None,       # chat session linkage (versioning)
        force_rebuild: bool = False,         # ignored at agent layer; honored by API
        signature_hash: str | None = None,   # #15 — question+schema fingerprint for cache rebuild
    ):
        self.project_slug = project_slug
        self.question = question
        self.persona = persona
        self.audience = audience
        self.n_panels = n_panels
        self.gen_model = gen_model
        self.judge_model = judge_model
        self.budget = budget or {"tokens_max": 80_000, "wall_s": 120, "panels_max": 12}
        self.session_id = session_id
        self.force_rebuild = bool(force_rebuild)
        self.signature_hash = signature_hash

        # Stage outputs (populated as pipeline runs)
        self.intent: DashboardIntent | None = None
        self.schema_ctx: SchemaContext | None = None
        self.plans: list[PanelPlan] = []
        self.sqls: list[PanelSQL] = []
        self.data: list[PanelData] = []
        self.panel_specs: list[EChartsPanelSpec] = []
        self.critique: Critique | None = None
        self.final: DeepDashSpec | None = None

        self.tokens_used = 0
        self.t0 = time.time()

        # Stable run-id used by _persist_skill_run + _persist_dashboard_audit.
        # The frontend may later overwrite with its own dashboard_id on save;
        # this just keeps every per-stage row in dash_dashboard_skill_runs
        # joinable to the same audit row.
        self.dashboard_id: str = f"deepdash_{uuid.uuid4().hex[:12]}"

    # ---------- versioned persist helper ----------
    def _persist_versioned(self, final_dump: dict) -> None:
        """Insert a NEW row per build (no upsert). If session_id present,
        compute next version + parent_id from latest row in same session.
        Fail-soft: logs on any exception, never raises.
        """
        try:
            from sqlalchemy import text as _sa_text
            from db.session import get_write_engine as _get_wen
            import json as _json

            # Compute label: 80-char prefix of executive_overview narrative if available,
            # else first chat question, else 'Dashboard'.
            label = "Dashboard"
            try:
                narr = getattr(self, "_narrative", None) or {}
                txt = ""
                if isinstance(narr, dict):
                    txt = str(narr.get("text") or "")
                if not txt and isinstance(final_dump, dict):
                    txt = str((final_dump.get("narrative") or {}).get("text") or "")
                if not txt:
                    txt = str(self.question or "")
                if txt:
                    label = txt.strip()[:80] or "Dashboard"
            except Exception:
                pass

            version = 1
            parent_id: str | None = None
            sid = self.session_id

            _eng = _get_wen()
            with _eng.begin() as _conn:
                if sid:
                    row = _conn.execute(_sa_text(
                        "SELECT id, COALESCE(MAX(version), 0) AS v "
                        "FROM public.dash_dashboards_v2 "
                        "WHERE project_slug=:p AND session_id=:s "
                        "GROUP BY id ORDER BY MAX(version) DESC LIMIT 1"
                    ), {"p": self.project_slug, "s": sid}).fetchone()
                    if row is not None:
                        parent_id = row[0]
                        version = int(row[1]) + 1
                _conn.execute(_sa_text(
                    "INSERT INTO public.dash_dashboards_v2 "
                    "(id, project_slug, spec, created_at, session_id, version, parent_id, label, signature_hash) "
                    "VALUES (:id, :slug, CAST(:spec AS JSONB), NOW(), :sid, :ver, :par, :lbl, :sig)"
                ), {
                    "id": self.dashboard_id,
                    "slug": self.project_slug,
                    "spec": _json.dumps(final_dump, default=str),
                    "sid": sid,
                    "ver": version,
                    "par": parent_id,
                    "lbl": label,
                    "sig": getattr(self, "signature_hash", None),
                })
        except Exception as _exc:
            logger.warning("dash_dashboards_v2 versioned persist failed for %s: %s", self.dashboard_id, _exc)

    # ---------- skill telemetry helpers ----------
    def _sv(self, skill_id: str) -> int:
        """Return the version captured for `skill_id` during this run, or 0."""
        return int(_SKILL_VERSIONS_RUN.get(skill_id, 0))

    def _compute_verified_cell_pct(self) -> float:
        """Percent of EChartsPanelSpec panels marked verified=True."""
        cells = self.panel_specs or []
        if not cells:
            return 0.0
        verified = 0
        for c in cells:
            v = getattr(c, "verified", None)
            if v is True:
                verified += 1
        return round((verified / len(cells)) * 100.0, 2)

    # ---------- guards ----------
    def _over_budget(self) -> bool:
        if self.tokens_used > self.budget["tokens_max"]:
            return True
        if time.time() - self.t0 > self.budget["wall_s"]:
            return True
        return False

    # ---------- stage 1 ----------
    def stage1_intent(self) -> DashboardIntent:
        prompt = f"""Classify dashboard request. Output JSON with fields:
question (string, restate user ask), audience (executive/analyst/operator/general),
n_panels_target (int 4-12), time_window (string, e.g. "last 90 days" or ""),
domain_hints (list of business domain keywords), is_edit (bool, true if user is
modifying an existing dashboard panel), target_panel_id (string or null).

User: {self.question}
Persona: {self.persona or "(none)"}

JSON only."""
        raw = _llm(prompt, task="extraction")
        self.tokens_used += (len(prompt) + len(raw)) // 4
        parsed = _parse_json(raw, {}) or {}
        try:
            self.intent = DashboardIntent(**parsed)
        except Exception:
            self.intent = DashboardIntent(question=self.question, audience=self.audience,  # type: ignore[arg-type]
                                          n_panels_target=self.n_panels)
        if not self.intent.question:
            self.intent.question = self.question
        return self.intent

    # ---------- stage 2 ----------
    def stage2_schema_rag(self) -> SchemaContext:
        """Top-k tables via pgvector + Codex-enriched metadata + sample rows."""
        tables: list[TableContext] = []
        glossary: dict[str, str] = {}
        aliases: dict[str, str] = {}
        try:
            from dash.tools.skill_refinery import _get_engine
            eng = _get_engine()
            with eng.connect() as conn:
                rows = conn.execute(text(
                    "SELECT table_name, metadata FROM public.dash_table_metadata "
                    "WHERE project_slug=:s LIMIT 12"
                ), {"s": self.project_slug}).fetchall()
                for tbl, meta in rows:
                    cols: list[dict] = []
                    if isinstance(meta, dict):
                        for c in (meta.get("columns") or [])[:30]:
                            if isinstance(c, dict):
                                cols.append({"name": c.get("name", ""),
                                             "dtype": c.get("type") or c.get("dtype", ""),
                                             "semantic": c.get("description", "")[:120]})
                    purpose = (meta or {}).get("purpose") or (meta or {}).get("description") or ""
                    sample = _run_sql(self.project_slug, f"SELECT * FROM {tbl} LIMIT 3")
                    tables.append(TableContext(
                        name=tbl, purpose=str(purpose)[:240],
                        columns=cols,
                        sample_rows=sample.to_dict("records") if sample is not None else [],
                    ))
                # Brain glossary + aliases (Layer 13)
                brain_rows = conn.execute(text(
                    "SELECT category, name, definition FROM public.dash_company_brain "
                    "WHERE (project_slug=:s OR project_slug IS NULL) "
                    "AND category IN ('glossary','alias') LIMIT 80"
                ), {"s": self.project_slug}).fetchall()
                for cat, name, defn in brain_rows:
                    if cat == "glossary":
                        glossary[name] = (defn or "")[:200]
                    else:
                        aliases[name] = (defn or "")[:120]
        except SQLAlchemyError as e:
            logger.warning(f"schema RAG (metadata path) failed: {e}")

        # Always query information_schema for live dtypes. Codex metadata is rich on
        # purpose/semantics but often lacks live dtypes (or is stale). Live dtypes are
        # required by SQL gen to emit correct casts (e.g. text→date for TEXT columns).
        # If no tables came from metadata path, this also bootstraps the table list.
        try:
            from db.session import get_project_readonly_engine
            eng2 = get_project_readonly_engine(self.project_slug)
            with eng2.connect() as conn:
                if not tables:
                    tbl_rows = conn.execute(text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema=:s AND table_type='BASE TABLE' "
                        "ORDER BY table_name LIMIT 12"
                    ), {"s": self.project_slug}).fetchall()
                    for (tbl,) in tbl_rows:
                        tables.append(TableContext(name=tbl, purpose="(no codex metadata)"))
                # For every table (metadata or freshly-discovered), populate dtypes
                # from information_schema if cols list is empty or missing dtype.
                for tc in tables:
                    needs = (not tc.columns) or any(not c.get("dtype") for c in tc.columns)
                    if not needs:
                        continue
                    col_rows = conn.execute(text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema=:s AND table_name=:t ORDER BY ordinal_position LIMIT 30"
                    ), {"s": self.project_slug, "t": tc.name}).fetchall()
                    live = {n: d for n, d in col_rows}
                    if tc.columns:
                        for c in tc.columns:
                            if not c.get("dtype") and c.get("name") in live:
                                c["dtype"] = live[c["name"]]
                        # Append any cols present in DB but missing from metadata
                        known = {c["name"] for c in tc.columns}
                        for n, d in col_rows:
                            if n not in known:
                                tc.columns.append({"name": n, "dtype": d, "semantic": ""})
                    else:
                        tc.columns = [{"name": n, "dtype": d, "semantic": ""} for n, d in col_rows]
                    if tc.row_count is None:
                        try:
                            rc = conn.execute(text(
                                f'SELECT COUNT(*) FROM "{self.project_slug}"."{tc.name}"'
                            )).scalar()
                            tc.row_count = int(rc) if rc is not None else None
                        except Exception:
                            pass
                    if not tc.sample_rows:
                        sample = _run_sql(self.project_slug, f'SELECT * FROM "{tc.name}" LIMIT 3')
                        if sample is not None:
                            tc.sample_rows = sample.to_dict("records")
        except Exception as e:
            logger.warning(f"schema RAG (information_schema enrich) failed: {e}")

        self.schema_ctx = SchemaContext(tables=tables, glossary=glossary, aliases=aliases)
        return self.schema_ctx

    # ---------- stage 3 ----------
    def stage3_panel_plan(self) -> list[PanelPlan]:
        if not self.intent or not self.schema_ctx:
            return []
        tables_blob = "\n".join(
            f"- {t.name} ({t.row_count or '?'} rows): {t.purpose[:140]}\n"
            f"  cols: {', '.join(c['name'] + ':' + str(c.get('dtype') or '') for c in t.columns[:20])}"
            for t in self.schema_ctx.tables
        )
        # skl_dash_orchestrator = pipeline-side instructions (separate from skl_dash_builder
        # which is Leader-facing redirect skill).
        prompt = _skill_prefix("skl_dash_orchestrator", self.project_slug) + f"""Decompose dashboard into {self.intent.n_panels_target} panels.
Each panel = one specific sub-question answerable from one or more tables below.

USER QUESTION: {self.intent.question}
AUDIENCE: {self.intent.audience}
TIME WINDOW: {self.intent.time_window or "(any)"}

TABLES:
{tables_blob}

GLOSSARY (top): {", ".join(list((self.schema_ctx.glossary or {}).keys())[:10])}

Output JSON array. Each item:
- id (string, panel_<6hex>)
- title (action-title: full-sentence insight, not topic)
- question (sub-question)
- panel_type (kpi/chart/table/insight/narrative)
- chart_type (bar/line/pie/scatter/area/grouped_bar/stacked_bar/histogram/heatmap/gauge/sankey/treemap/funnel/boxplot/radar/candlestick) — for chart panels
- metrics (list of column names)
- dimensions (list of column names)
- filters (list of POSTGRES filter expressions, e.g. "sale_date >= CURRENT_DATE - INTERVAL '30 days'", NEVER MySQL DATE_SUB)
- tables_used (list of table names from above)
- priority (int 0-100, higher = more important for audience)

HARD QUOTA — your plan MUST include:
  ≥ 3 KPI panels (panel_type='kpi', single big number with optional delta)
  ≥ 4 chart panels (panel_type='chart', chart_type in: bar/line/pie/stacked_bar/heatmap/scatter)
  ≥ 1 table panel (panel_type='table', top-N rows)
  ≤ 2 insight panels (panel_type='insight', narrative ONLY for executive summary)
  TOTAL: 8-12 panels

CHART TYPE VARIETY — use at least 4 different chart_types across the chart panels:
  • bar: categorical comparison (top 5 channels by revenue)
  • line: time series (monthly trend)
  • pie: distribution / share (channel mix %)
  • stacked_bar: composition over time (calls by outcome per month)
  • heatmap: 2D correlation (hour-of-day x day-of-week)
  • scatter: 2 numeric metrics (calls vs revenue per outlet)

EVERY non-insight panel MUST have:
  • metrics: list of metric refs (column names or aggregates)
  • dimensions: list of dimension refs (for charts/tables)
  • tables_used: at least one real table from the list above

NO panel may have empty metrics/dimensions/tables AND non-insight type. If you can't
write SQL for a panel idea, convert it to an insight panel OR drop it.

Rules: Start with KPI strip (3-5 kpi panels). Then charts. End with 0-2 insight/narrative.
Use ONLY listed table names. JSON only."""

        def _parse_plans(raw_text: str) -> list[PanelPlan]:
            arr = _parse_json(raw_text, []) or []
            out: list[PanelPlan] = []
            for item in arr if isinstance(arr, list) else []:
                try:
                    out.append(PanelPlan(**item))
                except Exception as e:
                    logger.debug(f"panel plan skip: {e}")
            return out

        raw = _llm(prompt, task="dashboard_gen", model=self.gen_model)
        self.tokens_used += (len(prompt) + len(raw)) // 4
        plans = _parse_plans(raw)

        # Quota validation + ONE retry on violation
        def _panel_quota_issues(pl: list[PanelPlan]) -> list[str]:
            types = [str(getattr(p, "panel_type", "")).lower() for p in pl]
            n_kpi = sum(1 for t in types if t == "kpi")
            n_chart = sum(1 for t in types if t == "chart")
            n_insight = sum(1 for t in types if t == "insight")
            issues = []
            if n_kpi < 3: issues.append(f"only {n_kpi} KPIs (need ≥3)")
            if n_chart < 4: issues.append(f"only {n_chart} charts (need ≥4)")
            if n_insight > 2: issues.append(f"{n_insight} insights (max 2)")
            return issues

        issues = _panel_quota_issues(plans)
        if issues:
            logger.info(f"panel quota violated: {issues}; requesting retry")
            retry_prompt = prompt + (
                f"\n\nYour previous plan violated the HARD QUOTA: {', '.join(issues)}. "
                "Re-emit the full JSON array with the correct mix of kpi/chart/table/insight panels."
            )
            try:
                raw2 = _llm(retry_prompt, task="dashboard_gen", model=self.gen_model)
                self.tokens_used += (len(retry_prompt) + len(raw2)) // 4
                plans2 = _parse_plans(raw2)
                if plans2 and len(_panel_quota_issues(plans2)) <= len(issues):
                    plans = plans2
                    issues = _panel_quota_issues(plans)
            except Exception as e:
                logger.warning(f"panel quota retry failed: {e}")

        # Force-drop excess insights if still over quota (fail-soft)
        if issues:
            kept: list[PanelPlan] = []
            n_insight_kept = 0
            for p in plans:
                pt = str(getattr(p, "panel_type", "")).lower()
                if pt == "insight":
                    if n_insight_kept >= 2:
                        logger.info(f"force-dropping excess insight panel {p.id}")
                        continue
                    n_insight_kept += 1
                kept.append(p)
            plans = kept

        # Filter non-insight panels with no metrics/dimensions/tables_used
        filtered: list[PanelPlan] = []
        for p in plans:
            pt = str(getattr(p, "panel_type", "")).lower()
            if pt == "insight":
                filtered.append(p)
                continue
            if not (p.metrics or p.dimensions or p.tables_used):
                logger.info(f"dropping panel {p.id} ({pt}): no metrics/dimensions/tables defined")
                continue
            filtered.append(p)
        plans = filtered

        # Phase 9 DQ gate: skip / demote time-axis panels where the chosen date
        # column is effectively constant (1-period data → meaningless time series).
        # See dash/utils/column_classifier.is_constant_column + CityPharma created_at
        # gotcha. Demote to KPI if all candidate date cols are constant; else
        # let SQL-gen pick a non-constant date col downstream.
        try:
            from dash.utils.column_classifier import is_constant_column
            from db.session import get_project_readonly_engine as _eng_for_gate
            _date_dtypes = {"date", "timestamp", "timestamp without time zone",
                            "timestamp with time zone", "timestamptz"}
            _date_name_re = re.compile(r"(date|time|_at$|_dt$|month|year|period)", re.IGNORECASE)
            # Build {table: [date_col, ...]} candidates from schema_ctx
            _tbl_date_cols: dict[str, list[str]] = {}
            for _t in (self.schema_ctx.tables if self.schema_ctx else []):
                _dcs = []
                for _c in (_t.columns or []):
                    _dt = str(_c.get("dtype") or "").lower()
                    _nm = str(_c.get("name") or "")
                    if _dt in _date_dtypes or _date_name_re.search(_nm):
                        _dcs.append(_nm)
                if _dcs:
                    _tbl_date_cols[_t.name] = _dcs
            if _tbl_date_cols:
                _gate_eng = _eng_for_gate(self.project_slug)
                with _gate_eng.connect() as _gconn:
                    _const_cache: dict[tuple, bool] = {}
                    def _all_dates_constant(_tbl: str) -> bool:
                        _cols = _tbl_date_cols.get(_tbl) or []
                        if not _cols:
                            return False
                        for _col in _cols:
                            _k = (_tbl, _col)
                            if _k not in _const_cache:
                                _const_cache[_k] = is_constant_column(_gconn, self.project_slug, _tbl, _col)
                            if not _const_cache[_k]:
                                return False  # at least one usable
                        return True

                    _demoted: list[PanelPlan] = []
                    for _p in plans:
                        _ct = str(getattr(_p, "chart_type", "") or "").lower()
                        _pt = str(getattr(_p, "panel_type", "") or "").lower()
                        _is_time_axis = _pt == "chart" and _ct in ("line", "area", "stacked_bar")
                        if _is_time_axis and _p.tables_used:
                            _tbls = _p.tables_used or []
                            if all(_all_dates_constant(_t) for _t in _tbls if _t in _tbl_date_cols):
                                logger.info(
                                    f"time-axis gate: panel {_p.id} ({_ct}) on "
                                    f"tables={_tbls} — all date cols constant → demoting to kpi"
                                )
                                try:
                                    _p.panel_type = "kpi"
                                    _p.chart_type = None
                                except Exception:
                                    pass
                        _demoted.append(_p)
                    plans = _demoted
        except Exception as _ex:
            logger.debug(f"time-axis constant-date gate skipped: {_ex}")

        # Cap to budget
        plans = plans[: self.budget["panels_max"]]
        self.plans = plans
        return plans

    # ---------- stage 4 ----------
    def stage4_sql_gen(self) -> list[PanelSQL]:
        sqls: list[PanelSQL] = []
        if not self.schema_ctx:
            return sqls
        for plan in self.plans:
            if plan.panel_type in ("narrative", "insight") and not plan.tables_used:
                sqls.append(PanelSQL(panel_id=plan.id, sql=""))
                continue
            tbl_blob = "\n".join(
                f"- {t.name}: {', '.join(c['name']+':'+c['dtype'] for c in t.columns[:20])}"
                for t in self.schema_ctx.tables if t.name in plan.tables_used
            ) or "(no tables matched)"
            # SQL-VALIDATED: inject central PG rules + schema hint at top of prompt
            _rules_blob = ""
            _schema_blob = ""
            if _SQL_VALIDATOR_AVAILABLE:
                try:
                    _rules_blob = (_sql_pg_rules() or "") + "\n\n"
                except Exception:
                    _rules_blob = ""
                try:
                    _schema_blob = (_sql_schema_hint(self.project_slug) or "") + "\n\n"
                except Exception:
                    _schema_blob = ""
            prompt = f"""{_rules_blob}{_schema_blob}Generate ONE Postgres SELECT statement for the panel below.

PANEL: {plan.title}
QUESTION: {plan.question}
METRICS: {plan.metrics}
DIMENSIONS: {plan.dimensions}
FILTERS: {plan.filters}
CHART TYPE: {plan.chart_type}

TABLES (use only these):
{tbl_blob}

Rules:
- POSTGRESQL DIALECT ONLY. Do not use MySQL/BigQuery syntax.
  WRONG (MySQL):   DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  WRONG (BigQuery): DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
  CORRECT (Postgres): CURRENT_DATE - INTERVAL '30 days'
  Also: CURRENT_DATE (no parens). NOW() for timestamp. AGE(), EXTRACT(epoch FROM ...).
- COLUMN-TYPE AWARE CASTING (look at dtype shown next to each column):
  - If a column's dtype is `text` / `character varying` / `varchar` but the value
    is a date/timestamp, CAST it: `sale_date::date >= CURRENT_DATE - INTERVAL '30 days'`
  - If dtype is `text` but used in numeric aggregation, CAST: `qty::numeric`
  - Date columns with dtype `date` / `timestamp` need no cast.
- SELECT only (no DDL/DML)
- Add LIMIT 5000
- Use real column names from tables above (quote with double quotes if reserved)
- Always use ::date casts on TEXT date columns, ::numeric on TEXT amount columns.

PANEL-SHAPE RULES (chart_type-specific result shape):
- KPI: SELECT a single value (COUNT/SUM/AVG). Optionally include previous period for delta.
- chart_type=line: SELECT date_col AS x, metric AS y, optionally a series_col. ORDER BY x.
- chart_type=bar/pie: SELECT category_col AS x, metric AS y. ORDER BY y DESC LIMIT 20.
- chart_type=stacked_bar: SELECT date_col, category_col, metric. ORDER BY date_col, category_col.
- chart_type=heatmap: SELECT row_dim, col_dim, value. ORDER BY row_dim, col_dim.
- chart_type=scatter: SELECT x_metric, y_metric (optionally label). LIMIT 500.
- table: SELECT all relevant columns, ORDER BY primary metric DESC LIMIT 50.

If you cannot write valid SQL for this panel (e.g. needed columns don't exist),
return EXACTLY: -- SKIP: <reason>
Do NOT fabricate columns.

Return ONLY the SQL, no markdown, no explanation"""
            raw = _llm(prompt, task="dashboard_gen", model=self.gen_model)
            self.tokens_used += (len(prompt) + len(raw)) // 4
            sql = _strip_fences(raw).strip().rstrip(";")
            # Honor -- SKIP: sentinel — don't fabricate, leave SQL empty so gate drops it
            if sql.lstrip().upper().startswith("-- SKIP"):
                logger.info(f"sql gen skip for panel {plan.id}: {sql[:120]}")
                sql = ""
            sqls.append(PanelSQL(panel_id=plan.id, sql=sql))
            if self._over_budget():
                break
        self.sqls = sqls
        return sqls

    # ---------- stage 5 ----------
    def stage5_explain_gate(self) -> list[PanelSQL]:
        """SQL-VALIDATED: route every panel SQL through central validator.
        Fail-soft: if validator unavailable OR raises, fall back to inline EXPLAIN.
        """
        # Fallback path when central validator unavailable
        if not _SQL_VALIDATOR_AVAILABLE or _sql_validate_and_fix is None:
            return self._stage5_explain_gate_fallback()

        for ps in self.sqls:
            if not ps.sql or not re.match(r"^\s*(WITH|SELECT)\b", ps.sql, re.IGNORECASE):
                ps.explain_passed = False
                ps.explain_error = "non-select"
                continue
            try:
                # SQL-VALIDATED: central validate + auto-fix
                v = _sql_validate_and_fix(ps.sql, self.project_slug, strict=True)
            except Exception as e:
                logger.warning(f"sql_validator raised, falling back: {e}")
                # Fail-soft per-panel: keep original SQL, mark passed so execution proceeds
                ps.explain_passed = True
                ps.explain_error = None
                continue

            if v.get("ok"):
                ps.sql = v.get("sql") or ps.sql
                ps.explain_passed = True
                ps.explain_error = None
                fixes = v.get("fixes_applied") or []
                if fixes:
                    logger.info(f"sql validator fixed panel {ps.panel_id}: {fixes}")
            else:
                ps.explain_passed = False
                errs = v.get("errors") or []
                ps.explain_error = ("; ".join(str(x) for x in errs))[:200] if errs else "validation_failed"
                # Retry once with validator errors fed back to LLM
                fix_prompt = _skill_prefix("skl_sql_optimizer", self.project_slug) + f"""SQL failed validation with: {ps.explain_error}
Fix the SQL. Use only real columns. SELECT only. LIMIT 5000.
Original:
{ps.sql}
Return ONLY fixed SQL."""
                fixed = _strip_fences(_llm(fix_prompt, task="extraction", model=self.gen_model)).strip().rstrip(";")
                if fixed and re.match(r"^\s*(WITH|SELECT)\b", fixed, re.IGNORECASE):
                    try:
                        # SQL-VALIDATED: re-validate the LLM repair
                        v2 = _sql_validate_and_fix(fixed, self.project_slug, strict=True)
                        if v2.get("ok"):
                            ps.sql = v2.get("sql") or fixed
                            ps.explain_passed = True
                            ps.explain_error = None
                    except Exception as e2:
                        ps.explain_error = str(e2)[:200]
        return self.sqls

    def _stage5_explain_gate_fallback(self) -> list[PanelSQL]:
        """Legacy inline EXPLAIN path used when central validator unavailable."""
        try:
            from db.session import get_project_readonly_engine
            eng = get_project_readonly_engine(self.project_slug)
        except Exception as e:
            logger.warning(f"explain gate engine init failed: {e}")
            return self.sqls
        for ps in self.sqls:
            if not ps.sql or not re.match(r"^\s*(WITH|SELECT)\b", ps.sql, re.IGNORECASE):
                ps.explain_passed = False
                ps.explain_error = "non-select"
                continue
            try:
                with eng.connect() as conn:
                    plan = conn.execute(text(f"EXPLAIN (FORMAT JSON) {ps.sql}")).fetchone()
                ps.explain_passed = True
                try:
                    ps.explain_cost = float(plan[0][0]["Plan"]["Total Cost"])  # type: ignore[index]
                except Exception:
                    pass
            except Exception as e:
                ps.explain_error = str(e)[:200]
                fix_prompt = _skill_prefix("skl_sql_optimizer", self.project_slug) + f"""SQL failed EXPLAIN with: {ps.explain_error}
Fix the SQL. Use only real columns. SELECT only. LIMIT 5000.
Original:
{ps.sql}
Return ONLY fixed SQL."""
                fixed = _strip_fences(_llm(fix_prompt, task="extraction", model=self.gen_model)).strip().rstrip(";")
                if fixed and re.match(r"^\s*(WITH|SELECT)\b", fixed, re.IGNORECASE):
                    try:
                        with eng.connect() as conn:
                            conn.execute(text(f"EXPLAIN {fixed}"))
                        ps.sql = fixed
                        ps.explain_passed = True
                        ps.explain_error = None
                    except Exception as e2:
                        ps.explain_error = str(e2)[:200]
        return self.sqls

    # ---------- stage 6 ----------
    def stage6_execute(self) -> list[PanelData]:
        out: list[PanelData] = []
        for ps in self.sqls:
            if not ps.explain_passed or not ps.sql:
                out.append(PanelData(panel_id=ps.panel_id))
                continue
            t = time.time()
            df = _run_sql(self.project_slug, ps.sql)
            ms = int((time.time() - t) * 1000)
            if df is None or df.empty:
                out.append(PanelData(panel_id=ps.panel_id, exec_ms=ms))
                continue
            cols = [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns[:30]]
            profile = {
                "row_count": int(len(df)),
                "null_pct": {c: float(df[c].isna().mean()) for c in df.columns[:20]},
                "cardinality": {c: int(df[c].nunique()) for c in df.columns[:20]
                                if df[c].dtype.kind in "OSb"},
            }
            out.append(PanelData(
                panel_id=ps.panel_id,
                rows=df.head(200).to_dict("records"),
                row_count=int(len(df)),
                columns=cols,
                profile=profile,
                exec_ms=ms,
            ))
        self.data = out
        # #13 — Drop failed/empty panels. Align plans + sqls + data by panel_id.
        try:
            valid_ids: set[str] = set()
            sql_by_pid = {s.panel_id: s for s in self.sqls}
            for plan, dat in zip(self.plans, out):
                pid = plan.id
                ptype = (plan.panel_type or "").lower()
                ps = sql_by_pid.get(pid)
                sql_err = (ps.explain_error if ps else None)
                if sql_err:
                    logger.info(f"dropped panel {pid} due to SQL error: {sql_err}")
                    continue
                rows = dat.rows or []
                if ptype in ("kpi", "chart", "table") and not rows:
                    logger.info(f"dropped panel {pid} due to 0 rows")
                    continue
                # all-null check for kpi/chart
                if rows and ptype in ("kpi", "chart"):
                    cols = [c.get("name") for c in (dat.columns or []) if c.get("name")]
                    if cols:
                        total = len(rows) * len(cols)
                        null_count = sum(
                            1 for r in rows for c in cols
                            if (r.get(c) if isinstance(r, dict) else None) is None
                        )
                        if total > 0 and (null_count / total) > 0.95:
                            logger.info(f"dropped panel {pid} due to >95% null")
                            continue
                valid_ids.add(pid)
            if valid_ids != {p.id for p in self.plans}:
                self.plans = [p for p in self.plans if p.id in valid_ids]
                self.sqls = [s for s in self.sqls if s.panel_id in valid_ids]
                self.data = [d for d in out if d.panel_id in valid_ids]
                out = self.data
        except Exception as e:
            logger.debug(f"panel filter failed (non-fatal): {e}")
        return out

    # ---------- stage 7 ----------
    def stage7_chart_specs(self) -> list[EChartsPanelSpec]:
        specs: list[EChartsPanelSpec] = []
        for plan, dat in zip(self.plans, self.data):
            if dat.row_count == 0:
                continue
            sample = dat.rows[:30]
            prompt = _skill_prefix("skl_panel_designer", self.project_slug) + f"""Generate an ECharts 5.5 options JSON for this panel.

TITLE: {plan.title}
CHART TYPE: {plan.chart_type or "bar"}
PANEL TYPE: {plan.panel_type}
COLUMNS: {[c['name'] for c in dat.columns]}
SAMPLE ROWS (first 30):
{json.dumps(sample, default=str)[:2000]}

Output JSON: {{
  "options": <ECharts options object — must include xAxis, yAxis, series for charts; or single 'value' for kpi>,
  "narrative": "1-2 sentence insight from the data",
  "confidence": "low|medium|high",
  "grid": [x, y, w, h]  // 12-col grid, w in 1-12, h in 1-6
}}

Rules: ECharts options ONLY (no Vega/Plotly). Use real column values from sample.
For KPI panels, use {{"value":N, "label":"...", "delta_pct":N}}. JSON only."""
            raw = _llm(prompt, task="dashboard_gen", model=self.gen_model)
            self.tokens_used += (len(prompt) + len(raw)) // 4
            parsed = _parse_json(raw, {}) or {}
            try:
                spec = EChartsPanelSpec(
                    panel_id=plan.id,
                    chart_type=plan.chart_type or "bar",  # type: ignore[arg-type]
                    title=plan.title,
                    options=parsed.get("options") or {},
                    narrative=parsed.get("narrative", "")[:300],
                    confidence=parsed.get("confidence", "medium"),
                    grid=parsed.get("grid") or [0, 0, 6, 3],
                    sources=plan.tables_used,
                )
                # Attach executed data to panel spec so persisted cells include rows/columns
                # for renderers that fall back to raw data (vs. just ECharts options).
                try:
                    setattr(spec, "rows", (dat.rows or [])[:500])
                    setattr(spec, "columns", dat.columns or [])
                    setattr(spec, "row_count", int(dat.row_count or 0))
                except Exception:
                    pass
                specs.append(spec)
            except Exception as e:
                logger.debug(f"chart spec parse skip {plan.id}: {e}")
            if self._over_budget():
                break
        self.panel_specs = specs
        return specs

    # ---------- stage 8 ----------
    def stage8_judge(self) -> Critique:
        """DIFFERENT-model critic. judge_model MUST differ from gen_model."""
        if not self.panel_specs:
            self.critique = Critique(judge_model=self.judge_model or "", gen_model=self.gen_model or "")
            return self.critique
        panels_blob = "\n".join(
            f"- {p.panel_id} [{p.chart_type}] {p.title} | conf={p.confidence}"
            for p in self.panel_specs
        )
        prompt = _skill_prefix("skl_dash_critic", self.project_slug) + f"""You are a dashboard reviewer(NOT the generator). Audit these panels.

USER QUESTION: {self.intent.question if self.intent else ""}
AUDIENCE: {self.audience}

PANELS:
{panels_blob}

For each issue, output a critique entry. Check:
- chart_type fits the data shape (bar vs line vs pie)
- axis labels present + readable
- color accessibility (WCAG-ish, no rainbow)
- panel redundancy (two panels showing same thing)
- encoding/dtype mismatch (e.g. categorical on continuous axis)
- low signal (variance trivially small)
- misleading (truncated axis, mismatched scale)

Output JSON: {{
  "issues": [{{
    "panel_id": "...", "severity": "low|medium|high",
    "kind": "chart_type_mismatch|axis_sanity|color_a11y|redundancy|missing_label|encoding_dtype_mismatch|low_signal|misleading",
    "detail": "...",
    "suggested_patch": [{{"op":"replace","path":"/panels/<i>/options/...", "value": ...}}]
  }}],
  "overall_score": 0-100
}}

JSON only."""
        raw = _llm(prompt, task="extraction", model=self.judge_model)
        self.tokens_used += (len(prompt) + len(raw)) // 4
        parsed = _parse_json(raw, {}) or {}
        issues: list[CritiqueIssue] = []
        for it in parsed.get("issues", []):
            try:
                issues.append(CritiqueIssue(**it))
            except Exception:
                pass
        self.critique = Critique(
            issues=issues,
            overall_score=int(parsed.get("overall_score", 50) or 50),
            judge_model=self.judge_model or "",
            gen_model=self.gen_model or "",
        )
        return self.critique

    # ---------- stage 9 ----------
    def stage9_layout(self) -> DeepDashSpec:
        # Auto-pack 12-col grid: KPIs row 0 (each width 3), charts below (width 6),
        # narratives full width (width 12).
        row = 0
        col = 0
        kpis: list[EChartsPanelSpec] = []
        charts: list[EChartsPanelSpec] = []
        narratives: list[EChartsPanelSpec] = []
        # Sort by plan.priority (preserve order of self.plans)
        prio = {p.id: p.priority for p in self.plans}
        ordered = sorted(self.panel_specs, key=lambda s: -prio.get(s.panel_id, 50))
        for s in ordered:
            plan = next((p for p in self.plans if p.id == s.panel_id), None)
            t = plan.panel_type if plan else "chart"
            (kpis if t == "kpi" else narratives if t in ("narrative", "insight") else charts).append(s)
        # KPI strip
        for s in kpis[:4]:
            s.grid = [col, row, 3, 2]
            col += 3
            if col >= 12:
                col = 0
                row += 2
        if kpis:
            row += 2
            col = 0
        # Charts 2-up
        for s in charts:
            s.grid = [col, row, 6, 3]
            col += 6
            if col >= 12:
                col = 0
                row += 3
        if col != 0:
            row += 3
            col = 0
        # Narratives full width
        for s in narratives:
            s.grid = [0, row, 12, 2]
            row += 2

        # Pick layout template
        if kpis and charts and narratives:
            layout = "executive"
        elif len(charts) >= len(self.panel_specs) - 1:
            layout = "operational"
        elif narratives:
            layout = "narrative"
        else:
            layout = "executive"

        self.final = DeepDashSpec(
            project_slug=self.project_slug,
            title=(self.intent.question if self.intent else "Dashboard")[:120],
            intent=self.intent or DashboardIntent(question=self.question),
            panels=ordered,
            layout=layout,  # type: ignore[arg-type]
            persona=self.persona,
            audience=self.audience,
            judge_score=(self.critique.overall_score if self.critique else None),
        )
        return self.final

    # ---------- helpers for narrator stage ----------
    def _collect_verified_values(self) -> list[dict]:
        """Per-panel `try_metric_shortcut` lookups for narrator truth-grounding."""
        out: list[dict] = []
        try:
            from dash.learning.verified_reward import try_metric_shortcut
        except Exception as e:
            logger.debug(f"verified_reward import failed: {e}")
            return out
        for plan in (self.plans or []):
            q = plan.question or plan.title
            if not q:
                continue
            try:
                hit = try_metric_shortcut(self.project_slug, q)
            except Exception:
                hit = None
            if hit and hit.get("value") is not None:
                out.append({
                    "panel_id": plan.id,
                    "label": plan.title[:120],
                    "value": hit.get("value"),
                    "source_q": hit.get("source_q"),
                })
        return out

    # ---------- orchestration ----------
    def run_sync(self) -> dict:
        # Reset per-run skill version tracker so audit only sees this run.
        _reset_skill_versions_for_run()

        # Stage 0.5 — intent classify (first turn). Drives narrator audience.
        t0 = time.time()
        try:
            ic = stage_intent_classify(self.question, history=None, project_slug=self.project_slug)
            if ic.get("audience"):
                self.audience = ic["audience"]
            self._intent_meta = ic
        except Exception as e:
            logger.debug(f"intent classify failed: {e}")
            self._intent_meta = {"intent": "kpi_overview", "audience": self.audience}
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dashboard_intent",
            self._sv("skl_dashboard_intent"), "stage_intent_classify",
            latency_ms=int((time.time() - t0) * 1000),
        )

        t0 = time.time(); self.stage1_intent()
        # stage 1 = intent extraction; no specific skill (uses base LLM).

        t0 = time.time(); self.stage2_schema_rag()
        # stage 2 = pure schema RAG; no skill.

        t0 = time.time(); self.stage3_panel_plan()
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dash_orchestrator",
            self._sv("skl_dash_orchestrator"), "stage_panel_plan",
            panel_count=len(self.plans or []),
            latency_ms=int((time.time() - t0) * 1000),
        )

        t0 = time.time(); self.stage4_sql_gen()
        # stage 4 = sql gen; no skill per task spec.

        t0 = time.time(); self.stage5_explain_gate()
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_sql_optimizer",
            self._sv("skl_sql_optimizer"), "stage_explain_gate",
            panel_count=len(self.sqls or []),
            latency_ms=int((time.time() - t0) * 1000),
        )

        t0 = time.time(); self.stage6_execute()
        # stage 6 = SQL execute; no skill.

        t0 = time.time(); self.stage7_chart_specs()
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_panel_designer",
            self._sv("skl_panel_designer"), "stage_chart_specs",
            panel_count=len(self.panel_specs or []),
            latency_ms=int((time.time() - t0) * 1000),
        )

        # Per-panel announcer telemetry (one row per panel).
        for ps in (self.panel_specs or []):
            t_ann = time.time()
            try:
                stage_panel_announce(ps, row_count=0, project_slug=self.project_slug)
            except Exception:
                pass
            _persist_skill_run(
                self.project_slug, self.dashboard_id, "skl_panel_announcer",
                self._sv("skl_panel_announcer"), "panel_announcement",
                panel_count=1,
                latency_ms=int((time.time() - t_ann) * 1000),
            )

        # Stage 7.5 — Executive Overview narrator (truth-grounded)
        verified_values = self._collect_verified_values()
        narrative = {}
        t0 = time.time()
        try:
            narrative = stage_executive_overview(
                spec=self,  # mutated best-effort; real attach happens after layout
                audience=self.audience,
                panels=self.panel_specs,
                verified_values=verified_values,
                project_slug=self.project_slug,
            )
            self._narrative = narrative
            self._verified_values = verified_values
        except Exception as e:
            logger.exception(f"stage_executive_overview failed: {e}")
            self._narrative = {}
            self._verified_values = []
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dashboard_narrator",
            self._sv("skl_dashboard_narrator"), "stage_executive_overview",
            panel_count=len(self.panel_specs or []),
            verified_cell_count=len(verified_values or []),
            latency_ms=int((time.time() - t0) * 1000),
        )

        t0 = time.time(); self.stage8_judge()
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dash_critic",
            self._sv("skl_dash_critic"), "stage_judge",
            panel_count=len(self.panel_specs or []),
            judge_score=(self.critique.overall_score if self.critique else None),
            latency_ms=int((time.time() - t0) * 1000),
        )

        self.stage9_layout()

        # Attach narrative onto final DeepDashSpec (extra='allow' or dict fallback)
        if self.final is not None and self._narrative:
            try:
                setattr(self.final, "narrative", self._narrative)
            except Exception:
                pass

        # Audience persistence — stamp the final spec's `audience` from intent
        # classify so refine-mode reload sees the original choice.
        if self.final is not None:
            try:
                setattr(self.final, "audience", self.audience)
            except Exception:
                pass
            try:
                if getattr(self.final, "intent", None) is not None:
                    setattr(self.final.intent, "audience", self.audience)
            except Exception:
                pass

        final_dump: dict = self.final.model_dump() if self.final else {}
        if self._narrative and isinstance(final_dump, dict):
            final_dump["narrative"] = self._narrative
        # Belt-and-braces: ensure JSON-dumped spec exposes audience at root.
        if isinstance(final_dump, dict):
            final_dump["audience"] = self.audience
            if final_dump.get("id") in (None, ""):
                final_dump["id"] = self.dashboard_id

        # End-of-run audit — verified_cell_pct + skill_versions JSONB.
        _persist_dashboard_audit(
            self.dashboard_id,
            dict(_SKILL_VERSIONS_RUN),
            self._compute_verified_cell_pct(),
        )

        # Persist spec — new row per build, versioned by session_id if present.
        # Generate fresh id for this build to avoid PK collision across versions.
        try:
            self.dashboard_id = f"deepdash_{uuid.uuid4().hex[:12]}"
        except Exception:
            pass
        self._persist_versioned(final_dump)

        return {
            "spec": final_dump,
            "critique": self.critique.model_dump() if self.critique else {},
            "narrative": self._narrative,
            "intent": getattr(self, "_intent_meta", {}),
            "tokens_used": self.tokens_used,
            "wall_s": round(time.time() - self.t0, 2),
            "dashboard_id": self.dashboard_id,
        }

    async def stream(self) -> AsyncGenerator[dict, None]:
        """SSE-ready stage events. Each stage runs in thread to not block loop.

        Order (11 stages including 0.5 intent + 7.5 narrator):
        intent_classify → intent → schema_rag → panel_plan → sql_gen →
        explain_gate → execute → chart_specs → narrator → judge → layout.

        Extra SSE events emitted:
        - `panel_announcement` per panel after `panel_ready` (LITE, non-blocking)
        - `narrative_ready` after stage 7.5 (truth-grounded exec overview)
        """
        # Reset per-run skill version tracker so audit only sees this run.
        _reset_skill_versions_for_run()

        # Stage 0.5 — intent classify
        yield {"type": "stage_start", "stage": "intent_classify", "n": 0, "of": 11}
        t0 = time.time()
        try:
            ic = await asyncio.to_thread(stage_intent_classify, self.question, None, self.project_slug)
            if ic.get("audience"):
                self.audience = ic["audience"]
            self._intent_meta = ic
            yield {"type": "stage_done", "stage": "intent_classify",
                   "intent": ic.get("intent"), "audience": ic.get("audience")}
        except Exception as e:
            logger.debug(f"intent_classify failed: {e}")
            self._intent_meta = {"intent": "kpi_overview", "audience": self.audience}
            yield {"type": "stage_error", "stage": "intent_classify", "error": str(e)[:300]}
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dashboard_intent",
            self._sv("skl_dashboard_intent"), "stage_intent_classify",
            latency_ms=int((time.time() - t0) * 1000),
        )

        stages: list[tuple[str, Any]] = [
            ("intent", self.stage1_intent),
            ("schema_rag", self.stage2_schema_rag),
            ("panel_plan", self.stage3_panel_plan),
            ("sql_gen", self.stage4_sql_gen),
            ("explain_gate", self.stage5_explain_gate),
            ("execute", self.stage6_execute),
            ("chart_specs", self.stage7_chart_specs),
        ]
        # Map stage_name -> (skill_id, stage_key) for telemetry persistence.
        # `None` means: no skill powered this stage (sql_gen, execute, schema_rag,
        # intent extraction), so we still persist a row with skill_id="".
        _stage_skill = {
            "intent": (None, "stage_intent"),
            "schema_rag": (None, "stage_schema_rag"),
            "panel_plan": ("skl_dash_orchestrator", "stage_panel_plan"),
            "sql_gen": (None, "stage_sql_gen"),
            "explain_gate": ("skl_sql_optimizer", "stage_explain_gate"),
            "execute": (None, "stage_execute"),
            "chart_specs": ("skl_panel_designer", "stage_chart_specs"),
        }
        for i, (name, fn) in enumerate(stages, start=1):
            yield {"type": "stage_start", "stage": name, "n": i, "of": 11}
            t_stage = time.time()
            try:
                result = await asyncio.to_thread(fn)
            except Exception as e:
                logger.exception(f"stage {name} failed")
                yield {"type": "stage_error", "stage": name, "error": str(e)[:300]}
                # Still record the latency on error so SkillRefinery sees the failure.
                sid_err, stage_key_err = _stage_skill.get(name, (None, f"stage_{name}"))
                if sid_err:
                    _persist_skill_run(
                        self.project_slug, self.dashboard_id, sid_err,
                        self._sv(sid_err), stage_key_err,
                        latency_ms=int((time.time() - t_stage) * 1000),
                    )
                continue
            payload: dict = {"type": "stage_done", "stage": name, "n": i}
            stage_panel_count = 0
            if name == "panel_plan":
                payload["panels"] = [p.model_dump() for p in (result or [])]
                stage_panel_count = len(self.plans or [])
            elif name == "explain_gate":
                payload["passed"] = sum(1 for s in self.sqls if s.explain_passed)
                payload["failed"] = sum(1 for s in self.sqls if not s.explain_passed)
                stage_panel_count = len(self.sqls or [])
            elif name == "chart_specs":
                payload["count"] = len(self.panel_specs)
                stage_panel_count = len(self.panel_specs or [])
            yield payload
            # Persist per-stage skill telemetry (skip stages with no skill).
            sid, stage_key = _stage_skill.get(name, (None, f"stage_{name}"))
            if sid:
                _persist_skill_run(
                    self.project_slug, self.dashboard_id, sid,
                    self._sv(sid), stage_key,
                    panel_count=stage_panel_count,
                    latency_ms=int((time.time() - t_stage) * 1000),
                )
            # Mid-stream panel emission so frontend renders as available,
            # followed by LITE per-panel announcement (non-blocking on failure).
            if name == "chart_specs":
                # Build a panel_id → row_count map from PanelData
                row_count_map: dict[str, int] = {}
                for pd_ in (self.data or []):
                    try:
                        row_count_map[pd_.panel_id] = int(pd_.row_count or 0)
                    except Exception:
                        pass
                for ps in self.panel_specs:
                    yield {"type": "panel_ready", "panel": ps.model_dump()}
                    t_ann = time.time()
                    try:
                        ann = await asyncio.to_thread(
                            stage_panel_announce, ps, row_count_map.get(ps.panel_id, 0), self.project_slug
                        )
                        yield {"type": "panel_announcement", **ann}
                    except Exception as e:
                        logger.debug(f"panel_announcement failed for {ps.panel_id}: {e}")
                    _persist_skill_run(
                        self.project_slug, self.dashboard_id, "skl_panel_announcer",
                        self._sv("skl_panel_announcer"), "panel_announcement",
                        panel_count=1,
                        latency_ms=int((time.time() - t_ann) * 1000),
                    )

        # Stage 7.5 — Executive Overview narrator (truth-grounded)
        yield {"type": "stage_start", "stage": "narrator", "n": 8, "of": 11}
        t_narr = time.time()
        verified_values_len = 0
        try:
            verified_values = await asyncio.to_thread(self._collect_verified_values)
            verified_values_len = len(verified_values or [])
            narrative = await asyncio.to_thread(
                stage_executive_overview,
                self, self.audience, self.panel_specs, verified_values, self.project_slug,
            )
            self._narrative = narrative
            self._verified_values = verified_values
            yield {"type": "stage_done", "stage": "narrator",
                   "verified_values": len(verified_values),
                   "text_len": len((narrative or {}).get("text", ""))}
            yield {
                "type": "narrative_ready",
                "text": (narrative or {}).get("text", ""),
                "audience": (narrative or {}).get("audience", self.audience),
                "verified_value_count": (narrative or {}).get("verified_value_count", 0),
            }
        except Exception as e:
            logger.exception(f"narrator stage failed: {e}")
            self._narrative = {}
            self._verified_values = []
            yield {"type": "stage_error", "stage": "narrator", "error": str(e)[:300]}
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dashboard_narrator",
            self._sv("skl_dashboard_narrator"), "stage_executive_overview",
            panel_count=len(self.panel_specs or []),
            verified_cell_count=verified_values_len,
            latency_ms=int((time.time() - t_narr) * 1000),
        )

        # Stage 8 — Judge
        yield {"type": "stage_start", "stage": "judge", "n": 9, "of": 11}
        t_judge = time.time()
        try:
            await asyncio.to_thread(self.stage8_judge)
            payload = {"type": "stage_done", "stage": "judge", "n": 9}
            if self.critique:
                payload["score"] = self.critique.overall_score
                payload["issues"] = len(self.critique.issues)
            yield payload
        except Exception as e:
            logger.exception("stage judge failed")
            yield {"type": "stage_error", "stage": "judge", "error": str(e)[:300]}
        _persist_skill_run(
            self.project_slug, self.dashboard_id, "skl_dash_critic",
            self._sv("skl_dash_critic"), "stage_judge",
            panel_count=len(self.panel_specs or []),
            judge_score=(self.critique.overall_score if self.critique else None),
            latency_ms=int((time.time() - t_judge) * 1000),
        )

        # Stage 9 — Layout
        yield {"type": "stage_start", "stage": "layout", "n": 10, "of": 11}
        try:
            await asyncio.to_thread(self.stage9_layout)
            yield {"type": "stage_done", "stage": "layout", "n": 10}
        except Exception as e:
            logger.exception("stage layout failed")
            yield {"type": "stage_error", "stage": "layout", "error": str(e)[:300]}

        # Attach narrative onto final spec dump
        if self.final is not None and getattr(self, "_narrative", None):
            try:
                setattr(self.final, "narrative", self._narrative)
            except Exception:
                pass

        # Audience persistence — stamp the final spec from intent classify
        # so refine-mode reload sees the original audience choice.
        if self.final is not None:
            try:
                setattr(self.final, "audience", self.audience)
            except Exception:
                pass
            try:
                if getattr(self.final, "intent", None) is not None:
                    setattr(self.final.intent, "audience", self.audience)
            except Exception:
                pass

        final_dump: dict = self.final.model_dump() if self.final else {}
        if getattr(self, "_narrative", None) and isinstance(final_dump, dict):
            final_dump["narrative"] = self._narrative
        # Belt-and-braces: ensure JSON-dumped spec exposes audience at root.
        if isinstance(final_dump, dict):
            final_dump["audience"] = self.audience
            if final_dump.get("id") in (None, ""):
                final_dump["id"] = self.dashboard_id

        # End-of-run audit — verified_cell_pct + skill_versions JSONB.
        _persist_dashboard_audit(
            self.dashboard_id,
            dict(_SKILL_VERSIONS_RUN),
            self._compute_verified_cell_pct(),
        )

        # Persist + mirror panels → cells so legacy DashRenderer + new Studio
        # renderer both see content (Deep Dash writes spec.panels; legacy
        # reads spec.cells). One source-of-truth normalize here.
        try:
            from sqlalchemy import text as _sa_text
            from db.session import get_write_engine as _get_wen
            import json as _json
            if isinstance(final_dump, dict):
                _panels = final_dump.get("panels") or []
                if _panels and not final_dump.get("cells"):
                    _cells = []
                    for _p in _panels:
                        if not isinstance(_p, dict):
                            continue
                        _ptype = str(_p.get("panel_type") or "chart").lower()
                        _ct = ("kpi" if _ptype == "kpi"
                               else "insight" if _ptype in ("insight", "narrative")
                               else "table" if _ptype == "table"
                               else "chart")
                        _cells.append({
                            "id": _p.get("panel_id") or f"p_{len(_cells)+1}",
                            "type": _ct,
                            "grid": _p.get("grid") or [0, 0, 6, 3],
                            "title": _p.get("title") or "",
                            "verified": bool(_p.get("verified")),
                            "source_metric": _p.get("source_metric"),
                            "rows": _p.get("rows") or [],
                            "columns": _p.get("columns") or [],
                            "row_count": _p.get("row_count") or 0,
                            "config": {
                                "chart_type": _p.get("chart_type") or "bar",
                                "echarts_options": _p.get("options") or {},
                                "narrative": _p.get("narrative") or "",
                                "confidence": _p.get("confidence") or "medium",
                                "sources": _p.get("sources") or [],
                                "headline": (_p.get("title") or "") if _ct in ("insight", "kpi") else None,
                                "cause": (_p.get("narrative") or "") if _ct == "insight" else None,
                                "rows": _p.get("rows") or [],
                                "columns": _p.get("columns") or [],
                            },
                        })
                    final_dump["cells"] = _cells
        except Exception as _exc:
            logger.warning("dash_dashboards_v2 cells normalize failed for %s: %s", self.dashboard_id, _exc)

        # Persist spec — new row per build, versioned by session_id if present.
        try:
            self.dashboard_id = f"deepdash_{uuid.uuid4().hex[:12]}"
        except Exception:
            pass
        self._persist_versioned(final_dump)

        yield {
            "type": "done",
            "spec": final_dump,
            "narrative": getattr(self, "_narrative", {}),
            "intent": getattr(self, "_intent_meta", {}),
            "tokens_used": self.tokens_used,
            "wall_s": round(time.time() - self.t0, 2),
            "dashboard_id": self.dashboard_id,
        }


# ============================================================
# JSON Patch — universal edit protocol (RFC 6902)
# ============================================================

# ============================================================
# NEW STAGES — skill-driven additions (intent / narrator / announcer / refiner)
# ============================================================

def _clean_narrative_numbers(text: str) -> str:
    """#12 — Round floats with >2 decimals to 0/1/2 decimals based on magnitude.
    Strips noisy precision like 182.333333334 → 182 in narrator output.
    """
    if not text:
        return text

    def repl(m):
        try:
            val = float(m.group(0))
            if val == int(val):
                return str(int(val))
            if abs(val) >= 100:
                return str(int(round(val)))
            if abs(val) >= 10:
                return f"{val:.1f}"
            return f"{val:.2f}"
        except Exception:
            return m.group(0)

    return re.sub(r'\b\d+\.\d{3,}\b', repl, text)


def stage_intent_classify(question: str, history: list[str] | None = None, project_slug: str | None = None) -> dict:
    """Stage 0.5 — classify intent + audience from the user question.

    Uses LITE model via `_skill_prefix('skl_dashboard_intent')` prompt prefix.
    Returns `{"intent": str, "audience": str}`. Audience drives Stage 7.5
    narrator skill choice (skl_narrative_{investor|ops|customer|exec}).
    Fail-soft: returns defaults on any error.
    """
    hist_blob = "\n".join(f"- {h}" for h in (history or [])[-6:]) or "(none)"
    prompt = _skill_prefix("skl_dashboard_intent", project_slug) + f"""Classify the user's dashboard request.

QUESTION: {question}
RECENT HISTORY:
{hist_blob}

Output JSON ONLY with:
- intent: short slug (e.g. "kpi_overview", "trend_review", "comparison", "drilldown", "qbr", "investor_update", "ops_review", "customer_review")
- audience: one of (investor, ops, customer, exec, general)

Rules: if the phrase mentions "for investors" / "to investors" → audience=investor. "for ops/operations" → ops. "for customer/client" → customer. "for exec/leadership/board" → exec. Else general.
JSON only."""
    raw = _llm(prompt, task="extraction")
    parsed = _parse_json(raw, {}) or {}
    intent = str(parsed.get("intent") or "kpi_overview").strip()[:80]
    audience = str(parsed.get("audience") or "general").strip().lower()
    if audience not in ("investor", "ops", "customer", "exec", "general"):
        audience = "general"
    return {"intent": intent, "audience": audience}


def stage_executive_overview(
    spec: Any,
    audience: str,
    panels: list,
    verified_values: list[dict] | None = None,
    project_slug: str | None = None,
) -> dict:
    """Stage 7.5 — generate Executive Overview narrative paragraph.

    Calls `_skill_prefix("skl_dashboard_narrator")` + audience-specific style
    skill (`skl_narrative_{audience}`). Verified values injected as
    "USE THESE NUMBERS VERBATIM" authoritative directive.

    Sets `spec.narrative = {text, audience, verified_value_count}` (mutates if
    pydantic model w/ extra='allow', else best-effort).

    Returns the narrative dict.
    """
    verified_values = verified_values or []
    if verified_values:
        vv_lines = []
        for v in verified_values[:20]:
            vv_lines.append(
                f"- {v.get('label') or v.get('source_q','metric')}: "
                f"{v.get('value')}  (panel={v.get('panel_id','?')})"
            )
        vv_block = (
            "\n\n⚑ VERIFIED METRICS — USE THESE NUMBERS VERBATIM, do NOT round, "
            "do NOT recompute:\n" + "\n".join(vv_lines)
        )
    else:
        vv_block = ""

    panels_blob = "\n".join(
        f"- {getattr(p, 'panel_id', '?')} [{getattr(p, 'chart_type', '?')}] "
        f"{getattr(p, 'title', '')} (conf={getattr(p, 'confidence', 'medium')})"
        for p in (panels or [])
    ) or "(no panels)"

    aud_skill = f"skl_narrative_{audience}" if audience in (
        "investor", "ops", "customer", "exec"
    ) else "skl_narrative_exec"

    prompt = (
        _skill_prefix("skl_dashboard_narrator", project_slug)
        + _skill_prefix(aud_skill, project_slug)
        + f"""Write the Executive Overview for this dashboard.

AUDIENCE: {audience}
PANELS:
{panels_blob}
{vv_block}

Rules:
- 2-4 sentences. Lead with the headline. End with the so-what.
- Use the verified numbers verbatim where applicable. Never invent figures.
- Tone fits the audience (investor → forward-looking + risk; ops → action; customer → outcome; exec → bottom-line).

Output ONLY the narrative paragraph as plain text. No JSON, no markdown headers."""
    )
    raw = _llm(prompt, task="deep_analysis")
    text_block = (raw or "").strip()
    # Trim accidental fences/quotes
    text_block = re.sub(r"^```[a-zA-Z]*\s*", "", text_block)
    text_block = re.sub(r"\s*```$", "", text_block).strip().strip('"').strip()
    # #12 — Truth-grounded narrator: clean noisy float precision.
    text_block = _clean_narrative_numbers(text_block)

    narrative = {
        "text": text_block[:2000],
        "audience": audience,
        "verified_value_count": len(verified_values),
    }
    # #12 — Also clean each panel's narrative field.
    try:
        for p in (panels or []):
            pn = getattr(p, "narrative", None)
            if isinstance(pn, str) and pn:
                cleaned = _clean_narrative_numbers(pn)
                try:
                    setattr(p, "narrative", cleaned)
                except Exception:
                    pass
    except Exception:
        pass
    # Best-effort attach to spec
    try:
        if hasattr(spec, "narrative"):
            try:
                setattr(spec, "narrative", narrative)
            except Exception:
                pass
        elif isinstance(spec, dict):
            spec["narrative"] = narrative
    except Exception:
        pass
    return narrative


def stage_panel_announce(panel: Any, row_count: int = 0, project_slug: str | None = None) -> dict:
    """LITE/FAST per-panel announcement.

    Calls `skl_panel_announcer` → returns one-line "✓ Added [title] (N rows)".
    Returns a dict suitable for an SSE `panel_announcement` event:
      {panel_id, message, mini_thumbnail_spec: {chart_type, sparkline_data}}
    Fail-soft.
    """
    title = getattr(panel, "title", "") or ""
    panel_id = getattr(panel, "panel_id", None) or (
        panel.get("panel_id") if isinstance(panel, dict) else None
    ) or "panel"
    chart_type = getattr(panel, "chart_type", None) or (
        panel.get("chart_type") if isinstance(panel, dict) else None
    ) or "bar"

    prompt = _skill_prefix("skl_panel_announcer", project_slug) + f"""Write ONE short chat-line announcing this panel was added.

PANEL TITLE: {title}
ROWS: {row_count}
CHART_TYPE: {chart_type}

Output ONE line, max ~90 chars, starting with ✓. Example: "✓ Added Revenue by Region (12 rows)".
Plain text only."""
    raw = _llm(prompt, task="extraction")
    msg = (raw or "").strip().splitlines()[0] if raw else ""
    if not msg:
        msg = f"✓ Added {title or panel_id} ({row_count} rows)"
    msg = msg[:200]

    # Sparkline preview: first 10 numeric values from panel options if we can find them
    sparkline: list = []
    try:
        opts = getattr(panel, "options", None)
        if isinstance(opts, dict):
            ser = opts.get("series") or []
            if isinstance(ser, list) and ser:
                first = ser[0]
                if isinstance(first, dict):
                    data = first.get("data") or []
                    for d in data[:10]:
                        if isinstance(d, (int, float)):
                            sparkline.append(d)
                        elif isinstance(d, dict) and "value" in d:
                            v = d.get("value")
                            if isinstance(v, (int, float)):
                                sparkline.append(v)
                            elif isinstance(v, list) and v and isinstance(v[-1], (int, float)):
                                sparkline.append(v[-1])
    except Exception:
        sparkline = []

    return {
        "panel_id": panel_id,
        "message": msg,
        "mini_thumbnail_spec": {
            "chart_type": chart_type,
            "sparkline_data": sparkline,
        },
    }


def apply_refine_command(spec: dict, nl_command: str, project_slug: str | None = None) -> dict:
    """Refine-endpoint helper. LITE-model NL → RFC 6902 JSON Patch ops.

    Uses `skl_dashboard_refiner` skill. Returns {ops, summary}. Caller is
    responsible for `apply_patch(spec, ops)`.

    Fail-soft: bad LLM output → {ops: [], summary: error string}.
    """
    spec_compact = {}
    try:
        spec_compact = {
            "title": spec.get("title"),
            "layout": spec.get("layout"),
            "panels": [
                {"panel_id": p.get("panel_id"), "title": p.get("title"),
                 "chart_type": p.get("chart_type")}
                for p in (spec.get("panels") or [])[:20]
                if isinstance(p, dict)
            ],
        }
    except Exception:
        spec_compact = {"title": spec.get("title") if isinstance(spec, dict) else None}

    prompt = _skill_prefix("skl_dashboard_refiner", project_slug) + f"""Convert the user's refine command into RFC 6902 JSON Patch operations against the dashboard spec.

USER COMMAND: {nl_command}

CURRENT SPEC (summarized):
{json.dumps(spec_compact, default=str)[:3000]}

Output JSON ONLY:
{{
  "ops": [
    {{"op":"replace","path":"/panels/0/title","value":"New Title"}},
    ...
  ],
  "summary": "one-line description of what changes"
}}

Rules: RFC 6902 paths only (/panels/<i>/...). Use op in {{add, replace, remove, move, copy, test}}. Touch the minimum needed. No prose outside JSON."""
    raw = _llm(prompt, task="extraction")
    parsed = _parse_json(raw, {}) or {}
    ops = parsed.get("ops") if isinstance(parsed.get("ops"), list) else []
    # Filter to dict ops only
    ops = [o for o in ops if isinstance(o, dict) and o.get("op") and "path" in o]
    summary = str(parsed.get("summary") or "").strip()[:300] or f"Applied: {nl_command[:120]}"
    return {"ops": ops, "summary": summary}


def apply_patch(spec_dict: dict, ops: list[dict]) -> dict:
    """Apply RFC 6902 JSON Patch ops to a DeepDashSpec dict.
    Used by iteration: chat edit → router → generate patch → apply → re-render.
    Never full-regen. Bumps spec_version."""
    try:
        import jsonpatch  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("jsonpatch not installed; falling back to manual apply")
        # Minimal manual apply for add/replace/remove only
        for op in ops:
            kind = op.get("op")
            path = op.get("path", "")
            value = op.get("value")
            parts = [p for p in path.split("/") if p]
            cur = spec_dict
            for p in parts[:-1]:
                cur = cur[int(p)] if p.isdigit() else cur.setdefault(p, {})
            last = parts[-1] if parts else None
            if last is None:
                continue
            key: Any = int(last) if last.isdigit() else last
            if kind in ("add", "replace"):
                if isinstance(cur, list):
                    if kind == "add":
                        cur.insert(int(last), value)
                    else:
                        cur[int(last)] = value
                else:
                    cur[key] = value
            elif kind == "remove":
                if isinstance(cur, list):
                    cur.pop(int(last))
                else:
                    cur.pop(key, None)
        spec_dict["spec_version"] = int(spec_dict.get("spec_version", 1)) + 1
        return spec_dict
    patched = jsonpatch.apply_patch(spec_dict, ops)
    patched["spec_version"] = int(spec_dict.get("spec_version", 1)) + 1
    return patched
