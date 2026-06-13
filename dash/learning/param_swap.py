"""Mode-1.5 reasoning cache — reuse a proven query's PLAN with a swapped value.

Mode-1 (try_query_bank_serve) only serves a near-IDENTICAL question. But most
real repeat traffic is the SAME question with a different STORE: "top sellers at
Shop 10" vs "… at Shop 23". Those produce two separate learned rows and each
rebuilds the agent. This lane recognises the shape, swaps the store literal in a
proven pattern's SQL, and re-runs it live — zero LLM.

How it stays safe:
  1. Only fires when the incoming question names a KNOWN store (alias derived live
     from shop_flat: the site_code, its "Shop N" label).
  2. Finds proven candidates via the existing NN recall, then requires the two
     questions to be near-identical AFTER both store names are masked to <STORE>
     (difflib ratio >= MIN) — i.e. genuinely the same question, different store.
  3. The candidate's SQL must literally contain its own store code (so the swap
     is well-defined); we replace it with the incoming store's code and re-run
     read-only with the schema-drift guard. Result is fresh, not cached numbers.

DEFAULT OFF (QUERY_PARAM_SWAP_ENABLED). When off it can still SHADOW-log what it
WOULD have served (dash_query_bank_shadow) so you can measure value on real
traffic before flipping serve on — exactly the validation the thresholds need.
"""
from __future__ import annotations

import os
import re
import time
import logging
from difflib import SequenceMatcher

logger = logging.getLogger("dash.param_swap")

SCHEMA = "citypharma"
_FLAT = f'"{SCHEMA}"."shop_flat"'
_MIN_SHAPE = float(os.getenv("QUERY_PARAM_SWAP_MIN", "0.92"))
_ALIAS_TTL = int(os.getenv("QUERY_PARAM_SWAP_ALIAS_TTL", "300"))

# cache: {"at": epoch, "aliases": [(alias_lower, site_code), ...] sorted len desc}
_CACHE: dict = {"at": 0.0, "aliases": []}


def _enabled() -> bool:
    return os.getenv("QUERY_PARAM_SWAP_ENABLED", "0") in ("1", "true", "True", "yes")


def _conn():
    from dash.tools._direct_db import direct_connect
    c = direct_connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '10s';")
    cur.execute(f'SET search_path = "{SCHEMA}", public;')
    return c, cur


def _aliases() -> list[tuple[str, str]]:
    """[(alias_lower, site_code)] sorted longest-first. site_code itself + its
    'Shop N' label are aliases. Cached for _ALIAS_TTL."""
    now = time.time()
    if (now - _CACHE["at"]) <= _ALIAS_TTL and _CACHE["aliases"]:
        return _CACHE["aliases"]
    pairs: list[tuple[str, str]] = []
    try:
        from dash.tools.shop_labels import shop_label
        c, cur = _conn()
        try:
            cur.execute(
                f"SELECT DISTINCT site_code FROM {_FLAT} "
                f"WHERE site_code IS NOT NULL AND site_code <> ''")
            for (sc,) in cur.fetchall():
                pairs.append((str(sc).lower(), sc))
                lbl = shop_label(sc)
                if lbl and lbl.lower() != str(sc).lower():
                    pairs.append((lbl.lower(), sc))
        finally:
            c.close()
    except Exception as e:
        logger.debug("alias build failed: %s", e)
        return _CACHE["aliases"]
    # longest alias first so "shop 52" beats "shop 5"
    pairs.sort(key=lambda p: -len(p[0]))
    if pairs:
        _CACHE["aliases"] = pairs
        _CACHE["at"] = now
    return pairs


def _detect_store(question: str):
    """Return (alias_text_in_q, site_code) for the most specific store named in
    the question, or None. Word-boundary match so 'shop 5' != 'shop 52'."""
    ql = (question or "").lower()
    for alias, sc in _aliases():
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", ql):
            return alias, sc
    return None


def _mask(question: str, alias: str) -> str:
    return re.sub(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])",
                  "<store>", (question or "").lower())


def _shadow(project_slug: str, question: str, matched_id, sim, would_serve, schema_ok):
    try:
        c, cur = _conn()
        try:
            cur.execute(
                "INSERT INTO public.dash_query_bank_shadow "
                "(project_slug, question, matched_id, sim, matched_status, "
                " would_serve, schema_ok) VALUES (%s,%s,%s,%s,'proven',%s,%s)",
                (project_slug, (question or "")[:500], matched_id, sim,
                 bool(would_serve), schema_ok))
        finally:
            c.close()
    except Exception:
        pass


def try_param_swap_serve(project_slug: str, question: str) -> dict | None:
    """Mode-1.5: serve a proven pattern with the store literal swapped. None on
    miss. Honors QUERY_PARAM_SWAP_ENABLED (off → shadow-log only, returns None)."""
    q = (question or "").strip()
    if not project_slug or len(q) < 6:
        return None
    det = _detect_store(q)
    if not det:
        return None
    inc_alias, inc_site = det
    masked_inc = _mask(q, inc_alias)

    # recall topically-close proven candidates (reuse query_bank NN)
    try:
        import asyncio, concurrent.futures
        from dash.learning.query_bank import _nn
        def _run():
            return asyncio.run(_nn(project_slug, q, 5, ("proven",)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            rows = _ex.submit(_run).result(timeout=8)
    except Exception as exc:  # noqa: BLE001
        logger.debug("param_swap nn failed: %s", exc)
        return None
    if not rows:
        return None

    for r in rows:
        cand_q = r[1] or ""
        cand_sql = (r[2] or "").strip()
        sim = float(r[7]) if r[7] is not None else 0.0
        cand_det = _detect_store(cand_q)
        if not cand_det:
            continue
        cand_alias, cand_site = cand_det
        if cand_site == inc_site:
            continue  # same store → Mode-1 already handles it
        # both questions must be the SAME shape once the store is masked out
        ratio = SequenceMatcher(None, masked_inc, _mask(cand_q, cand_alias)).ratio()
        if ratio < _MIN_SHAPE:
            continue
        # the candidate SQL must contain its own store literal to swap it
        if cand_site not in cand_sql:
            continue
        new_sql = cand_sql.replace(cand_site, inc_site)
        if new_sql == cand_sql:
            continue

        from dash.learning.query_bank import _schema_ok
        schema_ok = _schema_ok(project_slug, r[5], r[6])
        if schema_ok is False:
            continue

        if not _enabled():
            # shadow-only: record that we WOULD have served, then bail
            _shadow(project_slug, q, r[0], round(ratio, 3), True, schema_ok)
            logger.info("param_swap SHADOW (disabled) slug=%s pattern=%s shape=%.3f "
                        "%s->%s", project_slug, r[0], ratio, cand_site, inc_site)
            return None

        # serve: run the swapped SQL live
        try:
            t0 = time.monotonic()
            from dash.learning import verified_reward as _vr
            run = _vr._run_rows(project_slug, new_sql, limit=20)
            if not run or run.get("value") is None:
                continue
            value = run.get("value")
            rows_out = run.get("rows") or []
            cols = run.get("columns") or []
            from dash.learning.schema_guard import sql_source_tables
            from dash.learning.cache_curator import _build_card
            card = _build_card(q, value, rows_out, cols, new_sql,
                               sql_source_tables(new_sql),
                               row_count=run.get("row_count"))
            elapsed = int((time.monotonic() - t0) * 1000)
            _shadow(project_slug, q, r[0], round(ratio, 3), True, schema_ok)
            logger.info("param_swap HIT slug=%s pattern=%s shape=%.3f %s->%s %dms",
                        project_slug, r[0], ratio, cand_site, inc_site, elapsed)
            return {
                "content": f"{card}\n[VERIFIED:{elapsed}ms · learned (adapted)]",
                "sql": new_sql, "rows": rows_out, "columns": cols, "value": value,
                "row_count": run.get("row_count") or len(rows_out),
                "elapsed_ms": elapsed, "pattern_id": int(r[0]),
                "matched_q": cand_q, "shape": round(ratio, 3),
                "swapped": {"from": cand_site, "to": inc_site},
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("param_swap exec failed: %s", exc)
            continue
    return None
