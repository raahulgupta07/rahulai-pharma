"""MetricFlow YAML → MDL_PACK importer.

Reads dbt MetricFlow YAML files (`semantic_models.yml` + `metrics.yml`) and
translates to MDL_PACK dict shape used by `dash.workflows.verticals.install_mdl`.

MetricFlow shape (dbt semantic layer):
  - semantic_models: name, model (ref()), entities, dimensions, measures
  - metrics: name, type (simple|ratio|derived|cumulative), type_params

MDL_PACK shape (Dash):
  - models: [{name, raw_table_aliases, virtual_columns, relationships}]
  - workflows: [{name, description, model, sql, action}]
  - metric_definitions (semantic SQL strings)

Unsupported features are skipped with a warning (never crash).

Reuses install_mdl signature from `dash/workflows/verticals/__init__.py`:
    install_mdl(project_slug, pack_name, owner_user_id=None) -> dict
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


# ── ref() extraction ────────────────────────────────────────────────────────

_REF_RE = re.compile(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)")


def _extract_table_name(model_str: str) -> str:
    """Extract table name from `ref('foo')` or accept raw name."""
    if not model_str:
        return ""
    m = _REF_RE.search(model_str)
    if m:
        return m.group(1)
    return str(model_str).strip()


# ── File walking ────────────────────────────────────────────────────────────

def _walk_yaml_files(path: str) -> tuple[list[str], list[str]]:
    """Walk dir; classify YAML files into (semantic_model_files, metric_files).

    Classification heuristic:
      - file content has `semantic_models:` key → semantic
      - file content has `metrics:` key → metrics
      - file may have both → in both lists
    """
    sem_files: list[str] = []
    met_files: list[str] = []
    p = Path(path)
    if p.is_file():
        candidates = [p]
    elif p.is_dir():
        candidates = list(p.rglob("*.yml")) + list(p.rglob("*.yaml"))
    else:
        return [], []

    for fp in candidates:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                continue
            if "semantic_models" in data:
                sem_files.append(str(fp))
            if "metrics" in data:
                met_files.append(str(fp))
        except Exception as e:
            logger.warning(f"skip {fp}: yaml parse failed ({e})")
    return sem_files, met_files


def _load_yaml(path: str) -> dict:
    """Load a single YAML file. Returns {} on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"yaml load failed for {path}: {e}")
        return {}


# ── Public API: file/dir loaders ────────────────────────────────────────────

def load_metricflow_dir(path: str) -> dict:
    """Walk dir, parse all *.yml/*.yaml, return merged MetricFlow data dict.

    Output shape:
        {
          "semantic_models": [...],
          "metrics": [...],
        }
    """
    sem_files, met_files = _walk_yaml_files(path)
    return load_metricflow_files(sem_files, met_files)


def load_metricflow_files(semantic_models: list[str],
                          metrics: list[str]) -> dict:
    """Load specific files. Merges all `semantic_models:` and `metrics:` lists."""
    sem_list: list[dict] = []
    met_list: list[dict] = []
    seen_files: set[str] = set()

    for fp in semantic_models:
        if fp in seen_files:
            continue
        seen_files.add(fp)
        data = _load_yaml(fp)
        for sm in (data.get("semantic_models") or []):
            if isinstance(sm, dict):
                sem_list.append(sm)

    for fp in metrics:
        # A file may be in both lists (has both keys) — re-load is fine,
        # but skip if already processed for metrics.
        data = _load_yaml(fp)
        for m in (data.get("metrics") or []):
            if isinstance(m, dict):
                met_list.append(m)

    return {"semantic_models": sem_list, "metrics": met_list}


# ── Pure translator: MetricFlow → MDL ───────────────────────────────────────

def metricflow_to_mdl(mf_data: dict) -> dict:
    """Pure translator: MetricFlow data dict → MDL_PACK dict.

    Returns dict with keys: name, models, workflows, metric_definitions,
    _warnings, _skipped.
    """
    warnings: list[str] = []
    skipped: list[dict] = []

    sem_models = mf_data.get("semantic_models") or []
    metrics = mf_data.get("metrics") or []

    # Build measure index: {(semantic_model_name, measure_name): {agg, expr, agg_time_dimension}}
    measure_index: dict[tuple[str, str], dict] = {}
    # Also flat measure index for metrics that reference measures by name only
    measure_by_name: dict[str, dict] = {}

    # ── Translate semantic_models → MDL models ──────────────────────────────
    mdl_models: list[dict] = []
    for sm in sem_models:
        name = sm.get("name")
        if not name:
            skipped.append({"name": "<unnamed semantic_model>",
                            "reason": "missing name"})
            continue

        model_ref = sm.get("model")
        raw_table = _extract_table_name(model_ref) if model_ref else ""
        raw_table_aliases = [raw_table] if raw_table else [name]

        # virtual_columns from dimensions + measures
        virtual_columns: list[dict] = []

        # Dimensions
        for dim in (sm.get("dimensions") or []):
            if not isinstance(dim, dict):
                continue
            dname = dim.get("name")
            if not dname:
                continue
            dtype_mf = (dim.get("type") or "categorical").lower()
            dtype = "timestamp" if dtype_mf == "time" else (
                "numeric" if dtype_mf == "number" else "string"
            )
            expr = dim.get("expr") or dname
            vc: dict[str, Any] = {
                "name": dname,
                "expression": expr,
                "type": dtype,
            }
            # Skip unsupported keys gracefully
            if "fill_nulls_with" in dim:
                warnings.append(
                    f"dimension {name}.{dname}: fill_nulls_with not supported, skipped"
                )
            virtual_columns.append(vc)

        # Measures → virtual_columns (so they can be referenced) +
        # also index for metrics resolution
        for meas in (sm.get("measures") or []):
            if not isinstance(meas, dict):
                continue
            mname = meas.get("name")
            if not mname:
                continue
            agg = (meas.get("agg") or "sum").lower()
            expr = meas.get("expr") or mname
            agg_time_dim = meas.get("agg_time_dimension")
            measure_index[(name, mname)] = {
                "agg": agg, "expr": expr,
                "agg_time_dimension": agg_time_dim,
                "model_name": name,
            }
            measure_by_name[mname] = measure_index[(name, mname)]
            # Surface measure as a virtual column: expression = `{agg}({expr})`
            # — most useful when referenced directly. Type: numeric.
            virtual_columns.append({
                "name": mname,
                "expression": f"{agg.upper()}({expr})",
                "type": "numeric",
            })

        # Entities → relationships
        relationships: list[dict] = []
        for ent in (sm.get("entities") or []):
            if not isinstance(ent, dict):
                continue
            ename = ent.get("name")
            etype = (ent.get("type") or "").lower()
            if not ename:
                continue
            expr = ent.get("expr") or ename
            if etype == "primary":
                # Note as virtual column for visibility (not a relationship)
                virtual_columns.append({
                    "name": ename,
                    "expression": expr,
                    "type": "string",
                    "_entity_role": "primary",
                })
            elif etype == "foreign":
                # Foreign entity = link to another model whose primary entity == ename
                relationships.append({
                    "model": ename,  # target model name; resolver matches at install
                    "on": f"{ename} = {ename}",
                    "type": "many_to_one",
                    "optional": True,
                    "_source_col": expr,
                })
            elif etype == "unique":
                virtual_columns.append({
                    "name": ename,
                    "expression": expr,
                    "type": "string",
                    "_entity_role": "unique",
                })

        mdl_models.append({
            "name": name,
            "raw_table_aliases": raw_table_aliases,
            "virtual_columns": virtual_columns,
            "relationships": relationships,
        })

    # ── Translate metrics → metric_definitions (semantic SQL strings) ──────
    metric_defs: list[dict] = []
    for m in metrics:
        if not isinstance(m, dict):
            continue
        mname = m.get("name")
        mtype = (m.get("type") or "simple").lower()
        if not mname:
            skipped.append({"name": "<unnamed metric>", "reason": "missing name"})
            continue

        tp = m.get("type_params") or {}

        # Detect unsupported features → warn + still try
        if m.get("filter"):
            warnings.append(
                f"metric {mname}: top-level filter not supported, skipped filter"
            )

        if mtype == "simple":
            measure_ref = tp.get("measure")
            measure_name = None
            if isinstance(measure_ref, dict):
                measure_name = measure_ref.get("name")
                if measure_ref.get("filter"):
                    warnings.append(
                        f"metric {mname}: measure filter not supported, skipped"
                    )
                if measure_ref.get("fill_nulls_with") is not None:
                    warnings.append(
                        f"metric {mname}: fill_nulls_with not supported, skipped"
                    )
            elif isinstance(measure_ref, str):
                measure_name = measure_ref
            if not measure_name:
                skipped.append({"name": mname,
                                "reason": "simple metric missing measure ref"})
                continue
            meas = measure_by_name.get(measure_name)
            if not meas:
                skipped.append({"name": mname,
                                "reason": f"measure '{measure_name}' not found"})
                continue
            agg = meas["agg"].upper()
            expr = meas["expr"]
            sql = f"SELECT {agg}({expr}) AS value FROM {meas['model_name']}"
            metric_defs.append({
                "name": mname,
                "type": "simple",
                "model": meas["model_name"],
                "sql": sql,
                "description": m.get("description", ""),
            })

        elif mtype == "ratio":
            num_ref = tp.get("numerator")
            den_ref = tp.get("denominator")
            num_name = num_ref.get("name") if isinstance(num_ref, dict) else num_ref
            den_name = den_ref.get("name") if isinstance(den_ref, dict) else den_ref
            if not num_name or not den_name:
                skipped.append({"name": mname,
                                "reason": "ratio missing numerator/denominator"})
                continue
            metric_defs.append({
                "name": mname,
                "type": "ratio",
                "numerator": num_name,
                "denominator": den_name,
                "description": m.get("description", ""),
            })

        elif mtype == "derived":
            expr = tp.get("expr")
            if not expr:
                skipped.append({"name": mname, "reason": "derived missing expr"})
                continue
            metric_defs.append({
                "name": mname,
                "type": "derived",
                "expression": expr,
                "metrics_referenced": [
                    (mm.get("name") if isinstance(mm, dict) else mm)
                    for mm in (tp.get("metrics") or [])
                ],
                "description": m.get("description", ""),
            })

        elif mtype == "cumulative":
            measure_ref = tp.get("measure")
            measure_name = (measure_ref.get("name")
                            if isinstance(measure_ref, dict) else measure_ref)
            window = tp.get("window")
            grain_to_date = tp.get("grain_to_date")
            metric_defs.append({
                "name": mname,
                "type": "cumulative",
                "measure": measure_name,
                "window": window,
                "grain_to_date": grain_to_date,
                "description": m.get("description", ""),
            })

        else:
            skipped.append({"name": mname,
                            "reason": f"unknown metric type: {mtype}"})

    # Strip non-MDL meta keys before emitting models (keep clean for install_mdl)
    for m in mdl_models:
        for vc in m["virtual_columns"]:
            vc.pop("_entity_role", None)
        for rel in m["relationships"]:
            rel.pop("_source_col", None)

    pack = {
        "name": "metricflow_imported",
        "vertical": "MetricFlow Import",
        "description": f"Imported from {len(sem_models)} semantic_models "
                       f"+ {len(metrics)} metrics",
        "detect": {
            "required_tables_any": [
                _extract_table_name(sm.get("model", ""))
                for sm in sem_models if sm.get("model")
            ],
            "required_cols_any": [],
        },
        "models": mdl_models,
        "workflows": [],  # MetricFlow has no workflow concept
        "metric_definitions": metric_defs,
        "_warnings": warnings,
        "_skipped": skipped,
    }
    return pack


# ── End-to-end install ──────────────────────────────────────────────────────

def install_metricflow(project_slug: str, path: str,
                       owner_user_id: Optional[int] = None) -> dict:
    """End-to-end: load + translate + install via existing install_mdl().

    Process:
      1. Walk path (file or dir) → load MetricFlow YAML
      2. Translate → MDL_PACK dict
      3. Register pack at runtime in `dash.workflows.verticals._ALL_MDL_PACKS`
         (under name 'metricflow_imported')
      4. Call install_mdl(project_slug, 'metricflow_imported', owner_user_id)

    Returns:
        {ok, models_imported, metrics_imported, skipped, warnings, install_result}
    """
    if not project_slug:
        return {"ok": False, "models_imported": 0, "metrics_imported": 0,
                "skipped": [], "warnings": ["project_slug required"]}
    if not os.path.exists(path):
        return {"ok": False, "models_imported": 0, "metrics_imported": 0,
                "skipped": [], "warnings": [f"path not found: {path}"]}

    mf_data = load_metricflow_dir(path)
    pack = metricflow_to_mdl(mf_data)

    warnings = list(pack.get("_warnings") or [])
    skipped = list(pack.get("_skipped") or [])

    # Register pack at runtime so install_mdl() can find it.
    # _ALL_MDL_PACKS is the registry consulted by install_mdl resolver.
    try:
        from dash.workflows import verticals as _verticals
        # crm_calls / pharmacy_retail register at module import. install_mdl
        # iterates known modules; we need a runtime hook. Easiest: register a
        # synthetic module-like attr on the verticals package.
        if not hasattr(_verticals, "_metricflow_packs"):
            _verticals._metricflow_packs = {}
        _verticals._metricflow_packs[pack["name"]] = pack
    except Exception as e:
        return {"ok": False, "models_imported": 0, "metrics_imported": 0,
                "skipped": skipped,
                "warnings": warnings + [f"register pack failed: {e}"]}

    # Call install_mdl. It searches crm_calls/pharmacy_retail modules first;
    # if not found there, look up our runtime registry.
    install_result: dict
    try:
        from dash.workflows.verticals import install_mdl
        install_result = install_mdl(project_slug, pack["name"], owner_user_id)
    except Exception as e:
        install_result = {"ok": False, "error": f"install_mdl failed: {e}"}

    # install_mdl as-shipped only iterates crm_calls/pharmacy_retail. If it
    # returned "pack not found", attempt direct install via lower-level path.
    if (not install_result.get("ok")
            and "not found" in str(install_result.get("error", "")).lower()):
        install_result = _direct_install(project_slug, pack, owner_user_id)

    models_imported = install_result.get("models_installed", 0)
    metrics_imported = len(pack.get("metric_definitions") or [])

    return {
        "ok": bool(install_result.get("ok")),
        "models_imported": int(models_imported),
        "metrics_imported": int(metrics_imported),
        "skipped": skipped + [
            {"name": s, "reason": "install_mdl skipped"}
            for s in (install_result.get("skipped") or [])
        ],
        "warnings": warnings,
        "install_result": install_result,
        "pack": pack,
    }


def _direct_install(project_slug: str, pack: dict,
                    owner_user_id: Optional[int]) -> dict:
    """Fallback installer: temporarily monkey-patch the module-scan tuple in
    `dash.workflows.verticals.install_mdl` so it sees our synthetic pack.

    install_mdl's scan loop is `for mod in (crm_calls, pharmacy_retail):` —
    we inject a synthetic ModuleType holding MDL_PACK=pack and re-patch the
    function's __globals__ for the call duration.
    """
    try:
        import types
        from dash.workflows import verticals as _v

        synth = types.ModuleType("_metricflow_synth")
        synth.MDL_PACK = pack

        # Save originals
        orig_crm = _v.crm_calls
        orig_pharm = _v.pharmacy_retail
        try:
            # Replace module refs with synth so install_mdl's scan finds pack
            _v.crm_calls = synth
            # keep pharmacy_retail intact so its MDL_PACK lookup still misses cleanly
            return _v.install_mdl(project_slug, pack["name"], owner_user_id)
        finally:
            _v.crm_calls = orig_crm
            _v.pharmacy_retail = orig_pharm
    except Exception as e:
        return {"ok": False, "error": f"direct install failed: {e}"}
