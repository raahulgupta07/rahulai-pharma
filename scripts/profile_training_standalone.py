#!/usr/bin/env python3
"""Standalone training profiler — runs the REAL TRAIN ALL step functions against a
THROWAWAY Postgres, with per-step timing + LLM cost, and prints/writes a ranked
bottleneck report.

NEVER touches the production dash DB:
  - DB target must be the throwaway (port 55432 / host 'pg-profile' / PROFILE_DB=1);
    db_setup.assert_throwaway enforces this before any destructive op.
  - Env (DB_HOST/DB_PORT/DATABASE_URL) is set to the throwaway BEFORE importing
    app.upload, so the app's module-global db_url points at pg-profile, not prod.

Usage (env must point at throwaway PG):
    PROFILE_DB=1 DATABASE_URL=postgresql://ai:ai@localhost:55432/ai \
      python scripts/profile_training_standalone.py --slug profile_pharma --cold --warm
    # add --mock-llm to isolate code+SQL time from LLM time

Modes:
    --cold      fresh schema, real LLM (full baseline)            [default if no mode]
    --warm      second run w/o reset (cache/skip measurement)
    --mock-llm  monkeypatch training_llm_call -> canned JSON ($0)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# ── Resolve throwaway DB URL + STAMP ENV *BEFORE* importing app code ──────────
def _resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "55432")
        user = os.environ.get("DB_USER", "ai")
        pw = os.environ.get("DB_PASS", "ai")
        db = os.environ.get("DB_DATABASE", "ai")
        url = f"postgresql://{user}:{pw}@{host}:{port}/{db}"
    # Force psycopg3 driver (image has 'psycopg', not 'psycopg2'); SQLAlchemy
    # defaults plain postgresql:// to psycopg2 which isn't installed.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url

DB_URL = _resolve_db_url()
# Force app modules (computed at import from env) onto the throwaway DB.
os.environ["DATABASE_URL"] = DB_URL
# Parse host/port back so the app's DB_HOST/DB_PORT-based db_url also lands here.
try:
    from urllib.parse import urlparse
    _p = urlparse(DB_URL)
    if _p.hostname:
        os.environ["DB_HOST"] = _p.hostname
    if _p.port:
        os.environ["DB_PORT"] = str(_p.port)
    if _p.username:
        os.environ["DB_USER"] = _p.username
    if _p.password:
        os.environ["DB_PASS"] = _p.password
    if _p.path and len(_p.path) > 1:
        os.environ["DB_DATABASE"] = _p.path.lstrip("/")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.profile import db_setup, instrument, report  # noqa: E402


def _run_migrations() -> None:
    """Create all public.dash_* tables on the throwaway DB so step writes succeed."""
    try:
        from dash.db_runner.migrate import run_migrations
        run_migrations()
        print("[profile] migrations applied to throwaway DB")
    except Exception as e:
        print(f"[profile] migration run skipped/failed (steps may under-count writes): {e}")


def _mock_llm():
    """Monkeypatch training_llm_call -> instant canned JSON. Returns a restore fn."""
    import dash.settings as _s
    _orig = _s.training_llm_call

    def _fake(prompt, task="extraction", model=None):
        # tiny canned JSON that the 4-tier parsers accept
        return '{"ok": true, "items": [], "summary": "mock"}'

    _s.training_llm_call = _fake
    # also patch the symbol where modules imported it by-reference, best-effort
    patched = []
    for modname in ("app.upload", "dash.scope_deriver", "dash.learning.auto_configurator",
                    "dash.learning.subagent_synthesis", "dash.tools.knowledge_graph"):
        try:
            m = sys.modules.get(modname) or __import__(modname, fromlist=["x"])
            if hasattr(m, "training_llm_call"):
                m.training_llm_call = _fake
                patched.append(modname)
        except Exception:
            pass

    def _restore():
        _s.training_llm_call = _orig
        for modname in patched:
            try:
                sys.modules[modname].training_llm_call = _orig
            except Exception:
                pass
    return _restore


def _run_pipeline(slug: str, label: str) -> list[dict]:
    """Run the REAL per-table + tail steps under instrumentation. Returns timings."""
    import pandas as pd
    from pathlib import Path
    from sqlalchemy import text, inspect as sa_inspect
    from app import upload as U

    eng = db_setup.make_engine(DB_URL)
    schema = db_setup.derive_schema(slug)
    insp = sa_inspect(eng)
    tables = [t for t in insp.get_table_names(schema=schema)]
    print(f"[profile:{label}] tables: {tables}")

    knowledge_base = U.KNOWLEDGE_DIR / slug
    tables_dir = knowledge_base / "tables"
    business_dir = knowledge_base / "business"
    tables_dir.mkdir(parents=True, exist_ok=True)
    business_dir.mkdir(parents=True, exist_ok=True)

    targets = list(instrument.ALL_TARGETS) + ["app.upload:_run_auto_training"]
    instrument.reset_timings()

    with instrument.profile_calls(targets):
        # ── per-table ──
        for ti, tbl in enumerate(tables, start=1):
            try:
                df = pd.read_sql(f'SELECT * FROM "{schema}"."{tbl}" LIMIT 100', eng)
                col_analyses = [U._analyze_column(df[c]) for c in df.columns]
                sample_rows = df.head(10).to_dict("records")
                metadata = U._generate_metadata(tbl, df, col_analyses)
                biz_rules = U._generate_business_rules(tbl, col_analyses)
                U._run_auto_training(
                    slug, tbl, col_analyses, metadata, biz_rules, sample_rows,
                    tables_dir, business_dir,
                    master_run_id=None, table_index=ti, total_tables=len(tables),
                )
            except Exception as e:
                print(f"[profile:{label}] table {tbl} step error (non-fatal): {str(e)[:120]}")

        # ── tail (project-wide) ── each fail-soft; timing captured regardless
        def _safe(desc, fn):
            try:
                fn()
            except Exception as e:
                print(f"[profile:{label}] tail {desc} error (non-fatal): {str(e)[:120]}")

        _safe("knowledge_graph", lambda: __import__("dash.tools.knowledge_graph", fromlist=["x"]).build_knowledge_graph(slug))
        _safe("relationships", lambda: U._discover_relationships(slug) if hasattr(U, "_discover_relationships") else None)
        _safe("scope", lambda: __import__("dash.scope_deriver", fromlist=["x"]).derive_scope(slug))
        _safe("vertical_detect", lambda: __import__("dash.learning.auto_configurator", fromlist=["x"]).classify_vertical(slug))
        _safe("ml_models", lambda: __import__("dash.tools.ml_models", fromlist=["x"]).auto_create_models(slug, schema=schema))
        _safe("vector_backfill", lambda: U._enqueue_vector_backfill(slug) if hasattr(U, "_enqueue_vector_backfill") else None)
        _safe("eval_gen", lambda: U._generate_project_evals(slug) if hasattr(U, "_generate_project_evals") else None)
        _safe("evals", lambda: __import__("app.learning", fromlist=["x"])._run_evals_for_slug(slug))
        _safe("goals", lambda: __import__("dash.learning.goals_deriver", fromlist=["x"]).derive_goals(slug, force=True))

    return instrument.get_timings()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="profile_pharma")
    ap.add_argument("--data-dir", default="/tmp/profile_data")
    ap.add_argument("--cold", action="store_true")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--mock-llm", action="store_true")
    ap.add_argument("--out", default="docs/TRAINING_PROFILE.md")
    args = ap.parse_args()

    if not args.slug.startswith("profile_"):
        sys.exit("[profile] refusing: --slug must start with 'profile_' (safety)")

    # SAFETY: hard-stop unless throwaway DB.
    db_setup.assert_throwaway(DB_URL)
    print(f"[profile] DB = {DB_URL}")

    _run_migrations()

    eng = db_setup.make_engine(DB_URL)
    db_setup.reset_schema(eng, args.slug)
    counts = db_setup.load_csvs(eng, args.slug, args.data_dir)
    print(f"[profile] loaded: {counts}")
    db_setup.register_table_metadata(eng, args.slug)

    restore = _mock_llm() if args.mock_llm else None
    runs: dict[str, list[dict]] = {}
    try:
        label = "mock" if args.mock_llm else "cold"
        t0 = time.perf_counter()
        runs[label] = _run_pipeline(args.slug, label)
        wall_cold = time.perf_counter() - t0
        print(report.print_report(runs[label], run_label=label, total_wall_s=wall_cold))

        if args.warm:
            t1 = time.perf_counter()
            runs["warm"] = _run_pipeline(args.slug, "warm")
            wall_warm = time.perf_counter() - t1
            print(report.print_report(runs["warm"], run_label="warm", total_wall_s=wall_warm))
    finally:
        if restore:
            restore()

    try:
        report.write_markdown(
            args.out,
            cold=runs.get("cold") or runs.get("mock"),
            warm=runs.get("warm"),
            mock=runs.get("mock"),
            meta={"slug": args.slug, "db": DB_URL, "rows": counts},
        )
        print(f"[profile] report → {args.out}")
    except Exception as e:
        print(f"[profile] markdown write failed: {e}")


if __name__ == "__main__":
    main()
