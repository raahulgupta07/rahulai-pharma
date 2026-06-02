"""V2 training-pipeline step runner.

Backs the table ``public.dash_training_steps`` (UNIQUE(project_slug, name, scope)).
Each step is one unit of work, cached by a fingerprint (fp) of its inputs.

Caching semantics (P0):
    The runner persists only step *metadata* (status + fp + elapsed_ms), never the
    actual output object. The real output lives in-memory and is returned to the
    caller within a single process run.

    cache HIT  (stored status == 'done' and stored fp == current fp):
        -> skip the work, mark the step 'skipped', and return ``None``.
        The caller MUST be written so the persisted *side-effects* (e.g. brain
        seeds, applied templates) are what matter -- not the return value.
        Re-running those side-effects is exactly what we are avoiding.

    cache MISS / changed (no row, different fp, or prior status != 'done'):
        -> mark 'running', run ``execute_fn()``, then on success mark 'done',
        store the fp + elapsed_ms, and return the value ``execute_fn`` produced.

Graceful degradation:
    Every DB op is fail-soft. If the table does not exist yet (migration not
    applied), ``_load_row`` returns None, ``_upsert`` is a no-op, and ``run()``
    simply always executes ``execute_fn`` with no caching -- so the app works
    even before the migration lands.
"""

import hashlib
import json
import logging
import time
from typing import Any, Callable

from sqlalchemy import text

logger = logging.getLogger(__name__)


def compute_fp(inputs: Any) -> str:
    """Return sha256(canonical_json(inputs))[:16].

    Stable across runs (keys are sorted; non-serializable values fall back to
    ``str``). If JSON encoding fails entirely, fingerprint the ``str(inputs)``.
    """
    try:
        blob = json.dumps(inputs, sort_keys=True, default=str)
    except Exception:
        blob = str(inputs)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class StepRunner:
    """Cache-or-run executor for training pipeline steps."""

    def __init__(
        self,
        run_id: int | None,
        project_slug: str,
        logger_fn: Callable[[str], None] | None = None,
    ):
        self.run_id = run_id
        self.slug = project_slug
        self._log = logger_fn or (lambda m: None)

    def _engine(self):
        # Unguarded NullPool engine that can write to public.dash_* (bypasses RO guard).
        from dash.tools.skill_refinery import _get_engine

        return _get_engine()

    def _load_row(self, name: str, scope: str) -> dict | None:
        """Return {status, fp, output_ref} for the step, or None if absent/error."""
        try:
            eng = self._engine()
            with eng.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT status, fp, output_ref "
                        "FROM public.dash_training_steps "
                        "WHERE project_slug = :s AND name = :n AND scope = :sc"
                    ),
                    {"s": self.slug, "n": name, "sc": scope},
                ).mappings().first()
            if row is None:
                return None
            return {
                "status": row["status"],
                "fp": row["fp"],
                "output_ref": row["output_ref"],
            }
        except Exception as e:
            logger.debug("StepRunner._load_row(%s/%s) failed: %s", name, scope, e)
            return None

    def _upsert(
        self,
        name,
        scope,
        status,
        fp=None,
        output_ref=None,
        elapsed_ms=None,
        error=None,
        step_no=None,
    ):
        """Insert or update the step row. No-op on any DB error (fail-soft)."""
        try:
            # Conditional timestamp columns: started_at on running, finished_at on terminal.
            set_started = "now()" if status == "running" else None
            set_finished = (
                "now()" if status in ("done", "skipped", "failed") else None
            )

            params = {
                "run_id": self.run_id,
                "s": self.slug,
                "n": name,
                "sc": scope,
                "step_no": step_no,
                "status": status,
                "fp": fp,
                "output_ref": output_ref,
                "elapsed_ms": elapsed_ms,
                "error": error,
            }

            insert_started = set_started if set_started else "NULL"
            insert_finished = set_finished if set_finished else "NULL"

            update_clauses = [
                "run_id = EXCLUDED.run_id",
                "step_no = COALESCE(EXCLUDED.step_no, public.dash_training_steps.step_no)",
                "status = EXCLUDED.status",
                "fp = COALESCE(EXCLUDED.fp, public.dash_training_steps.fp)",
                "output_ref = COALESCE(EXCLUDED.output_ref, public.dash_training_steps.output_ref)",
                "elapsed_ms = EXCLUDED.elapsed_ms",
                "error = EXCLUDED.error",
                "updated_at = now()",
            ]
            if set_started:
                update_clauses.append("started_at = now()")
            if set_finished:
                update_clauses.append("finished_at = now()")

            sql = (
                "INSERT INTO public.dash_training_steps "
                "(run_id, project_slug, step_no, name, scope, status, fp, "
                " output_ref, elapsed_ms, error, started_at, finished_at, updated_at) "
                "VALUES (:run_id, :s, :step_no, :n, :sc, :status, :fp, "
                f":output_ref, :elapsed_ms, :error, {insert_started}, {insert_finished}, now()) "
                "ON CONFLICT (project_slug, name, scope) DO UPDATE SET "
                + ", ".join(update_clauses)
            )

            eng = self._engine()
            with eng.begin() as conn:
                conn.execute(text(sql), params)
        except Exception as e:
            logger.debug("StepRunner._upsert(%s/%s) failed: %s", name, scope, e)

    def run(
        self,
        name: str,
        execute_fn: Callable[[], Any],
        *,
        fp_inputs: Any,
        scope: str = "project",
        step_no: int | None = None,
    ) -> Any:
        """Cache-or-run one step.

        - Compute ``fp = compute_fp(fp_inputs)``.
        - If a prior row exists with status == 'done' and matching fp -> mark
          'skipped' and return ``None`` (cache HIT; side-effects already persisted).
        - Otherwise: mark 'running', call ``execute_fn()``; on success mark 'done'
          and store fp + elapsed_ms; on exception mark 'failed' + error.
        - Fail-soft: never raises. Returns ``None`` on failure or cache hit.

        Returns whatever ``execute_fn`` returned, or ``None`` on failure / cache hit.
        """
        fp = compute_fp(fp_inputs)

        prior = self._load_row(name, scope)
        if prior is not None and prior.get("status") == "done" and prior.get("fp") == fp:
            self._log(f"step {name}: cache hit (fp={fp})")
            self._upsert(name, scope, "skipped", fp=fp, step_no=step_no)
            return None

        self._upsert(name, scope, "running", fp=fp, step_no=step_no)

        t = time.perf_counter()
        try:
            result = execute_fn()
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - t) * 1000)
            err = str(e)
            self._log(f"step {name}: FAILED {err}")
            logger.warning("StepRunner step %s/%s failed: %s", name, scope, e)
            self._upsert(
                name,
                scope,
                "failed",
                fp=fp,
                elapsed_ms=elapsed_ms,
                error=err[:4000],
                step_no=step_no,
            )
            return None

        elapsed_ms = round((time.perf_counter() - t) * 1000)
        self._log(f"step {name}: ran {elapsed_ms}ms")
        self._upsert(
            name,
            scope,
            "done",
            fp=fp,
            elapsed_ms=elapsed_ms,
            step_no=step_no,
        )
        return result
