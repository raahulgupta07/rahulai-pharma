"""Tests for EnhancedProviderTrainer + classify_columns_step.

Mocks both the classifier module and the parent ProviderTrainer so the
tests run without a database connection or trained artifacts on disk.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Inject a stub classifier module before training_steps_v2 is imported.
# The stub is overridable per-test via _stub_module().
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_classifier(monkeypatch, tmp_path):
    calls = {"classify_source": 0, "last_kwargs": None}

    def classify_source(
        knowledge_dir,
        project_slug,
        source_id,
        llm_call_fn=None,
        embed_fn=None,
    ):
        calls["classify_source"] += 1
        calls["last_kwargs"] = {
            "knowledge_dir": knowledge_dir,
            "project_slug": project_slug,
            "source_id": source_id,
            "llm_call_fn": llm_call_fn,
            "embed_fn": embed_fn,
        }
        out = Path(tmp_path) / f"col_class_{source_id}.json"
        out.write_text("{}")
        return out

    mod = types.ModuleType("dash.providers.column_classifier")
    mod.classify_source = classify_source  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dash.providers.column_classifier", mod)
    return calls


@pytest.fixture
def steps_module(stub_classifier):
    # Force a fresh import each test so the stub is honoured
    if "dash.providers.training_steps_v2" in sys.modules:
        del sys.modules["dash.providers.training_steps_v2"]
    import dash.providers.training_steps_v2 as m  # noqa: WPS433

    return m


# ---------------------------------------------------------------------------
# classify_columns_step direct
# ---------------------------------------------------------------------------

def _collect(agen):
    out = []

    async def go():
        async for ev in agen:
            out.append(ev)

    asyncio.get_event_loop().run_until_complete(go())
    return out


def _new_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def test_classify_step_emits_start_and_done(steps_module, stub_classifier, tmp_path):
    provider = MagicMock()
    provider.project_slug = "proj_x"
    provider.config = {"classifier_tiers": ["stats", "regex"]}

    loop = _new_loop()
    try:
        events = []

        async def run():
            async for ev in steps_module.classify_columns_step(
                provider, source_id=1, knowledge_dir=tmp_path
            ):
                events.append(ev)

        loop.run_until_complete(run())
    finally:
        loop.close()

    statuses = [getattr(e, "status", None) for e in events]
    assert "start" in statuses
    assert "done" in statuses
    assert stub_classifier["classify_source"] == 1


def test_classify_step_passes_llm_when_tier_enabled(
    steps_module, stub_classifier, tmp_path, monkeypatch
):
    # Stub dash.settings.training_llm_call so the step's local import succeeds
    settings_stub = types.ModuleType("dash.settings")
    settings_stub.training_llm_call = lambda prompt, task="extraction": "ok"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dash.settings", settings_stub)

    provider = MagicMock()
    provider.project_slug = "proj_y"
    provider.config = {"classifier_tiers": ["stats", "llm"]}

    loop = _new_loop()
    try:
        async def run():
            async for _ in steps_module.classify_columns_step(
                provider, source_id=2, knowledge_dir=tmp_path
            ):
                pass

        loop.run_until_complete(run())
    finally:
        loop.close()

    # llm_call_fn should be wired when "llm" is in tiers
    assert stub_classifier["last_kwargs"]["llm_call_fn"] is not None


def test_classify_step_no_llm_when_tier_absent(
    steps_module, stub_classifier, tmp_path
):
    provider = MagicMock()
    provider.project_slug = "proj_z"
    provider.config = {"classifier_tiers": ["stats", "regex", "name"]}

    loop = _new_loop()
    try:
        async def run():
            async for _ in steps_module.classify_columns_step(
                provider, source_id=3, knowledge_dir=tmp_path
            ):
                pass

        loop.run_until_complete(run())
    finally:
        loop.close()

    assert stub_classifier["last_kwargs"]["llm_call_fn"] is None


def test_classify_step_handles_exception_and_emits_error(
    steps_module, monkeypatch, tmp_path
):
    # Replace the stub so it raises
    bad_mod = types.ModuleType("dash.providers.column_classifier")

    def boom(**kwargs):
        raise RuntimeError("simulated classifier failure")

    bad_mod.classify_source = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dash.providers.column_classifier", bad_mod)

    provider = MagicMock()
    provider.project_slug = "proj_err"
    provider.config = {}

    loop = _new_loop()
    try:
        events = []

        async def run():
            async for ev in steps_module.classify_columns_step(
                provider, source_id=4, knowledge_dir=tmp_path
            ):
                events.append(ev)

        loop.run_until_complete(run())
    finally:
        loop.close()

    statuses = [getattr(e, "status", None) for e in events]
    assert "error" in statuses


def test_train_event_shape(steps_module, stub_classifier, tmp_path):
    provider = MagicMock()
    provider.project_slug = "proj_evt"
    provider.config = {}

    loop = _new_loop()
    try:
        events = []

        async def run():
            async for ev in steps_module.classify_columns_step(
                provider, source_id=5, knowledge_dir=tmp_path
            ):
                events.append(ev)

        loop.run_until_complete(run())
    finally:
        loop.close()

    for ev in events:
        # TrainEvent has these attributes
        for attr in ("step", "index", "total", "status"):
            assert hasattr(ev, attr), f"missing {attr} on event"
        assert ev.step == "classify_columns"


# ---------------------------------------------------------------------------
# EnhancedProviderTrainer
# ---------------------------------------------------------------------------

def _make_enhanced(steps_module, provider, monkeypatch):
    """Build an EnhancedProviderTrainer instance with the parent's heavy
    __init__ + run() bypassed via monkeypatch."""
    from dash.providers.trainer import ProviderTrainer, TrainEvent

    async def fake_parent_run(self):
        yield TrainEvent(step="catalog", index=1, total=14, status="done")
        yield TrainEvent(
            step="__result__", index=14, total=14, status="done", message="ok"
        )

    monkeypatch.setattr(ProviderTrainer, "run", fake_parent_run)

    cls = steps_module.EnhancedProviderTrainer
    inst = cls.__new__(cls)
    inst.provider = provider
    inst.source_id = 10
    inst.project_slug = getattr(provider, "project_slug", "p")
    return inst


def test_enhanced_no_extra_when_flag_off(steps_module, stub_classifier, monkeypatch):
    provider = MagicMock()
    provider.project_slug = "p1"
    provider.config = {"disable_classify": True}

    trainer = _make_enhanced(steps_module, provider, monkeypatch)

    loop = _new_loop()
    try:
        events = []

        async def go():
            async for ev in trainer.run():
                events.append(ev)

        loop.run_until_complete(go())
    finally:
        loop.close()

    steps = [e.step for e in events]
    assert "classify_columns" not in steps
    assert stub_classifier["classify_source"] == 0


def test_enhanced_runs_optional_when_flag_on(steps_module, stub_classifier, tmp_path, monkeypatch):
    provider = MagicMock()
    provider.project_slug = "p2"
    provider.config = {}

    trainer = _make_enhanced(steps_module, provider, monkeypatch)

    loop = _new_loop()
    try:
        events = []

        async def go():
            async for ev in trainer.run():
                events.append(ev)

        loop.run_until_complete(go())
    finally:
        loop.close()

    steps = [e.step for e in events]
    assert "classify_columns" in steps
    assert stub_classifier["classify_source"] == 1


def test_enhanced_optional_failure_does_not_break_run(
    steps_module, monkeypatch, tmp_path
):
    bad_mod = types.ModuleType("dash.providers.column_classifier")

    def boom(**kwargs):
        raise RuntimeError("bad")

    bad_mod.classify_source = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dash.providers.column_classifier", bad_mod)

    provider = MagicMock()
    provider.project_slug = "p3"
    provider.config = {}

    trainer = _make_enhanced(steps_module, provider, monkeypatch)

    loop = _new_loop()
    try:
        events = []

        async def go():
            async for ev in trainer.run():
                events.append(ev)

        loop.run_until_complete(go())
    finally:
        loop.close()

    # Parent's __result__ event still emitted; an error event for the optional
    # step is also present.
    steps = [e.step for e in events]
    assert "__result__" in steps
    statuses = [(e.step, e.status) for e in events]
    assert any(
        s == "error" and step == "classify_columns" for step, s in statuses
    )
