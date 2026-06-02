"""Vertical workflow packs — schema-aware auto-install of pre-built workflow
templates.

Each pack is a Python dict (kept as Python not YAML to avoid an extra dep).
A pack declares:
  - `detect`: schema signals that say "this project looks like THIS vertical"
    (required tables + columns w/ alias lists, scored 0.0–1.0)
  - `workflows`: list of workflow templates w/ alias-bindable placeholders

The resolver scores all packs against a project's information_schema, picks
the highest-scoring pack (≥0.6), and installs each workflow whose required
column placeholders can be alias-resolved against the real schema.

Public API:
  list_packs()            → list[dict]   all available packs
  detect(project_slug)    → list[dict]   ranked matches w/ score
  install(slug, pack)     → dict         install result + per-workflow status
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── Pack registry ────────────────────────────────────────────────────────────
# Import each pack module, gather PACK dict. Add new packs by creating
# dash/workflows/verticals/<name>.py with a top-level PACK = {...} dict.
from . import crm_calls  # noqa: E402
from . import pharmacy_retail  # noqa: E402
from . import finance_fpa  # noqa: E402
from . import retail_ops  # noqa: E402
from . import hr_workforce  # noqa: E402

_ALL_PACKS = [
    crm_calls.PACK,
    pharmacy_retail.PACK,
    finance_fpa.PACK,
    retail_ops.PACK,
    hr_workforce.PACK,
]


def _derive_manifest(module) -> dict:
    """Build a registry MANIFEST for a vertical-pack module.

    Prefers an explicit `MANIFEST` dict on the module; otherwise infers from
    the module's PACK / MDL_PACK + docstring + filename. Used by `app/packs_api.py`
    `/sync` to upsert into `dash.dash_packs`.
    """
    explicit = getattr(module, "MANIFEST", None)
    if isinstance(explicit, dict) and explicit.get("name"):
        return dict(explicit)

    pack = getattr(module, "PACK", None) or {}
    mdl = getattr(module, "MDL_PACK", None) or {}
    base = pack or mdl or {}

    fname = getattr(module, "__name__", "").rsplit(".", 1)[-1] or "unknown"
    name = base.get("name") or fname
    description = base.get("description") or (
        (module.__doc__ or "").strip().split("\n", 1)[0]
        if getattr(module, "__doc__", None) else ""
    )

    skills: list[str] = []
    wf_names = [w.get("name") for w in (base.get("workflows") or []) if w.get("name")]
    if wf_names:
        skills = wf_names[:20]

    return {
        "name": name,
        "version": str(base.get("version") or "1.0.0"),
        "author": base.get("author") or "internal",
        "description": description or f"Vertical pack: {fname}",
        "vertical": base.get("vertical") or fname,
        "skills": skills,
        "golden_qa": base.get("golden_qa") or [],
        "mdl_fragments": base.get("mdl_fragments") or (
            [m.get("name") for m in (mdl.get("models") or []) if m.get("name")]
            if mdl else []
        ),
        "workflow_count": len(base.get("workflows") or []),
        "model_count": len(mdl.get("models") or []) if mdl else 0,
        "format": "mdl" if mdl else "legacy",
    }


def iter_pack_modules():
    """Yield (module, manifest, source_path) for every registered vertical pack.

    Consumed by `app/packs_api.py /sync` to populate `dash.dash_packs`.
    """
    import os as _os
    for mod in (crm_calls, pharmacy_retail, finance_fpa, retail_ops, hr_workforce):
        manifest = _derive_manifest(mod)
        source_path = getattr(mod, "__file__", None) or ""
        try:
            source_path = _os.path.abspath(source_path) if source_path else ""
        except Exception:
            pass
        yield mod, manifest, source_path

# MDL-format packs (Phase 3). Listed separately because they install via
# install_mdl() not install(). list_packs() includes both legacy + mdl rows.
_ALL_MDL_PACKS = []
for _m in (crm_calls, pharmacy_retail, finance_fpa, retail_ops, hr_workforce):
    _mp = getattr(_m, "MDL_PACK", None)
    if _mp:
        _ALL_MDL_PACKS.append(_mp)


def list_packs() -> list[dict]:
    """Return shallow copy of all registered packs (legacy + MDL)."""
    out = []
    for p in _ALL_PACKS:
        out.append({
            "name": p["name"],
            "vertical": p["vertical"],
            "description": p.get("description", ""),
            "workflow_count": len(p.get("workflows") or []),
            "detect_tables": p.get("detect", {}).get("required_tables_any") or [],
            "detect_cols": p.get("detect", {}).get("required_cols_any") or [],
            "format": "legacy",
        })
    for p in _ALL_MDL_PACKS:
        out.append({
            "name": p["name"],
            "vertical": p["vertical"],
            "description": p.get("description", ""),
            "workflow_count": len(p.get("workflows") or []),
            "model_count": len(p.get("models") or []),
            "detect_tables": p.get("detect", {}).get("required_tables_any") or [],
            "detect_cols": p.get("detect", {}).get("required_cols_any") or [],
            "format": "mdl",
        })
    return out


def _engine():
    from db import get_sql_engine
    return get_sql_engine()


def _write_engine():
    """Write-capable engine for platform-metadata writes (public schema).
    See CLAUDE.md gotcha: get_sql_engine is read-only enforced."""
    from db.session import get_write_engine
    return get_write_engine()


def _read_schema(project_slug: str) -> dict:
    """Return {tables: {tname: {cols: [colnames]}}} for project schema."""
    eng = _engine()
    out: dict[str, dict] = {"tables": {}}
    try:
        with eng.connect() as cn:
            rows = cn.execute(text(
                "SELECT table_name, column_name "
                "  FROM information_schema.columns "
                " WHERE table_schema=:s "
                " ORDER BY table_name, ordinal_position"
            ), {"s": project_slug}).fetchall()
        for tname, cname in rows:
            out["tables"].setdefault(tname.lower(), {"cols": []})["cols"].append(cname.lower())
    except Exception as e:
        logger.debug("_read_schema(%s) failed: %s", project_slug, e)
    return out


def _alias_match(needle: str, haystack: list[str]) -> Optional[str]:
    """Case-insensitive substring/exact/snake-case match. Returns matching
    name from haystack or None."""
    needle_lc = needle.lower()
    needle_clean = re.sub(r"[^a-z0-9]", "", needle_lc)
    for h in haystack:
        h_lc = h.lower()
        if needle_lc == h_lc:
            return h
        if needle_lc in h_lc or h_lc in needle_lc:
            return h
        if re.sub(r"[^a-z0-9]", "", h_lc) == needle_clean:
            return h
    return None


def _score_pack(pack: dict, schema: dict) -> float:
    """0.0–1.0 confidence that project schema matches pack."""
    det = pack.get("detect") or {}
    req_tables = [t.lower() for t in (det.get("required_tables_any") or [])]
    req_cols = [c.lower() for c in (det.get("required_cols_any") or [])]
    table_names = list(schema["tables"].keys())
    all_cols = []
    for t in schema["tables"].values():
        all_cols.extend(t.get("cols") or [])
    table_hits = sum(1 for t in req_tables if _alias_match(t, table_names))
    col_hits = sum(1 for c in req_cols if _alias_match(c, all_cols))
    # Weighted: table match worth 2x column match (table presence = stronger signal)
    if not req_tables and not req_cols:
        return 0.0
    table_score = (table_hits / len(req_tables)) if req_tables else 0.5
    col_score = (col_hits / len(req_cols)) if req_cols else 0.5
    return round(0.6 * table_score + 0.4 * col_score, 3)


def detect(project_slug: str) -> list[dict]:
    """Return ranked packs w/ schema-fit scores. Includes legacy + MDL."""
    schema = _read_schema(project_slug)
    if not schema["tables"]:
        return []
    ranked = []
    for pack in _ALL_PACKS:
        score = _score_pack(pack, schema)
        ranked.append({
            "name": pack["name"],
            "vertical": pack["vertical"],
            "description": pack.get("description", ""),
            "score": score,
            "workflow_count": len(pack.get("workflows") or []),
            "format": "legacy",
        })
    for pack in _ALL_MDL_PACKS:
        score = _score_pack(pack, schema)
        ranked.append({
            "name": pack["name"],
            "vertical": pack["vertical"],
            "description": pack.get("description", ""),
            "score": score,
            "workflow_count": len(pack.get("workflows") or []),
            "model_count": len(pack.get("models") or []),
            "format": "mdl",
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def _resolve_table(table_aliases: list[str], schema: dict) -> Optional[str]:
    """Pick the best-matching real table for a list of aliases."""
    for alias in table_aliases:
        match = _alias_match(alias, list(schema["tables"].keys()))
        if match:
            return match
    return None


def _resolve_col(col_aliases: list[str], table_cols: list[str]) -> Optional[str]:
    for alias in col_aliases:
        match = _alias_match(alias, table_cols)
        if match:
            return match
    return None


def _bind_workflow(wf: dict, schema: dict) -> Optional[dict]:
    """Resolve {table} + {col} placeholders. Returns
    {sql, resolved: {table:..., cols:{}}} or None on missing binding."""
    expects = wf.get("expects") or {}
    table_aliases = (expects.get("table") or {}).get("aliases") or []
    table_name = _resolve_table(table_aliases, schema)
    if not table_name:
        return None
    table_cols = schema["tables"].get(table_name, {}).get("cols") or []
    col_specs = (expects.get("cols") or {})
    resolved_cols: dict[str, str] = {}
    for placeholder, aliases in col_specs.items():
        match = _resolve_col(aliases or [], table_cols)
        if not match:
            return None
        resolved_cols[placeholder] = match
    sql_template = wf.get("template_sql") or ""
    # Replace {table} + {col_placeholder} substitutions
    sql = sql_template.replace("{table}", f"{schema.get('schema_name', '')}.{table_name}" if schema.get("schema_name") else table_name)
    for k, v in resolved_cols.items():
        sql = sql.replace("{" + k + "}", v)
    return {"sql": sql, "resolved": {"table": table_name, "cols": resolved_cols}}


def install(project_slug: str, pack_name: str, owner_user_id: Optional[int] = None) -> dict:
    """Install all workflows from `pack_name` into project. Skips workflows
    whose required columns don't resolve. Idempotent on (project_slug, name)."""
    pack = next((p for p in _ALL_PACKS if p["name"] == pack_name), None)
    if not pack:
        return {"ok": False, "error": f"pack not found: {pack_name}"}
    schema = _read_schema(project_slug)
    schema["schema_name"] = project_slug
    if not schema["tables"]:
        return {"ok": False, "error": "project schema empty"}
    installed = 0
    skipped = 0
    skipped_reasons: list[str] = []
    rows_out: list[dict] = []
    eng = _engine()
    with eng.begin() as cn:
        for wf in pack.get("workflows") or []:
            bound = _bind_workflow(wf, schema)
            if not bound:
                skipped += 1
                skipped_reasons.append(f"{wf['name']}: missing required columns")
                continue
            import json as _json
            try:
                cn.execute(text(
                    "INSERT INTO dash.dash_autonomous_workflows "
                    "  (project_slug, name, description, query_template, resolved_query, "
                    "   action, status, vertical_pack, binding_resolved, owner_user_id) "
                    "VALUES (:s, :n, :d, :q, :q, :a, 'pending', :p, "
                    "        CAST(:b AS jsonb), :o) "
                    "ON CONFLICT DO NOTHING"
                ), {
                    "s": project_slug, "n": wf["name"],
                    "d": wf.get("description", ""), "q": bound["sql"],
                    "a": wf.get("action", "post_insight"),
                    "p": pack_name,
                    "b": _json.dumps(bound["resolved"]),
                    "o": owner_user_id,
                })
                installed += 1
                rows_out.append({"name": wf["name"], "table": bound["resolved"]["table"],
                                 "cols": bound["resolved"]["cols"]})
            except Exception as e:
                skipped += 1
                skipped_reasons.append(f"{wf['name']}: insert failed ({e})")
        # Audit row
        score = _score_pack(pack, schema)
        cn.execute(text(
            "INSERT INTO dash.dash_vertical_pack_history "
            "  (project_slug, pack_name, score, workflows_installed, "
            "   workflows_skipped, installed_by) "
            "VALUES (:s, :p, :sc, :wi, :ws, :u)"
        ), {"s": project_slug, "p": pack_name, "sc": score,
            "wi": installed, "ws": skipped, "u": owner_user_id})
    return {
        "ok": True, "pack": pack_name, "score": score,
        "installed": installed, "skipped": skipped,
        "skipped_reasons": skipped_reasons[:10],
        "workflows": rows_out,
    }


def _vcol_dry_run(eng, project_slug: str, raw_table: str,
                  expression: str) -> tuple[bool, str]:
    """H11: EXPLAIN `SELECT <expression> FROM <project_slug>.<raw_table> LIMIT 0`.

    Catches typos / missing cols / type mismatches at install time so they
    don't surface as runtime SQL errors mid-chat.

    Returns (passed, error_msg). Fail-soft: on connection issue, pass
    (don't block install on infra hiccups).
    """
    if not expression or not expression.strip():
        return True, ""
    # Skip pure column-rename (no operators) — _resolve_alias already proved
    # the column exists. Only test expressions containing operators or fn calls.
    has_op = any(c in expression for c in "+-*/()=<>") or " " in expression.strip()
    if not has_op:
        return True, ""
    try:
        from sqlalchemy import text as _text
        # Quote identifiers safely
        sql = f'EXPLAIN SELECT ({expression}) FROM "{project_slug}"."{raw_table}" LIMIT 0'
        with eng.connect() as cn:
            cn.execute(_text(sql))
        return True, ""
    except Exception as e:
        msg = str(e).split("\n")[0][:200]
        return False, msg


def _resolve_alias(aliases: list[str], real_cols: set[str]) -> Optional[str]:
    """First alias that matches a real column (case-insensitive)."""
    if not aliases:
        return None
    lower_real = {c.lower(): c for c in real_cols}
    for a in aliases:
        hit = lower_real.get(a.lower())
        if hit:
            return hit
    # Token-overlap fallback
    for a in aliases:
        for col in real_cols:
            if a.lower() in col.lower():
                return col
    return None


def _resolve_table_alias(aliases: list[str], tables: dict) -> Optional[str]:
    """First raw_table_alias that matches a real table (case-insensitive,
    then prefix/contains)."""
    if not aliases or not tables:
        return None
    lower_real = {t.lower(): t for t in tables.keys()}
    for a in aliases:
        hit = lower_real.get(a.lower())
        if hit:
            return hit
    for a in aliases:
        for tname in tables.keys():
            if a.lower() in tname.lower() or tname.lower().startswith(a.lower()):
                return tname
    return None


def install_mdl(project_slug: str, pack_name: str,
                owner_user_id: Optional[int] = None) -> dict:
    """Install an MDL-format pack (Phase 3 / WrenAI-style).

    Pack must have `models` + `workflows` keys (vs legacy `expects` +
    `template_sql`). For each model:
      1. Resolve raw_table via alias match
      2. Resolve each virtual_column's raw expression via alias match
      3. INSERT one row into dash_metric_definitions per model
         w/ model_name + raw_table_ref + virtual_columns JSONB

    For each workflow: INSERT into dash_autonomous_workflows w/ the SEMANTIC
    SQL (compile happens at exec time via dash.semantic.compile_query).

    Idempotent on (project_slug, name) per existing UNIQUE behavior.
    """
    # Find pack
    pack = None
    for mod in (crm_calls, pharmacy_retail, finance_fpa, retail_ops, hr_workforce):
        for attr in ("MDL_PACK",):
            cand = getattr(mod, attr, None)
            if cand and cand.get("name") == pack_name:
                pack = cand
                break
        if pack:
            break
    if not pack:
        return {"ok": False, "error": f"MDL pack not found: {pack_name}"}

    schema = _read_schema(project_slug)
    if not schema["tables"]:
        return {"ok": False, "error": "project schema empty"}

    # --- Pre-install gates (C3, C7) ----------------------------------------
    # Cycle detect across declared models (rejects unshippable MDL early so
    # compiler's MAX_ITERS cap doesn't silently return non-fixed-point SQL).
    try:
        from dash.semantic import detect_cycles as _detect_cycles
        _pre_models = {}
        for m in pack.get("models") or []:
            _pre_models[m["name"]] = {
                "virtual_columns": m.get("virtual_columns", []),
            }
        _cycles = _detect_cycles(_pre_models)
        if _cycles:
            return {
                "ok": False,
                "error": "vcol cycle(s) in MDL pack",
                "cycles": [" -> ".join(c) for c in _cycles[:5]],
            }
    except Exception as _ce:
        logger.warning(f"cycle detect skipped for {pack_name}: {_ce}")

    # Name collision: model_name == real table in project schema. Model wins
    # in compiler, so user loses ability to query the raw table by that name.
    # Warn but don't reject (operator may want the override).
    name_collisions: list[str] = []
    for m in pack.get("models") or []:
        if m["name"].lower() in schema["tables"]:
            name_collisions.append(m["name"])
    if name_collisions:
        logger.warning(
            f"MDL install {pack_name}: model_name collides with real table(s): "
            f"{name_collisions} — model wins in compile, raw table shadowed"
        )

    # Use WRITE engine — dash_metric_definitions lives in public schema,
    # which the default sql_engine has transaction_read_only=on for.
    eng = _write_engine()
    models_installed = 0
    workflows_installed = 0
    skipped_workflows: list[str] = []
    model_summaries: list[dict] = []

    import json as _json
    with eng.begin() as cn:
        # 1. Install each model into dash_metric_definitions
        for m in pack.get("models") or []:
            raw_table = _resolve_table_alias(m.get("raw_table_aliases", []),
                                             schema["tables"])
            if not raw_table:
                skipped_workflows.append(
                    f"model '{m['name']}': no raw table matches {m.get('raw_table_aliases')}"
                )
                continue
            real_cols = set(schema["tables"][raw_table]["cols"])

            # Resolve each virtual column to its expression
            resolved_vcs: list[dict] = []
            vcol_dry_run_failures: list[str] = []
            for vc in m.get("virtual_columns", []):
                name = vc["name"]
                if "expression" in vc:
                    # Already an expression (e.g., was_successful) — keep as-is
                    # NOTE: expression may reference other virtual cols by their
                    # logical names. Compiler handles recursion via column rewrite.
                    # H11: dry-run EXPLAIN against raw table to catch bad refs.
                    ok, err = _vcol_dry_run(_engine(), project_slug,
                                             raw_table, vc["expression"])
                    if not ok:
                        vcol_dry_run_failures.append(f"{name}: {err}")
                        # Don't INSERT a broken vcol — operator must fix
                        continue
                    entry = {
                        "name": name, "expression": vc["expression"],
                        "type": vc.get("type", "string"),
                    }
                    if vc.get("bounds"):  # H12: pass bounds through
                        entry["bounds"] = vc["bounds"]
                    resolved_vcs.append(entry)
                    continue
                # Alias resolution → raw column name
                raw_col = _resolve_alias(vc.get("aliases", []), real_cols)
                if not raw_col:
                    continue  # virtual col unresolved → skip
                entry = {
                    "name": name, "expression": raw_col,
                    "type": vc.get("type", "string"),
                }
                if vc.get("bounds"):
                    entry["bounds"] = vc["bounds"]
                resolved_vcs.append(entry)
            # Surface dry-run failures into skipped_workflows so installer sees them
            for f in vcol_dry_run_failures:
                skipped_workflows.append(f"model '{m['name']}' vcol {f}")

            if not resolved_vcs:
                skipped_workflows.append(
                    f"model '{m['name']}': 0 virtual cols resolved against {raw_table}"
                )
                continue

            # Filter relationships to those whose target model exists
            rels = [r for r in m.get("relationships", []) if not r.get("optional")]

            try:
                cn.execute(text(
                    "INSERT INTO public.dash_metric_definitions "
                    "  (project_slug, name, description, kind, status, "
                    "   model_name, raw_table_ref, virtual_columns, relationships) "
                    "VALUES (:s, :n, :d, 'count', 'verified', :mn, :rt, "
                    "        CAST(:vc AS jsonb), CAST(:rel AS jsonb)) "
                    "ON CONFLICT (project_slug, name) DO UPDATE SET "
                    "  model_name = EXCLUDED.model_name, "
                    "  raw_table_ref = EXCLUDED.raw_table_ref, "
                    "  virtual_columns = EXCLUDED.virtual_columns, "
                    "  relationships = EXCLUDED.relationships, "
                    "  updated_at = now()"
                ), {
                    "s": project_slug,
                    "n": f"mdl_{m['name']}",  # avoid clash w/ user-pinned KPIs
                    "d": f"MDL model for {m['name']} (pack: {pack_name})",
                    "mn": m["name"], "rt": raw_table,
                    "vc": _json.dumps(resolved_vcs),
                    "rel": _json.dumps(rels),
                })
                models_installed += 1
                model_summaries.append({
                    "name": m["name"], "raw_table": raw_table,
                    "virtual_cols": [v["name"] for v in resolved_vcs],
                })
            except Exception as e:
                skipped_workflows.append(f"model '{m['name']}': insert failed ({e})")

        # Invalidate compiler cache so new model visible immediately (C6)
        try:
            from dash.semantic import invalidate as _mdl_invalidate
            _mdl_invalidate(project_slug)
        except Exception:
            pass

        # 2. Install workflows (SQL is semantic; compile at exec time)
        for wf in pack.get("workflows") or []:
            try:
                cn.execute(text(
                    "INSERT INTO dash.dash_autonomous_workflows "
                    "  (project_slug, name, description, query_template, "
                    "   resolved_query, action, status, vertical_pack, "
                    "   binding_resolved, owner_user_id) "
                    "VALUES (:s, :n, :d, :q, :q, :a, 'pending', :p, "
                    "        CAST(:b AS jsonb), :o) "
                    "ON CONFLICT DO NOTHING"
                ), {
                    "s": project_slug, "n": wf["name"],
                    "d": wf.get("description", ""), "q": wf["sql"],
                    "a": wf.get("action", "post_insight"),
                    "p": pack_name,
                    "b": _json.dumps({"format": "mdl", "model": wf.get("model")}),
                    "o": owner_user_id,
                })
                workflows_installed += 1
            except Exception as e:
                skipped_workflows.append(f"{wf['name']}: insert failed ({e})")

        # Audit
        cn.execute(text(
            "INSERT INTO dash.dash_vertical_pack_history "
            "  (project_slug, pack_name, score, workflows_installed, "
            "   workflows_skipped, installed_by) "
            "VALUES (:s, :p, 1.0, :wi, :ws, :u)"
        ), {"s": project_slug, "p": pack_name,
            "wi": workflows_installed, "ws": len(skipped_workflows),
            "u": owner_user_id})

    return {
        "ok": True, "pack": pack_name, "format": "mdl",
        "models_installed": models_installed,
        "workflows_installed": workflows_installed,
        "skipped": skipped_workflows[:10],
        "models": model_summaries,
        "name_collisions": name_collisions,
    }


__all__ = ["list_packs", "detect", "install", "install_mdl",
           "iter_pack_modules", "_derive_manifest"]
