"""Phase-6 harness for the dynamic / self-adapting schema.

Runs INSIDE cp-api (needs DB env + the app modules):

    docker exec cp-api python /app/scripts/verify_dynamic_schema.py

Exercises the whole spine without touching live citypharma data:
  - colmap resolve / set_map / decide roundtrip (throwaway slug)
  - contract drift diff + propose_remaps (rename auto-map vs pending)
  - schema_events log + read
  - resolve() correctness for the real seeded citypharma registry
  - build_shop_flat still rebuilds real data to > 0 rows (no regression)

Prints PASS/FAIL per check; exits non-zero if any check fails. The throwaway
test slug rows are deleted at the end (live data never mutated).
"""
from __future__ import annotations

import sys
import traceback

import pandas as pd

from db.session import get_sql_engine
from sqlalchemy import text

from dash.ingest import colmap
from dash.ingest.contract import check_against_contract, infer_contract
from dash.ingest.schema_events import log_schema_event

TEST_SLUG = "__dynschema_test__"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, note: str = "") -> None:
    _results.append((name, bool(cond), note))


def _cleanup() -> None:
    try:
        eng = get_sql_engine()
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM public.dash_column_map WHERE project_slug = :p"),
                         {"p": TEST_SLUG})
            conn.execute(text("DELETE FROM public.dash_schema_events WHERE project_slug = :p"),
                         {"p": TEST_SLUG})
    except Exception:
        pass


def main() -> int:
    # 1. seed + resolve fallback chain ------------------------------------------
    colmap.seed_defaults(TEST_SLUG)
    check("seed: resolve brand -> brand_name",
          colmap.resolve(TEST_SLUG, "catalog", "brand") == "brand_name")
    check("seed: resolve stock cost -> weighted_cost_price",
          colmap.resolve(TEST_SLUG, "stock", "cost") == "weighted_cost_price")
    check("resolve unknown -> identity",
          colmap.resolve(TEST_SLUG, "catalog", "zzz_unknown") == "zzz_unknown")

    # 2. pending map is NOT used until confirmed --------------------------------
    colmap.set_map(TEST_SLUG, "catalog", "brand", "brand_en",
                   confidence=0.6, source="auto", status="pending")
    check("pending remap not active (resolve still default)",
          colmap.resolve(TEST_SLUG, "catalog", "brand") == "brand_name")
    colmap.decide_map(TEST_SLUG, "catalog", "brand", "confirm")
    check("confirm flips pending -> active",
          colmap.resolve(TEST_SLUG, "catalog", "brand") == "brand_en")
    colmap.decide_map(TEST_SLUG, "catalog", "brand", "reject")
    check("reject -> resolve falls back to default",
          colmap.resolve(TEST_SLUG, "catalog", "brand") == "brand_name")

    # restore the seed mapping for the propose test
    colmap.set_map(TEST_SLUG, "catalog", "brand", "brand_name",
                   confidence=1.0, source="seed", status="active")

    # 3. contract drift diff + propose_remaps -----------------------------------
    base = pd.DataFrame({"article_code": ["1", "2"], "brand_name": ["a", "b"],
                         "generic_name": ["x", "y"]})
    contract = infer_contract(base, TEST_SLUG, "catalog", "base.csv")
    drifted = pd.DataFrame({"article_code": ["1", "2"], "brand_en": ["a", "b"],
                            "generic_name": ["x", "y"]})
    chk = check_against_contract(contract, drifted)
    check("drift detected on rename", chk.get("verdict") == "drift", str(chk.get("diff")))
    props = colmap.propose_remaps(TEST_SLUG, "catalog", chk.get("diff"))
    brand_prop = [p for p in props if p.get("logical_name") == "brand"]
    check("propose_remaps maps brand -> brand_en",
          bool(brand_prop) and brand_prop[0]["to"] == "brand_en",
          str(props))

    # 4. schema_events log + read ----------------------------------------------
    log_schema_event(TEST_SLUG, "catalog", "adapt", {"added": ["brand_en"], "mode": "test"})
    try:
        eng = get_sql_engine()
        with eng.connect() as conn:
            n = conn.execute(text(
                "SELECT count(*) FROM public.dash_schema_events WHERE project_slug = :p"),
                {"p": TEST_SLUG}).scalar()
        check("schema_event logged + readable", (n or 0) >= 1, f"{n} rows")
    except Exception as e:
        check("schema_event logged + readable", False, str(e))

    # 5. real citypharma registry correctness -----------------------------------
    check("citypharma resolve brand -> brand_name",
          colmap.resolve("citypharma", "catalog", "brand") == "brand_name")
    check("citypharma resolve stock_qty -> stock_qty",
          colmap.resolve("citypharma", "stock", "stock_qty") == "stock_qty")

    # 6. build_shop_flat no regression (real data) ------------------------------
    try:
        from scripts.build_shop_flat import run as build_shop_flat
        out = build_shop_flat()
        check("build_shop_flat ok + rows > 0",
              bool(out.get("ok")) and int(out.get("rows", 0)) > 0,
              f"rows={out.get('rows')} art={out.get('art_table')} stock={out.get('stock_table')}")
    except Exception as e:
        check("build_shop_flat ok + rows > 0", False, str(e))

    # report --------------------------------------------------------------------
    _cleanup()
    passed = sum(1 for _, ok, _ in _results if ok)
    print("\n=== dynamic-schema harness ===")
    for name, ok, note in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {note}" if note and not ok else ""))
    print(f"\n{passed}/{len(_results)} passed")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        _cleanup()
        rc = 2
    sys.exit(rc)
