"""Optional training steps — additive layer on top of ProviderTrainer.

These steps run AFTER the core 14 steps when their config flag is enabled.
They never modify existing artifacts; they only write new sibling files.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import AsyncIterator

from dash.providers.trainer import ProviderTrainer, TrainEvent

logger = logging.getLogger(__name__)


# Default knowledge dir — relative to repo root, matches existing trainer
KNOWLEDGE_DIR = Path("knowledge")


async def classify_columns_step(provider, source_id: int, knowledge_dir: Path = KNOWLEDGE_DIR):
    """Run column classifier on artifacts produced by the core trainer.

    Yields TrainEvent objects. Reads catalog/profile/dimensions; writes
    column_classification.json. Optional LLM tier driven by
    provider.config.classifier_tiers.
    """
    # Local imports keep this file importable even if the parallel agent has
    # not yet shipped the classifier module.
    from dash.providers.column_classifier import classify_source  # type: ignore
    try:
        from dash.settings import training_llm_call  # type: ignore
    except Exception:  # pragma: no cover - defensive
        training_llm_call = None  # type: ignore

    cfg = getattr(provider, "config", {}) or {}
    tiers = cfg.get("classifier_tiers", ["stats", "regex", "name"])

    llm_fn = None
    if "llm" in tiers and training_llm_call is not None:
        def _llm(prompt: str, task: str = "extraction"):
            try:
                return training_llm_call(prompt, task=task)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("LLM call failed in classifier: %s", e)
                return None
        llm_fn = _llm

    embed_fn = None  # Phase 5+: wire to db.session.get_active_embedder()

    project_slug = getattr(provider, "project_slug", None)

    yield TrainEvent(
        step="classify_columns",
        index=15,
        total=15,
        status="start",
        message=f"tiers={tiers}",
    )

    t0 = time.time()
    try:
        out_path = await asyncio.to_thread(
            classify_source,
            knowledge_dir=knowledge_dir,
            project_slug=project_slug,
            source_id=source_id,
            llm_call_fn=llm_fn,
            embed_fn=embed_fn,
        )
        out_name = getattr(out_path, "name", str(out_path))
        yield TrainEvent(
            step="classify_columns",
            index=15,
            total=15,
            status="done",
            message=f"wrote {out_name}",
            duration_ms=int((time.time() - t0) * 1000),
        )
    except Exception as exc:
        logger.exception("classify_columns_step failed")
        yield TrainEvent(
            step="classify_columns",
            index=15,
            total=15,
            status="error",
            message=str(exc)[:300],
            duration_ms=int((time.time() - t0) * 1000),
        )


class EnhancedProviderTrainer(ProviderTrainer):
    """Subclass that adds optional steps after the core run.

    Default-on steps run automatically. Opt out via the step's flag in
    ``provider.config`` (e.g. ``disable_classify=True``). Off-by-default
    steps must be enabled via ``enable_<step_name>=True``.
    """

    # (step_name, fn, default_on, opt_out_flag)
    OPTIONAL_STEPS = [
        ("classify_columns", classify_columns_step, True, "disable_classify"),
    ]

    async def run(self) -> AsyncIterator[TrainEvent]:
        async for ev in super().run():
            yield ev

        cfg = getattr(self.provider, "config", {}) or {}

        # Admin gate — auto_classify_on_train
        try:
            from dash.admin.settings import get_setting
            auto_classify = bool(get_setting(
                "auto_classify_on_train",
                project_slug=getattr(self.provider, "project_slug", None),
            ))
            if not auto_classify:
                cfg["disable_classify"] = True
        except Exception:
            pass

        # Admin gate — auto_load_seeds
        try:
            from dash.admin.settings import get_setting
            if not get_setting(
                "auto_load_seeds",
                project_slug=getattr(self.provider, "project_slug", None),
            ):
                cfg["disable_seeds"] = True
        except Exception:
            pass

        for name, fn, default_on, opt_out_flag in self.OPTIONAL_STEPS:
            if cfg.get(opt_out_flag, False):
                logger.info(
                    "Skipping optional step %s — disabled via %s",
                    name,
                    opt_out_flag,
                )
                continue
            if not default_on and not cfg.get(f"enable_{name}", False):
                continue
            try:
                async for ev in fn(self.provider, self.source_id):
                    yield ev
            except Exception as e:
                logger.exception("Optional step %s failed: %s", name, e)
                yield TrainEvent(
                    step=name,
                    index=99,
                    total=99,
                    status="error",
                    message=str(e)[:300],
                )


