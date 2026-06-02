"""ProviderTrainer — per-source training orchestrator.

Runs ~14 steps to convert a connected data source into an Analyst-ready
expert: schema, profile, dimensions, sample, Codex enrich, Q&A verify,
relationships, persona, domain knowledge, KG triples, LangExtract,
drift baseline, watermark register.

Streams progress via async generator yielding :class:`TrainEvent` dicts.
Writes a ``dash_source_training_runs`` row that the SSE endpoint surfaces.

Phase 4 — initial cut implements 5 critical steps end-to-end (catalog,
profile, dim catalog, sample, Codex). Remaining steps thread through
existing helpers in :mod:`app.upload` and
:mod:`dash.tools.knowledge_graph`; gradual migration is acceptable and
each stub method emits a ``done`` event with cost=0 so the SSE stream
still progresses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .base import BaseProvider
from .training_steps import (
    categorical_profile_sql,
    detect_watermark_column,
    diversify_sample,
    dimension_value_sql,
    flatten_sample_rows,
    fuzzy_match_colname,
    hierarchy_pair_sql,
    numeric_profile_sql,
    overlap_count_sql,
    qa_prompt,
    qualified,
    quote_ident,
    relationship_prompt,
    sample_rows_sql,
    schema_fingerprint,
    watermark_max_sql,
    _codex_prompt,
)

logger = logging.getLogger(__name__)

STEPS = [
    "catalog",            # 1: introspect
    "profile",            # 2: SQL column profile
    "dimension_catalog",  # 3: SELECT DISTINCT for cat cols
    "hierarchy_detect",   # 4: parent->child mapping (TODO)
    "sample",             # 5: 20 diverse rows
    "codex_enrich",       # 6: LLM purpose/grain/PK/FK per table
    "qa_verify",          # 7: LLM Q&A executed on source (TODO)
    "relationships",      # 8: FK + value overlap (TODO)
    "persona",            # 9: domain persona (TODO)
    "domain_knowledge",   # 10: glossary/KPIs (TODO)
    "kg_triples",         # 11: SPO triples tagged source_uri (TODO)
    "langextract",        # 12: grounded facts (TODO)
    "drift_baseline",     # 13: schema hash + NDV snapshot
    "watermark_register", # 14: pick update column, store last value
    "domain_detect",      # 15: detect domain from sample data + tables
    "auto_seed",          # 16: load matching brain seeds for detected domain
]

# Numeric SQL types we treat as candidates for percentile/avg profiling.
_NUMERIC_HINTS = (
    "int", "numeric", "decimal", "float", "double", "real", "money", "bigint",
    "smallint", "tinyint", "number",
)

# Rough cost-per-1K-tokens estimate ($) per task — used so the UI can render
# a sensible number even before we wire OpenRouter usage headers through.
_CODEX_COST_PER_TABLE_USD = 0.012
# Each Q&A batch: 1 deep_analysis call producing 5 pairs.
_QA_BATCH_COST_USD = 0.015
# Implicit relationship confirmation uses the lite extraction model.
_REL_CALL_COST_USD = 0.001
# Cap how many tables we run heavy steps against per source.
_QA_TABLE_CAP = 5
_HIERARCHY_PER_TABLE_CAP = 3
_IMPLICIT_REL_CAP = 50


@dataclass
class TrainEvent:
    """One streamable progress tick."""

    step: str
    index: int
    total: int
    status: str  # 'start' | 'done' | 'error'
    message: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class TrainResult:
    """Final summary after :meth:`ProviderTrainer.run` exhausts."""

    source_id: int
    project_slug: str
    success: bool
    steps_done: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    duration_seconds: int = 0
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


# ---------------------------------------------------------------------------
# Trainer
# ---------------------------------------------------------------------------

class ProviderTrainer:
    """Orchestrates per-source training across the 14 steps."""

    def __init__(
        self,
        provider: BaseProvider,
        dash_engine: Engine,
        deep_model_call: Callable[..., Any],
        *,
        source_id: int | None = None,
        knowledge_root: str | Path = "knowledge",
    ) -> None:
        self.provider = provider
        self.dash_engine = dash_engine
        self.deep_model_call = deep_model_call
        # ``provider.id`` is a string; ``source_id`` is the FK into
        # dash_data_sources used by the training_runs table.
        try:
            self.source_id = int(source_id) if source_id is not None else int(provider.id)
        except (TypeError, ValueError):
            self.source_id = 0
        self.project_slug = provider.project_slug
        self.knowledge_root = Path(knowledge_root)
        self.cost_usd: float = 0.0
        self.run_id: int | None = None
        self._catalog: dict[str, Any] = {}
        self._profile: dict[str, dict[str, Any]] = {}
        self._dimensions: dict[str, dict[str, Any]] = {}
        self._sample: dict[str, list[dict[str, Any]]] = {}
        self.steps_done: list[str] = []

    # ---- Public entrypoint --------------------------------------------------

    async def run(self) -> AsyncIterator[TrainEvent]:
        """Stream events while executing the pipeline.

        The orchestrator inserts a ``dash_source_training_runs`` row at
        start, updates ``current_step`` as it advances, and marks the row
        as ``completed`` / ``failed`` at exit. One bad step does NOT kill
        the whole run — each step is wrapped in try/except and emits an
        ``error`` event before the next step starts.
        """
        started = time.time()
        await asyncio.to_thread(self._insert_run_row)
        total = len(STEPS)
        error: str | None = None

        step_methods = {
            "catalog": self._step_catalog,
            "profile": self._step_profile,
            "dimension_catalog": self._step_dimension_catalog,
            "hierarchy_detect": self._step_hierarchy_detect,
            "sample": self._step_sample,
            "codex_enrich": self._step_codex_enrich,
            "qa_verify": self._step_qa_verify,
            "relationships": self._step_relationships,
            "persona": self._step_persona,
            "domain_knowledge": self._step_domain_knowledge,
            "kg_triples": self._step_kg_triples,
            "langextract": self._step_langextract,
            "drift_baseline": self._step_drift_baseline,
            "watermark_register": self._step_watermark_register,
            "domain_detect": self._step_domain_detect,
            "auto_seed": self._step_auto_seed,
        }

        for idx, step in enumerate(STEPS, start=1):
            yield TrainEvent(step=step, index=idx, total=total, status="start")
            await asyncio.to_thread(self._update_run_row, current_step=step)

            t0 = time.time()
            try:
                method = step_methods.get(step, self._step_todo)
                summary = await method() if step in step_methods else await self._step_todo(step)
                duration_ms = int((time.time() - t0) * 1000)
                self.steps_done.append(step)
                yield TrainEvent(
                    step=step,
                    index=idx,
                    total=total,
                    status="done",
                    message=str(summary.get("message", "")) if isinstance(summary, dict) else "",
                    cost_usd=float(summary.get("cost_usd", 0.0)) if isinstance(summary, dict) else 0.0,
                    duration_ms=duration_ms,
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.time() - t0) * 1000)
                logger.exception(
                    "ProviderTrainer step %s failed for source %s",
                    step, self.source_id,
                )
                error = error or f"{step}: {exc}"
                yield TrainEvent(
                    step=step,
                    index=idx,
                    total=total,
                    status="error",
                    message=str(exc)[:300],
                    duration_ms=duration_ms,
                )

        duration_seconds = int(time.time() - started)
        success = error is None
        await asyncio.to_thread(
            self._finalize_run_row, success, error, duration_seconds
        )

        # The last yielded value is the final summary — callers using
        # async-for can discard it; the SSE wrapper sends it as `event: done`.
        yield TrainEvent(
            step="__result__",
            index=total,
            total=total,
            status="done" if success else "error",
            message=error or "ok",
            cost_usd=self.cost_usd,
            duration_ms=duration_seconds * 1000,
        )

    def build_result(self, error: str | None, duration_seconds: int) -> TrainResult:
        return TrainResult(
            source_id=self.source_id,
            project_slug=self.project_slug,
            success=error is None,
            steps_done=list(self.steps_done),
            cost_usd=self.cost_usd,
            duration_seconds=duration_seconds,
            error=error,
        )

    # ---- Step 1: catalog ----------------------------------------------------

    async def _step_catalog(self) -> dict[str, Any]:
        """Run :meth:`provider.introspect` and persist to disk."""
        catalog = await asyncio.to_thread(self.provider.introspect)
        self._catalog = catalog or {}
        path = self._knowledge_dir() / "catalog.json"
        await asyncio.to_thread(self._write_json, path, self._catalog)
        n = len(self._catalog.get("tables") or [])
        return {"message": f"{n} table(s) catalogued", "tables": n}

    # ---- Step 2: profile ----------------------------------------------------

    async def _step_profile(self) -> dict[str, Any]:
        """Per-table column profile via SQL on the source engine."""
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        columns = catalog.get("columns") or {}
        profile_dir = self._knowledge_dir() / "profile"
        profiled = 0
        for table in tables:
            cols = columns.get(table) or []
            try:
                tbl_profile = await asyncio.to_thread(
                    self._profile_table, table, cols
                )
                self._profile[table] = tbl_profile
                await asyncio.to_thread(
                    self._write_json, profile_dir / f"{_safe_filename(table)}.json", tbl_profile
                )
                profiled += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("profile failed for %s: %s", table, exc)
        return {"message": f"profiled {profiled}/{len(tables)} tables"}

    def _profile_table(self, table: str, cols: list[dict[str, Any]]) -> dict[str, Any]:
        """Synchronous: run one profile SQL per column, accumulate stats."""
        engine = self.provider.engine_ro
        if engine is None:
            return {}
        out: dict[str, Any] = {"table": table, "columns": {}}
        with engine.connect() as conn:
            for col in cols:
                name = col.get("name")
                ctype = (col.get("type") or "").lower()
                if not name:
                    continue
                is_numeric = any(h in ctype for h in _NUMERIC_HINTS)
                sql = (
                    numeric_profile_sql(self.provider.dialect, table, name)
                    if is_numeric
                    else categorical_profile_sql(self.provider.dialect, table, name)
                )
                try:
                    row = conn.execute(text(sql)).mappings().first() or {}
                    out["columns"][name] = {
                        "type": ctype,
                        "is_numeric": is_numeric,
                        **{k: _coerce_scalar(v) for k, v in row.items()},
                    }
                except Exception as exc:  # noqa: BLE001
                    out["columns"][name] = {"type": ctype, "error": str(exc)[:200]}
        return out

    # ---- Step 3: dimension catalog -----------------------------------------

    async def _step_dimension_catalog(self) -> dict[str, Any]:
        """For each column with NDV<500, capture top-N values + freq."""
        dim_dir = self._knowledge_dir() / "dimensions"
        captured = 0
        for table, prof in (self._profile or {}).items():
            tbl_dims: dict[str, list[dict[str, Any]]] = {}
            for col_name, stats in (prof.get("columns") or {}).items():
                ndv = stats.get("ndv")
                try:
                    ndv = int(ndv) if ndv is not None else None
                except (TypeError, ValueError):
                    ndv = None
                if ndv is None or ndv >= 500 or ndv <= 0:
                    continue
                try:
                    rows = await asyncio.to_thread(
                        self._fetch_dimension_values, table, col_name
                    )
                    tbl_dims[col_name] = rows
                    captured += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("dim values failed for %s.%s: %s", table, col_name, exc)
            self._dimensions[table] = tbl_dims
            if tbl_dims:
                await asyncio.to_thread(
                    self._write_json,
                    dim_dir / f"{_safe_filename(table)}.json",
                    tbl_dims,
                )
        return {"message": f"{captured} dimension column(s) catalogued"}

    def _fetch_dimension_values(
        self, table: str, col: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        engine = self.provider.engine_ro
        if engine is None:
            return []
        sql = dimension_value_sql(self.provider.dialect, table, col, limit=limit)
        with engine.connect() as conn:
            return [
                {"value": _coerce_scalar(r[0]), "freq": int(r[1] or 0)}
                for r in conn.execute(text(sql)).all()
            ]

    # ---- Step 5: sample -----------------------------------------------------

    async def _step_sample(self) -> dict[str, Any]:
        """Pull 20 diverse rows per table — kept in memory only (no disk)."""
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        sampled = 0
        for table in tables:
            try:
                rows = await asyncio.to_thread(self._sample_table, table, 60)
                self._sample[table] = diversify_sample(rows, target=20)
                sampled += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("sample failed for %s: %s", table, exc)
        return {"message": f"sampled {sampled}/{len(tables)} tables (in-memory)"}

    def _sample_table(self, table: str, n: int) -> list[dict[str, Any]]:
        engine = self.provider.engine_ro
        if engine is None:
            return []
        sql = sample_rows_sql(self.provider.dialect, table, n)
        with engine.connect() as conn:
            cur = conn.execute(text(sql))
            cols = list(cur.keys())
            raw = cur.fetchall()
        return flatten_sample_rows(raw, cols)

    # ---- Step 6: codex enrich ----------------------------------------------

    async def _step_codex_enrich(self) -> dict[str, Any]:
        """One LLM call per table — purpose, grain, PK/FK, usage patterns."""
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        columns = catalog.get("columns") or {}
        fks_all = catalog.get("fks") or []
        codex_dir = self._knowledge_dir() / "codex"
        enriched = 0
        cost = 0.0
        for table in tables:
            cols = columns.get(table) or []
            sample = self._sample.get(table) or []
            profile = self._profile.get(table) or {}
            dims = self._dimensions.get(table) or {}
            tbl_fks = [fk for fk in fks_all if fk.get("from_table", "").endswith(table)]
            prompt = _codex_prompt(table, cols, sample, profile, dims, tbl_fks)
            try:
                content = await asyncio.to_thread(
                    self._call_llm, prompt, "deep_analysis"
                )
                cost += _CODEX_COST_PER_TABLE_USD
                payload = _try_parse_json(content) or {"raw": content}
                await asyncio.to_thread(
                    self._write_json,
                    codex_dir / f"{_safe_filename(table)}.json",
                    payload,
                )
                enriched += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("codex_enrich failed for %s: %s", table, exc)
        self.cost_usd += cost
        return {"message": f"enriched {enriched}/{len(tables)} tables", "cost_usd": cost}

    def _call_llm(self, prompt: str, task: str) -> str | None:
        # ``training_llm_call(prompt, task='deep_analysis')`` — that's the
        # signature exposed by ``dash.settings``. Returns content string.
        return self.deep_model_call(prompt, task=task)

    # ---- Step 13: drift baseline --------------------------------------------

    async def _step_drift_baseline(self) -> dict[str, Any]:
        catalog = self._catalog or {}
        fp = schema_fingerprint(catalog)
        baseline = {
            "schema_fingerprint": fp,
            "table_count": len(catalog.get("tables") or []),
            "column_count": sum(
                len(v or []) for v in (catalog.get("columns") or {}).values()
            ),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await asyncio.to_thread(
            self._write_data_source_jsonb, "drift_baseline", baseline
        )
        return {"message": f"fingerprint {fp[:12]}..."}

    # ---- Step 14: watermark register ---------------------------------------

    async def _step_watermark_register(self) -> dict[str, Any]:
        catalog = self._catalog or {}
        columns = catalog.get("columns") or {}
        chosen: dict[str, Any] = {}
        for table, cols in columns.items():
            wcol = detect_watermark_column(cols or [])
            if not wcol:
                continue
            try:
                max_v = await asyncio.to_thread(
                    self._fetch_watermark_max, table, wcol
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("watermark MAX failed for %s.%s: %s", table, wcol, exc)
                continue
            chosen[table] = {"column": wcol, "max_value": _coerce_scalar(max_v)}
        if chosen:
            await asyncio.to_thread(
                self._write_data_source_jsonb, "last_watermark", chosen
            )
        return {"message": f"{len(chosen)} watermark column(s) registered"}

    def _fetch_watermark_max(self, table: str, col: str) -> Any:
        engine = self.provider.engine_ro
        if engine is None:
            return None
        sql = watermark_max_sql(self.provider.dialect, table, col)
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    # ---- Step 15: domain detect --------------------------------------------

    async def _step_domain_detect(self) -> dict[str, Any]:
        """Detect domain for the source so seed packs can target it.

        Wrapped defensively: failure here must not break the run since
        ``auto_seed`` falls back to the generic seed pack.
        """
        try:
            from dash.learning.domain_detector import detect  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.warning("domain_detect: detector unavailable: %s", e)
            return {"message": "detector unavailable"}
        try:
            detection = await asyncio.to_thread(
                detect, self.project_slug, self.source_id
            )
            primary = getattr(detection, "primary", "generic")
            secondaries = getattr(detection, "secondaries", []) or []
            return {
                "message": f"primary={primary} secondaries={secondaries}"
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("domain_detect failed: %s", e)
            return {"message": f"error: {str(e)[:200]}"}

    # ---- Step 16: auto seed ------------------------------------------------

    async def _step_auto_seed(self) -> dict[str, Any]:
        """Load matching brain seed packs for the detected domain."""
        try:
            from dash.learning.seed_loader import auto_load
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_seed: loader unavailable: %s", e)
            return {"message": "loader unavailable"}
        try:
            stats = await asyncio.to_thread(
                auto_load, self.project_slug, self.source_id
            )
            inserted = stats.get("total_inserted", 0)
            domains = stats.get("domains", []) or []
            return {
                "message": f"loaded {inserted} entries from {len(domains)} domains"
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_seed failed: %s", e)
            return {"message": f"error: {str(e)[:200]}"}

    # ---- Step 4: hierarchy detect ------------------------------------------

    async def _step_hierarchy_detect(self) -> dict[str, Any]:
        """Detect parent->child hierarchies between dim columns within a table.

        Mirrors ``app.upload._detect_hierarchies`` but operates on the source
        engine via dialect-aware SQL. Caps results to 3 hierarchies/table
        to avoid combinatorial explosion on wide categorical schemas.
        """
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        results: list[dict[str, Any]] = []
        scanned = 0
        for table in tables:
            dims = self._dimensions.get(table) or {}
            if not dims or len(dims) < 2:
                continue
            scanned += 1
            try:
                pairs = await asyncio.to_thread(self._detect_table_hierarchies, table, dims)
                results.extend(pairs[:_HIERARCHY_PER_TABLE_CAP])
            except Exception as exc:  # noqa: BLE001
                logger.warning("hierarchy_detect failed for %s: %s", table, exc)
        path = self._knowledge_dir() / "hierarchies.json"
        await asyncio.to_thread(self._write_json, path, results)
        return {
            "message": f"{len(results)} hierarchy/ies across {scanned} table(s)",
            "hierarchies": len(results),
            "tables_scanned": scanned,
        }

    def _detect_table_hierarchies(
        self, table: str, dims: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        engine = self.provider.engine_ro
        if engine is None:
            return []
        # NDV(col) ~ len(captured values) — dimension catalog already capped
        # at NDV<500, so length is exact unless truncated.
        ndvs = {col: len(vals or []) for col, vals in dims.items()}
        col_names = list(ndvs.keys())
        found: list[dict[str, Any]] = []
        with engine.connect() as conn:
            for parent in col_names:
                if len(found) >= _HIERARCHY_PER_TABLE_CAP:
                    break
                for child in col_names:
                    if parent == child or len(found) >= _HIERARCHY_PER_TABLE_CAP:
                        continue
                    if ndvs[parent] >= ndvs[child] or ndvs[parent] == 0:
                        continue  # children must have more values than parents
                    sql = hierarchy_pair_sql(
                        self.provider.dialect, table, parent, child
                    )
                    try:
                        row = conn.execute(text(sql)).first()
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "hierarchy SQL failed for %s.%s->%s: %s",
                            table, parent, child, exc,
                        )
                        continue
                    if not row:
                        continue
                    pair_rows, ndv_child = int(row[0] or 0), int(row[1] or 0)
                    if ndv_child > 0 and pair_rows == ndv_child:
                        found.append(
                            {"table": table, "parent": parent, "child": child}
                        )
        return found

    # ---- Step 7: qa_verify --------------------------------------------------

    async def _step_qa_verify(self) -> dict[str, Any]:
        """LLM generates 5 Q&A pairs per top table; SQL executed on source."""
        catalog = self._catalog or {}
        tables_all = list(catalog.get("tables") or [])
        columns = catalog.get("columns") or {}
        # Pick top tables by row count from profile
        ranked: list[tuple[str, int]] = []
        for t in tables_all:
            n = 0
            try:
                cols_prof = (self._profile.get(t) or {}).get("columns") or {}
                for stats in cols_prof.values():
                    n = max(n, int(stats.get("n") or 0))
            except Exception:  # noqa: BLE001
                n = 0
            ranked.append((t, n))
        ranked.sort(key=lambda x: x[1], reverse=True)
        top_tables = [t for t, _ in ranked[:_QA_TABLE_CAP]]

        verified_total = 0
        total_pairs = 0
        cost = 0.0
        summary: list[dict[str, Any]] = []

        # Detect once whether dash_training_qa has source_id (it currently
        # does NOT — schema unchanged across the provider migration).
        has_source_id = await asyncio.to_thread(
            self._table_has_column, "dash_training_qa", "source_id"
        )

        for table in top_tables:
            cols = columns.get(table) or []
            sample = self._sample.get(table) or []
            profile = self._profile.get(table) or {}
            dims = self._dimensions.get(table) or {}
            prompt = qa_prompt(table, cols, sample, profile, dims, self.provider.dialect)
            try:
                content = await asyncio.to_thread(
                    self._call_llm, prompt, "deep_analysis"
                )
                cost += _QA_BATCH_COST_USD
            except Exception as exc:  # noqa: BLE001
                logger.warning("qa_verify LLM failed for %s: %s", table, exc)
                continue
            pairs = _try_parse_json_list(content) or []
            if not pairs:
                continue
            tbl_verified = 0
            for pair in pairs[:5]:
                if not isinstance(pair, dict):
                    continue
                question = str(pair.get("question") or "").strip()
                sql = str(pair.get("sql") or "").strip()
                expected = str(pair.get("expected_answer_summary") or "")[:1000]
                if not question or not sql:
                    continue
                total_pairs += 1
                ok, answer_text = False, expected
                try:
                    ok, answer_text = await asyncio.to_thread(
                        self._execute_qa_sql, sql, expected
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("qa SQL exec failed for %s: %s", table, exc)
                if ok:
                    verified_total += 1
                    tbl_verified += 1
                try:
                    await asyncio.to_thread(
                        self._insert_training_qa,
                        table, question, sql, answer_text, ok, has_source_id,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("qa insert failed for %s: %s", table, exc)
            summary.append(
                {"table": table, "verified": tbl_verified, "total": min(5, len(pairs))}
            )

        await asyncio.to_thread(
            self._write_json,
            self._knowledge_dir() / "qa_verified.json",
            {"verified": verified_total, "total": total_pairs, "tables": summary},
        )
        self.cost_usd += cost
        return {
            "message": f"{verified_total}/{total_pairs} Q&A verified across "
                       f"{len(top_tables)} table(s)",
            "verified": verified_total,
            "total": total_pairs,
            "tables_processed": len(top_tables),
            "cost_usd": cost,
        }

    def _execute_qa_sql(self, sql: str, fallback: str) -> tuple[bool, str]:
        """Run a generated SELECT with a 30s timeout; return ``(ok, answer)``."""
        engine = self.provider.engine_ro
        if engine is None:
            return False, fallback
        cleaned = sql.strip().rstrip(";")
        if not cleaned.lower().startswith("select"):
            return False, fallback
        try:
            with engine.connect().execution_options(timeout=30) as conn:
                cur = conn.execute(text(cleaned))
                row = cur.fetchone()
                if row is None:
                    return False, fallback
                # Render first row as ``col=val`` joined by commas.
                try:
                    keys = list(cur.keys())
                except Exception:  # noqa: BLE001
                    keys = [f"c{i}" for i in range(len(row))]
                pairs = [f"{k}={_coerce_scalar(v)}" for k, v in zip(keys, row)]
                return True, ", ".join(pairs)[:1000]
        except Exception as exc:  # noqa: BLE001
            logger.debug("qa SQL exec error: %s", exc)
            return False, fallback

    def _table_has_column(self, table: str, column: str) -> bool:
        try:
            with self.dash_engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_schema = 'public' "
                        "AND table_name = :t AND column_name = :c LIMIT 1"
                    ),
                    {"t": table, "c": column},
                ).first()
                return bool(row)
        except Exception:  # noqa: BLE001
            return False

    def _insert_training_qa(
        self,
        table: str,
        question: str,
        sql: str,
        answer: str,
        verified: bool,
        has_source_id: bool,
    ) -> None:
        # Schema today: (project_slug, table_name, question, sql, answer_template)
        params = {
            "s": self.project_slug,
            "t": table,
            "q": question,
            "sql": sql,
            "a": answer,
        }
        if has_source_id:
            sql_text = (
                "INSERT INTO public.dash_training_qa "
                "(project_slug, table_name, question, sql, answer_template, source_id) "
                "VALUES (:s, :t, :q, :sql, :a, :sid)"
            )
            params["sid"] = self.source_id
        else:
            sql_text = (
                "INSERT INTO public.dash_training_qa "
                "(project_slug, table_name, question, sql, answer_template) "
                "VALUES (:s, :t, :q, :sql, :a)"
            )
        # ``verified`` is currently informational only — schema lacks the
        # column so we encode it inline in answer_template.
        if not verified:
            params["a"] = f"[unverified] {params['a']}"[:1000]
        with self.dash_engine.connect() as conn:
            conn.execute(text(sql_text), params)
            conn.commit()

    # ---- Step 8: relationships ---------------------------------------------

    async def _step_relationships(self) -> dict[str, Any]:
        """Verify FKs by data overlap; mine implicit joins by name match.

        Implicit candidates are confirmed via a cheap LLM call (extraction
        task → LITE_MODEL). Anything LLM rejects is skipped.
        """
        catalog = self._catalog or {}
        fks = list((self.provider.schema_blob or {}).get("fks") or catalog.get("fks") or [])
        columns = catalog.get("columns") or {}

        has_source_id = await asyncio.to_thread(
            self._table_has_column, "dash_relationships", "source_id"
        )

        fks_verified = 0
        for fk in fks:
            from_table = str(fk.get("from_table") or "").split(".")[-1]
            to_table = str(fk.get("to_table") or "").split(".")[-1]
            from_col = str(fk.get("from_column") or "")
            to_col = str(fk.get("to_column") or "")
            if not (from_table and to_table and from_col and to_col):
                continue
            try:
                overlap, _, ndv_a = await asyncio.to_thread(
                    self._fetch_overlap, from_table, from_col, to_table, to_col
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("FK overlap failed for %s.%s: %s", from_table, from_col, exc)
                continue
            if ndv_a <= 0 or (overlap / max(ndv_a, 1)) < 0.5:
                continue
            try:
                await asyncio.to_thread(
                    self._insert_relationship,
                    from_table, from_col, to_table, to_col,
                    "fk", min(0.99, overlap / max(ndv_a, 1)),
                    "verified by data overlap",
                    has_source_id,
                )
                fks_verified += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("FK insert failed: %s", exc)

        # Build implicit candidate list from name match / fuzzy match.
        candidates: list[tuple[str, str, str, str]] = []
        all_tables = list(columns.keys())
        seen: set[tuple[str, str, str, str]] = set()
        existing_fk_keys = {
            (
                str(fk.get("from_table") or "").split(".")[-1],
                str(fk.get("from_column") or ""),
                str(fk.get("to_table") or "").split(".")[-1],
                str(fk.get("to_column") or ""),
            )
            for fk in fks
        }
        for i, ta in enumerate(all_tables):
            for tb in all_tables[i + 1:]:
                for ca in columns.get(ta) or []:
                    name_a = ca.get("name") or ""
                    if not name_a:
                        continue
                    fa = fuzzy_match_colname(name_a)
                    for cb in columns.get(tb) or []:
                        name_b = cb.get("name") or ""
                        if not name_b:
                            continue
                        fb = fuzzy_match_colname(name_b)
                        if name_a.lower() == name_b.lower() or (fa and fa == fb):
                            key = (ta, name_a, tb, name_b)
                            if key in seen or key in existing_fk_keys:
                                continue
                            seen.add(key)
                            candidates.append(key)
                            if len(candidates) >= _IMPLICIT_REL_CAP:
                                break
                    if len(candidates) >= _IMPLICIT_REL_CAP:
                        break
                if len(candidates) >= _IMPLICIT_REL_CAP:
                    break
            if len(candidates) >= _IMPLICIT_REL_CAP:
                break

        implicit_found = 0
        cost = 0.0
        for ta, ca, tb, cb in candidates:
            try:
                overlap, ndv_a, ndv_b = await asyncio.to_thread(
                    self._fetch_overlap, ta, ca, tb, cb
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("implicit overlap failed for %s.%s ~ %s.%s: %s",
                             ta, ca, tb, cb, exc)
                continue
            denom = max(min(ndv_a, ndv_b), 1)
            pct = overlap / denom
            if pct <= 0.8:
                continue
            prompt = relationship_prompt(ta, ca, tb, cb, pct)
            try:
                content = await asyncio.to_thread(self._call_llm, prompt, "extraction")
                cost += _REL_CALL_COST_USD
            except Exception as exc:  # noqa: BLE001
                logger.debug("rel LLM failed: %s", exc)
                continue
            decision = _try_parse_json(content) or {}
            if not bool(decision.get("valid")):
                continue
            reason = str(decision.get("reason") or "")[:500]
            try:
                await asyncio.to_thread(
                    self._insert_relationship,
                    ta, ca, tb, cb, "implicit", min(0.95, pct), reason,
                    has_source_id,
                )
                implicit_found += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug("implicit insert failed: %s", exc)

        await asyncio.to_thread(
            self._write_json,
            self._knowledge_dir() / "relationships.json",
            {
                "fks_verified": fks_verified,
                "implicit_found": implicit_found,
                "candidates_scanned": len(candidates),
            },
        )
        self.cost_usd += cost
        return {
            "message": f"{fks_verified} FK + {implicit_found} implicit relationship(s)",
            "fks_verified": fks_verified,
            "implicit_found": implicit_found,
            "total": fks_verified + implicit_found,
            "cost_usd": cost,
        }

    def _fetch_overlap(
        self, table_a: str, col_a: str, table_b: str, col_b: str
    ) -> tuple[int, int, int]:
        engine = self.provider.engine_ro
        if engine is None:
            return 0, 0, 0
        sql = overlap_count_sql(self.provider.dialect, table_a, col_a, table_b, col_b)
        with engine.connect() as conn:
            row = conn.execute(text(sql)).first()
        if not row:
            return 0, 0, 0
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    def _insert_relationship(
        self,
        from_table: str,
        from_col: str,
        to_table: str,
        to_col: str,
        rel_type: str,
        confidence: float,
        reason: str,
        has_source_id: bool,
    ) -> None:
        if has_source_id:
            sql_text = (
                "INSERT INTO public.dash_relationships "
                "(project_slug, source_id, from_table, from_column, to_table, "
                " to_column, rel_type, confidence, source) "
                "VALUES (:slug, :sid, :ft, :fc, :tt, :tc, :rt, :conf, :src) "
                "ON CONFLICT (project_slug, from_table, from_column, to_table, to_column) "
                "DO NOTHING"
            )
            params = {
                "slug": self.project_slug,
                "sid": self.source_id,
                "ft": from_table,
                "fc": from_col,
                "tt": to_table,
                "tc": to_col,
                "rt": rel_type,
                "conf": float(confidence),
                "src": (reason or "trainer")[:200],
            }
        else:
            sql_text = (
                "INSERT INTO public.dash_relationships "
                "(project_slug, from_table, from_column, to_table, to_column, "
                " rel_type, confidence, source) "
                "VALUES (:slug, :ft, :fc, :tt, :tc, :rt, :conf, :src) "
                "ON CONFLICT (project_slug, from_table, from_column, to_table, to_column) "
                "DO NOTHING"
            )
            params = {
                "slug": self.project_slug,
                "ft": from_table,
                "fc": from_col,
                "tt": to_table,
                "tc": to_col,
                "rt": rel_type,
                "conf": float(confidence),
                "src": (reason or "trainer")[:200],
            }
        with self.dash_engine.connect() as conn:
            conn.execute(text(sql_text), params)
            conn.commit()

    # ---- Step 9: persona ---------------------------------------------------

    async def _step_persona(self) -> dict[str, Any]:
        """Generate a 200-300 word domain persona via LLM, persist to DB or disk."""
        try:
            payload = await asyncio.to_thread(self._build_persona_prompt)
            prompt = payload["prompt"]
            content = await asyncio.to_thread(self._call_llm, prompt, "deep_analysis")
            persona_text = (content or "").strip()
            self.cost_usd += 0.015
            saved = await asyncio.to_thread(self._persist_persona, persona_text)
            return {
                "message": f"persona {len(persona_text)} chars",
                "cost_usd": 0.015,
                "persona_chars": len(persona_text),
                "saved": saved,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("persona step failed")
            raise

    def _build_persona_prompt(self) -> dict[str, Any]:
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        columns = catalog.get("columns") or {}
        # Top 5 tables by row count when profile has it; fall back to first 5.
        ranked: list[tuple[str, int]] = []
        for tbl in tables:
            rc = 0
            prof_cols = (self._profile.get(tbl) or {}).get("columns") or {}
            for stats in prof_cols.values():
                try:
                    rc = max(rc, int(stats.get("row_count") or stats.get("count") or 0))
                except (TypeError, ValueError):
                    pass
            ranked.append((tbl, rc))
        ranked.sort(key=lambda r: r[1], reverse=True)
        top = ranked[:5] or [(t, 0) for t in tables[:5]]

        lines: list[str] = []
        for tbl, rc in top:
            cols = columns.get(tbl) or []
            col_names = ", ".join(c.get("name", "") for c in cols[:10])
            dims = self._dimensions.get(tbl) or {}
            dim_preview = []
            for dcol, vals in list(dims.items())[:3]:
                vs = ", ".join(str(v.get("value")) for v in (vals or [])[:5])
                dim_preview.append(f"{dcol}=[{vs}]")
            measures = [
                c.get("name") for c in cols
                if any(h in (c.get("type") or "").lower() for h in _NUMERIC_HINTS)
            ][:5]
            lines.append(
                f"- {tbl} (~{rc} rows): cols={col_names}; "
                f"dims={'; '.join(dim_preview) or 'n/a'}; "
                f"measures={', '.join(measures) or 'n/a'}"
            )
        catalog_summary = "\n".join(lines) or "<empty catalog>"

        prompt = (
            f"You are creating an analyst persona for the data source '{self.provider.name}' "
            f"(dialect: {self.provider.dialect}) in the workspace '{self.project_slug}'.\n\n"
            f"CATALOG SUMMARY (top tables by size):\n{catalog_summary}\n\n"
            "Write a 200-300 word persona in second person ('You are a ...'). "
            "Format: 'You are a {domain} analyst for {workspace}, specializing in "
            "{key tables}, with deep understanding of {key metrics}...'. "
            "Mention the dialect, the domain implied by the tables, the most important "
            "measures, and how this analyst should approach questions. "
            "Return only the persona prose — no markdown headers, no JSON."
        )
        return {"prompt": prompt}

    def _persist_persona(self, persona_text: str) -> bool:
        """Try DB insert into dash_personas with source_id; fall back to file."""
        if not persona_text:
            return False
        # Probe schema for source_id column.
        has_source_id = False
        try:
            with self.dash_engine.connect() as conn:
                row = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='dash_personas' "
                    "AND column_name='source_id' LIMIT 1"
                )).fetchone()
                has_source_id = bool(row)
        except Exception:  # noqa: BLE001
            has_source_id = False

        if has_source_id:
            try:
                with self.dash_engine.connect() as conn:
                    conn.execute(
                        text(
                            "INSERT INTO public.dash_personas "
                            "(project_slug, source_id, persona_text, created_at) "
                            "VALUES (:slug, :sid, :ptxt, NOW())"
                        ),
                        {
                            "slug": self.project_slug,
                            "sid": self.source_id,
                            "ptxt": persona_text,
                        },
                    )
                    conn.commit()
                return True
            except Exception:  # noqa: BLE001
                logger.exception("dash_personas insert failed; falling back to disk")

        # Fallback — file under knowledge/{slug}/source_{id}/persona.txt
        logger.warning(
            "dash_personas.source_id missing or insert failed; saving persona to disk for source %s",
            self.source_id,
        )
        path = self._knowledge_dir() / "persona.txt"
        try:
            path.write_text(persona_text, encoding="utf-8")
            return False
        except Exception:  # noqa: BLE001
            logger.exception("persona disk write failed")
            return False

    # ---- Step 10: domain knowledge -----------------------------------------

    async def _step_domain_knowledge(self) -> dict[str, Any]:
        """6 LLM sub-steps: glossary, calculations, value_mappings, kpis,
        data_quality, negative_examples. Persist each to dash_memories +
        knowledge/{slug}/source_{id}/domain/{type}.json."""
        sub_specs = [
            (
                "glossary", "domain_glossary",
                "Extract a business glossary: domain terms, acronyms, abbreviations "
                "with definitions. Return JSON: "
                '[{"term": "MMK", "definition": "Myanmar Kyat (currency)"}].',
            ),
            (
                "calculations", "domain_calculation",
                "Identify derived metrics and formulas relating columns. "
                'Return JSON: [{"name": "net_amount", "formula": "total - tax + discount"}].',
            ),
            (
                "value_mappings", "domain_value_mapping",
                "For low-cardinality categorical columns, map codes to meanings. "
                'Return JSON: [{"column": "size", "code": "S", "meaning": "Small"}].',
            ),
            (
                "kpis", "domain_kpi",
                "List the KPIs measurable from this data. Return JSON: "
                '[{"name": "Total Spend", "definition": "sum of approved net_amount", '
                '"sql_hint": "SUM(net_amount) WHERE status=\'Approved\'"}].',
            ),
            (
                "data_quality", "domain_data_quality",
                "Document data quality issues, NULL meanings, and pitfalls. "
                'Return JSON: [{"issue": "supplier_name NULL", "rule": "exclude template rows"}].',
            ),
            (
                "negative_examples", "domain_negative",
                "Common mistakes when querying this data, as DON'T/DO pairs. "
                'Return JSON: [{"avoid": "COUNT(*) for orders", "do": "COUNT(DISTINCT order_id)"}].',
            ),
        ]
        sub_done: list[str] = []
        total_entries = 0
        cost = 0.0
        catalog_summary = await asyncio.to_thread(self._domain_context_string)

        for kind, source_tag, instr in sub_specs:
            try:
                prompt = (
                    f"Source '{self.provider.name}' (dialect={self.provider.dialect}, "
                    f"workspace={self.project_slug}).\n\n"
                    f"CATALOG:\n{catalog_summary}\n\n"
                    f"{instr}\n\n"
                    "Cap the array at 20 items. Return ONLY a JSON array — no prose, no markdown."
                )
                content = await asyncio.to_thread(
                    self._call_llm, prompt, "deep_analysis"
                )
                cost += 0.012
                entries = _try_parse_json_array(content) or []
                entries = entries[:20]
                # Persist
                saved = await asyncio.to_thread(
                    self._persist_domain_entries, source_tag, entries
                )
                # Save raw JSON artifact
                await asyncio.to_thread(
                    self._write_json,
                    self._knowledge_dir() / "domain" / f"{kind}.json",
                    entries,
                )
                total_entries += saved
                sub_done.append(kind)
            except Exception as exc:  # noqa: BLE001
                logger.warning("domain_knowledge sub-step %s failed: %s", kind, exc)
        self.cost_usd += cost
        return {
            "message": f"{len(sub_done)}/6 sub-steps · {total_entries} entries",
            "cost_usd": cost,
            "sub_steps_done": sub_done,
            "total_entries": total_entries,
        }

    def _domain_context_string(self) -> str:
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])[:8]
        columns = catalog.get("columns") or {}
        lines: list[str] = []
        for tbl in tables:
            cols = columns.get(tbl) or []
            col_names = ", ".join(c.get("name", "") for c in cols[:12])
            dims = self._dimensions.get(tbl) or {}
            dim_preview = []
            for dcol, vals in list(dims.items())[:3]:
                vs = ", ".join(str(v.get("value")) for v in (vals or [])[:5])
                dim_preview.append(f"{dcol}=[{vs}]")
            prof_cols = (self._profile.get(tbl) or {}).get("columns") or {}
            null_info = []
            for cn, st in list(prof_cols.items())[:6]:
                nullp = st.get("null_pct") or st.get("null_percent")
                if nullp:
                    null_info.append(f"{cn}={nullp}%null")
            lines.append(
                f"- {tbl}: cols=[{col_names}]; "
                f"dims=[{'; '.join(dim_preview) or 'n/a'}]; "
                f"nulls=[{', '.join(null_info) or 'n/a'}]"
            )
        return "\n".join(lines) or "<empty>"

    def _persist_domain_entries(self, source_tag: str, entries: list[Any]) -> int:
        if not entries:
            return 0
        saved = 0
        try:
            with self.dash_engine.connect() as conn:
                for entry in entries:
                    if isinstance(entry, dict):
                        fact_text = json.dumps(entry, ensure_ascii=False)[:1000]
                    else:
                        fact_text = str(entry)[:1000]
                    if not fact_text or len(fact_text) < 4:
                        continue
                    try:
                        conn.execute(
                            text(
                                "INSERT INTO public.dash_memories "
                                "(project_slug, scope, fact, source, source_id) "
                                "VALUES (:slug, 'project', :fact, :src, :sid) "
                                "ON CONFLICT DO NOTHING"
                            ),
                            {
                                "slug": self.project_slug,
                                "fact": fact_text,
                                "src": source_tag,
                                "sid": self.source_id,
                            },
                        )
                        saved += 1
                    except Exception:  # noqa: BLE001
                        # source_id col may be missing on legacy DBs — retry without
                        try:
                            conn.execute(
                                text(
                                    "INSERT INTO public.dash_memories "
                                    "(project_slug, scope, fact, source) "
                                    "VALUES (:slug, 'project', :fact, :src) "
                                    "ON CONFLICT DO NOTHING"
                                ),
                                {
                                    "slug": self.project_slug,
                                    "fact": fact_text,
                                    "src": source_tag,
                                },
                            )
                            saved += 1
                        except Exception:  # noqa: BLE001
                            pass
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("persist_domain_entries failed for %s", source_tag)
        return saved

    # ---- Step 11: KG triples -----------------------------------------------

    async def _step_kg_triples(self) -> dict[str, Any]:
        """Build SPO triples for tables/columns/dim values, tag with source_uri."""
        catalog = self._catalog or {}
        tables = list(catalog.get("tables") or [])
        columns = catalog.get("columns") or {}
        fks_all = catalog.get("fks") or []

        triples_inserted = await asyncio.to_thread(
            self._build_and_insert_kg_triples, tables, columns, fks_all
        )
        return {
            "message": f"{triples_inserted} triple(s) inserted across {len(tables)} table(s)",
            "triples_inserted": triples_inserted,
            "tables_processed": len(tables),
        }

    def _build_and_insert_kg_triples(
        self,
        tables: list[str],
        columns: dict[str, list[dict[str, Any]]],
        fks_all: list[dict[str, Any]],
    ) -> int:
        """Construct triples and insert into dash_knowledge_triples directly."""
        source_uri_base = f"{self.provider.dialect}:{self.source_id}"
        rows: list[dict[str, Any]] = []
        for tbl in tables:
            uri = f"{source_uri_base}:{tbl}"
            cols = columns.get(tbl) or []
            # table -> column edges
            for c in cols:
                cname = c.get("name")
                if not cname:
                    continue
                rows.append({
                    "subject": tbl, "predicate": "hasColumn", "object": cname,
                    "source_type": "table", "source_id": tbl,
                    "source_uri": uri, "confidence": 1.0,
                })
                # numeric column => measure
                ctype = (c.get("type") or "").lower()
                if any(h in ctype for h in _NUMERIC_HINTS):
                    rows.append({
                        "subject": tbl, "predicate": "has_metric", "object": cname,
                        "source_type": "table", "source_id": tbl,
                        "source_uri": uri, "confidence": 1.0,
                    })
            # FK edges
            for fk in fks_all:
                from_t = fk.get("from_table") or fk.get("table") or ""
                if not from_t.endswith(tbl):
                    continue
                from_c = fk.get("from_column") or fk.get("column") or ""
                to_t = fk.get("to_table") or fk.get("ref_table") or ""
                to_c = fk.get("to_column") or fk.get("ref_column") or ""
                if from_c and to_t and to_c:
                    rows.append({
                        "subject": f"{tbl}.{from_c}",
                        "predicate": "references",
                        "object": f"{to_t}.{to_c}",
                        "source_type": "fk", "source_id": tbl,
                        "source_uri": uri, "confidence": 1.0,
                    })
            # Dim value -> column edges (top 8 values per dim col)
            dims = self._dimensions.get(tbl) or {}
            for dcol, vals in dims.items():
                for v in (vals or [])[:8]:
                    val = v.get("value")
                    if val is None or val == "":
                        continue
                    rows.append({
                        "subject": str(val),
                        "predicate": "isValueOf",
                        "object": f"{tbl}.{dcol}",
                        "source_type": "value", "source_id": tbl,
                        "source_uri": uri, "confidence": 0.9,
                    })

        if not rows:
            return 0

        inserted = 0
        try:
            with self.dash_engine.connect() as conn:
                # Ensure table exists (matches knowledge_graph._save_knowledge_graph)
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
                        source_uri TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """))
                # Wipe previous triples for this source_uri so reruns are idempotent.
                conn.execute(
                    text(
                        "DELETE FROM public.dash_knowledge_triples "
                        "WHERE project_slug = :slug AND source_uri LIKE :prefix"
                    ),
                    {"slug": self.project_slug, "prefix": f"{source_uri_base}:%"},
                )
                for r in rows:
                    try:
                        conn.execute(
                            text(
                                "INSERT INTO public.dash_knowledge_triples "
                                "(project_slug, subject, predicate, object, "
                                "source_type, source_id, confidence, inferred, "
                                "community, source_uri) VALUES "
                                "(:slug, :s, :p, :o, :st, :sid, :c, FALSE, NULL, :uri)"
                            ),
                            {
                                "slug": self.project_slug,
                                "s": r["subject"], "p": r["predicate"],
                                "o": r["object"],
                                "st": r["source_type"], "sid": r["source_id"],
                                "c": r["confidence"], "uri": r["source_uri"],
                            },
                        )
                        inserted += 1
                    except Exception:  # noqa: BLE001
                        # Older schema without source_uri column — retry without it
                        try:
                            conn.execute(
                                text(
                                    "INSERT INTO public.dash_knowledge_triples "
                                    "(project_slug, subject, predicate, object, "
                                    "source_type, source_id, confidence) VALUES "
                                    "(:slug, :s, :p, :o, :st, :sid, :c)"
                                ),
                                {
                                    "slug": self.project_slug,
                                    "s": r["subject"], "p": r["predicate"],
                                    "o": r["object"],
                                    "st": r["source_type"], "sid": r["source_id"],
                                    "c": r["confidence"],
                                },
                            )
                            inserted += 1
                        except Exception:  # noqa: BLE001
                            pass
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("kg_triples insert failed")
        return inserted

    # ---- Step 12: langextract ----------------------------------------------

    async def _step_langextract(self) -> dict[str, Any]:
        """Extract grounded facts from training artifacts (codex/domain JSON).

        SQL providers have no source documents — we run langextract on the LLM
        text we already produced during codex_enrich + domain_knowledge.
        """
        knowledge_dir = self._knowledge_dir()
        artifact_text = await asyncio.to_thread(
            self._collect_artifact_text, knowledge_dir
        )
        if not artifact_text or len(artifact_text) < 50:
            return {"message": "no artifacts to extract", "facts": 0, "method": "skipped"}

        method = "regex"
        facts: list[dict[str, Any]] = []
        try:
            facts = await asyncio.to_thread(self._run_langextract, artifact_text)
            if facts:
                method = "langextract"
        except Exception as exc:  # noqa: BLE001
            logger.info("langextract unavailable or failed (%s); using regex fallback", exc)

        if not facts:
            facts = await asyncio.to_thread(self._regex_extract_facts, artifact_text)
            method = "regex"

        # Persist
        saved = await asyncio.to_thread(self._persist_langextract_facts, facts)
        await asyncio.to_thread(
            self._write_json,
            knowledge_dir / "langextract" / "grounded_facts.json",
            facts,
        )
        return {
            "message": f"{saved} fact(s) via {method}",
            "facts": saved,
            "method": method,
        }

    def _collect_artifact_text(self, knowledge_dir: Path) -> str:
        """Concatenate codex + domain JSON files into a single text blob."""
        chunks: list[str] = []
        for sub in ("codex", "domain"):
            d = knowledge_dir / sub
            if not d.exists():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    chunks.append(f"=== {sub}/{p.name} ===\n{p.read_text(encoding='utf-8')}")
                except Exception:  # noqa: BLE001
                    continue
        return "\n\n".join(chunks)

    def _run_langextract(self, all_text: str) -> list[dict[str, Any]]:
        """Try langextract; raises on ImportError to trigger fallback."""
        import langextract as lx  # noqa: F401  # raises ImportError when missing

        import os
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return []

        from dash.settings import TRAINING_MODEL  # type: ignore

        examples = [
            lx.ExampleData(
                text=(
                    "Plant efficiency reached 87% in March 2025. "
                    "Revenue was $4.2M, up 12% YoY. "
                    "Defect rate must stay below 1%."
                ),
                extractions=[
                    lx.Extraction(extraction_class="kpi", extraction_text="Plant efficiency: 87%",
                                  attributes={"period": "March 2025"}),
                    lx.Extraction(extraction_class="kpi", extraction_text="Revenue: $4.2M, up 12% YoY",
                                  attributes={"trend": "up 12%"}),
                    lx.Extraction(extraction_class="business_rule",
                                  extraction_text="Defect rate must stay below 1%",
                                  attributes={"threshold": "1%"}),
                ],
            ),
        ]
        text_chunk = all_text[:8000]
        annotated = lx.extract(
            text_chunk,
            prompt_description=(
                "Extract KPIs, metrics, decisions, business rules, risks, targets, "
                "thresholds with specific numbers, dates, percentages."
            ),
            examples=examples,
            model_id=TRAINING_MODEL,
            api_key=api_key,
            max_workers=3,
            extraction_passes=1,
        )
        if not annotated:
            return []
        docs_list = annotated if isinstance(annotated, list) else [annotated]
        out: list[dict[str, Any]] = []
        for doc in docs_list:
            for ext in (getattr(doc, "extractions", []) or []):
                fact_text = getattr(ext, "extraction_text", str(ext))
                fact_class = getattr(ext, "extraction_class", "fact")
                ci = getattr(ext, "char_interval", None)
                cs = getattr(ci, "start_pos", None) if ci else None
                ce = getattr(ci, "end_pos", None) if ci else None
                attrs = getattr(ext, "attributes", None) or {}
                out.append({
                    "text": fact_text,
                    "type": fact_class,
                    "char_start": cs,
                    "char_end": ce,
                    "attributes": attrs,
                    "grounded": True,
                    "confidence": 0.85,
                })
        # cap cost
        return out[:30]

    def _regex_extract_facts(self, all_text: str) -> list[dict[str, Any]]:
        """Cheap fallback when langextract is unavailable."""
        import re
        patterns = [
            (re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%"), "percentage"),
            (re.compile(r"\$\s?(\d[\d,\.]*)(?:\s?[KMB])?"), "currency"),
            (re.compile(r"\btarget\s*=\s*([^\n,;]+)", re.IGNORECASE), "target"),
            (re.compile(r"\bthreshold\s*[><=]+\s*([^\n,;]+)", re.IGNORECASE), "threshold"),
        ]
        facts: list[dict[str, Any]] = []
        for rx, kind in patterns:
            for m in rx.finditer(all_text):
                snippet = all_text[max(0, m.start() - 40):min(len(all_text), m.end() + 40)]
                facts.append({
                    "text": snippet.strip(),
                    "type": kind,
                    "char_start": m.start(),
                    "char_end": m.end(),
                    "attributes": {"match": m.group(0)},
                    "grounded": True,
                    "confidence": 0.4,
                })
                if len(facts) >= 30:
                    return facts
        return facts

    def _persist_langextract_facts(self, facts: list[dict[str, Any]]) -> int:
        if not facts:
            return 0
        saved = 0
        try:
            with self.dash_engine.connect() as conn:
                for f in facts:
                    fact_text = (f.get("text") or "").strip()[:1000]
                    if len(fact_text) < 4:
                        continue
                    type_tag = (f.get("type") or "fact").upper()
                    cs = f.get("char_start")
                    ce = f.get("char_end")
                    pos = f" [chars {cs}-{ce}]" if cs is not None else ""
                    memory_text = f"[{type_tag}] {fact_text}{pos}"
                    try:
                        conn.execute(
                            text(
                                "INSERT INTO public.dash_memories "
                                "(project_slug, scope, fact, source, source_id) "
                                "VALUES (:slug, 'project', :fact, 'langextract', :sid) "
                                "ON CONFLICT DO NOTHING"
                            ),
                            {
                                "slug": self.project_slug,
                                "fact": memory_text,
                                "sid": self.source_id,
                            },
                        )
                        saved += 1
                    except Exception:  # noqa: BLE001
                        try:
                            conn.execute(
                                text(
                                    "INSERT INTO public.dash_memories "
                                    "(project_slug, scope, fact, source) "
                                    "VALUES (:slug, 'project', :fact, 'langextract') "
                                    "ON CONFLICT DO NOTHING"
                                ),
                                {"slug": self.project_slug, "fact": memory_text},
                            )
                            saved += 1
                        except Exception:  # noqa: BLE001
                            pass
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("persist_langextract_facts failed")
        return saved

    # ---- Stub steps ---------------------------------------------------------

    async def _step_todo(self, step: str | None = None) -> dict[str, Any]:
        """Placeholder for steps that still call into ``app.upload``.

        TODO Phase 4.x: migrate hierarchy_detect, qa_verify, relationships,
        persona, domain_knowledge, kg_triples, langextract from
        ``app.upload`` helpers (operate on ``proj_{slug}`` engine) to use
        ``provider.engine_ro`` directly.
        """
        logger.info(
            "ProviderTrainer step %s: TODO (Phase 4.x) — calling existing helpers",
            step,
        )
        return {"message": f"TODO: {step}", "cost_usd": 0.0}

    # ---- Persistence helpers ------------------------------------------------

    def _insert_run_row(self) -> None:
        try:
            with self.dash_engine.connect() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO public.dash_source_training_runs "
                        "(source_id, project_slug, status, current_step, total_steps, cost_usd) "
                        "VALUES (:sid, :slug, 'running', :step, :total, 0) RETURNING id"
                    ),
                    {
                        "sid": self.source_id,
                        "slug": self.project_slug,
                        "step": STEPS[0],
                        "total": len(STEPS),
                    },
                ).fetchone()
                conn.commit()
                self.run_id = int(row[0]) if row else None
        except Exception:  # noqa: BLE001
            logger.exception("Failed to insert dash_source_training_runs row")

    def _update_run_row(self, *, current_step: str) -> None:
        if not self.run_id:
            return
        try:
            with self.dash_engine.connect() as conn:
                conn.execute(
                    text(
                        "UPDATE public.dash_source_training_runs "
                        "SET current_step = :step, cost_usd = :cost "
                        "WHERE id = :id"
                    ),
                    {"step": current_step, "cost": self.cost_usd, "id": self.run_id},
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to update training run row")

    def _finalize_run_row(
        self, success: bool, error: str | None, duration_seconds: int
    ) -> None:
        if not self.run_id:
            return
        try:
            with self.dash_engine.connect() as conn:
                conn.execute(
                    text(
                        "UPDATE public.dash_source_training_runs "
                        "SET status = :status, completed_at = NOW(), "
                        "    duration_seconds = :dur, cost_usd = :cost, error = :err "
                        "WHERE id = :id"
                    ),
                    {
                        "status": "completed" if success else "failed",
                        "dur": duration_seconds,
                        "cost": self.cost_usd,
                        "err": (error or "")[:1000] or None,
                        "id": self.run_id,
                    },
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to finalize training run row")

    def _write_data_source_jsonb(self, column: str, payload: dict[str, Any]) -> None:
        """Update a JSONB column on dash_data_sources for this source."""
        if column not in {"drift_baseline", "last_watermark"}:
            raise ValueError(f"refusing to write unknown column {column!r}")
        try:
            with self.dash_engine.connect() as conn:
                conn.execute(
                    text(
                        f"UPDATE public.dash_data_sources "
                        f"SET {column} = :payload, updated_at = NOW() "
                        f"WHERE id = :id"
                    ),
                    {"payload": json.dumps(payload), "id": self.source_id},
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to write %s for source %s", column, self.source_id
            )

    # ---- Filesystem helpers ------------------------------------------------

    def _knowledge_dir(self) -> Path:
        d = self.knowledge_root / self.project_slug / f"source_{self.source_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write — matches the pattern used by the rest of the codebase.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, default=str, ensure_ascii=False, indent=2)
        tmp.replace(path)


# ---------------------------------------------------------------------------
# Local utilities
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Strip filesystem-unfriendly characters for per-table JSON filenames."""
    out = []
    for ch in str(name):
        out.append(ch if (ch.isalnum() or ch in ("_", "-", ".")) else "_")
    return "".join(out)[:120] or "table"


def _coerce_scalar(v: Any) -> Any:
    """JSON-friendly coercion for cursor scalars (Decimals, datetimes…)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _try_parse_json(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except Exception:  # noqa: BLE001
        return None
    return parsed if isinstance(parsed, dict) else None


def _try_parse_json_list(content: str | None) -> list[Any] | None:
    """Parse a JSON list. Tolerate ```json fences and trailing commentary."""
    if not content:
        return None
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[:-3]
    raw = raw.strip()
    # Common case: object wrapping a list — try direct first, then bracket scan.
    try:
        parsed = json.loads(raw)
    except Exception:  # noqa: BLE001
        # Fallback: extract first [...] block.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
        except Exception:  # noqa: BLE001
            return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return None


def _try_parse_json_array(content: str | None) -> list[Any] | None:
    """Parse an LLM response into a JSON array. Tolerates ```json fences and
    leading/trailing prose by extracting the first ``[...]`` block."""
    if not content:
        return None
    s = content.strip()
    # Strip code fences
    if s.startswith("```"):
        s = s.strip("`")
        # Drop optional 'json' language tag on first line
        first_nl = s.find("\n")
        if first_nl != -1 and s[:first_nl].strip().lower() in ("json", ""):
            s = s[first_nl + 1:]
        s = s.strip().rstrip("`").strip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    # Fall back: locate first '[' .. matching ']'
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            if isinstance(parsed, list):
                return parsed
        except Exception:  # noqa: BLE001
            return None
    return None
