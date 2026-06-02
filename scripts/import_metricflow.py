#!/usr/bin/env python3
"""CLI: import dbt MetricFlow YAML into a Dash project as MDL.

Usage:
    python scripts/import_metricflow.py --project <slug> --path <dir_or_file> [--dry-run]

Examples:
    # Dry-run: print translated MDL dict as YAML, don't touch DB
    python scripts/import_metricflow.py --project proj_demo --path ./metricflow/ --dry-run

    # Real install (calls install_metricflow → install_mdl)
    python scripts/import_metricflow.py --project proj_demo --path ./metricflow/

Exit code 0 on success, 1 on any errors (loader warnings, install failures).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Make repo root importable when run from anywhere
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import yaml  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Import dbt MetricFlow YAML → Dash MDL pack",
    )
    ap.add_argument("--project", required=True,
                    help="Dash project slug (e.g. proj_demo_pharmacy)")
    ap.add_argument("--path", required=True,
                    help="Directory or single file with MetricFlow YAML")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print translated MDL dict as YAML; skip DB writes")
    ap.add_argument("--verbose", action="store_true",
                    help="Verbose logging")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not os.path.exists(args.path):
        print(f"ERROR: path not found: {args.path}", file=sys.stderr)
        return 1

    # Import after sys.path setup
    from dash.semantic.metricflow_loader import (
        load_metricflow_dir,
        metricflow_to_mdl,
        install_metricflow,
    )

    if args.dry_run:
        print(f"# DRY RUN — project={args.project} path={args.path}\n")
        mf = load_metricflow_dir(args.path)
        pack = metricflow_to_mdl(mf)
        # Strip private keys for the YAML emission, but include summaries
        warnings = pack.pop("_warnings", [])
        skipped = pack.pop("_skipped", [])
        out = yaml.safe_dump(pack, sort_keys=False, default_flow_style=False)
        print(out)
        print("\n# ─── Summary ───")
        print(f"# semantic_models loaded : {len(mf.get('semantic_models') or [])}")
        print(f"# metrics loaded         : {len(mf.get('metrics') or [])}")
        print(f"# MDL models             : {len(pack.get('models') or [])}")
        print(f"# metric_definitions     : {len(pack.get('metric_definitions') or [])}")
        print(f"# warnings               : {len(warnings)}")
        for w in warnings:
            print(f"#   - {w}")
        print(f"# skipped                : {len(skipped)}")
        for s in skipped:
            print(f"#   - {s.get('name')}: {s.get('reason')}")
        return 0 if not skipped else 1

    # Real install
    result = install_metricflow(args.project, args.path)

    print("─── Import Summary ───")
    print(f"project           : {args.project}")
    print(f"path              : {args.path}")
    print(f"ok                : {result.get('ok')}")
    print(f"models_imported   : {result.get('models_imported', 0)}")
    print(f"metrics_imported  : {result.get('metrics_imported', 0)}")
    skipped = result.get("skipped") or []
    warnings = result.get("warnings") or []
    print(f"warnings          : {len(warnings)}")
    for w in warnings:
        print(f"  - {w}")
    print(f"skipped           : {len(skipped)}")
    for s in skipped:
        if isinstance(s, dict):
            print(f"  - {s.get('name')}: {s.get('reason')}")
        else:
            print(f"  - {s}")
    install_result = result.get("install_result") or {}
    if install_result.get("error"):
        print(f"install error     : {install_result.get('error')}")

    has_errors = (
        not result.get("ok")
        or bool(install_result.get("error"))
    )
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
