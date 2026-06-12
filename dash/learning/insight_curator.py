"""Insight compilation — distil durable observations from query history + live data.

Karpathy "second brain" applied to FINDINGS, not just query paths: instead of
waiting to be asked, the system periodically reads the data + what staff actually
ask and writes short, durable INSIGHT notes ("N products are stocked nowhere",
"category X is concentrated in 3 outlets", "staff most often ask about Y"). Those
notes are injected into chat context (via app.brain.get_brain_context) so the
agent can volunteer them.

TRUST: every distilled insight lands as `status='pending'` in dash_company_brain
(Intern Rule) — it does NOT reach chat until an admin approves it. The supporting
numbers are kept in dash_insights for review.

PURE SQL — no LLM, no ML. Read-only heuristics over the denormalized shop_flat +
dash_query_patterns. Own direct read/write connection to cp-db (autocommit),
same pattern as the pharma tools (sidesteps the pgbouncer read-only engine).

Also runs the FACT FRESHNESS pass (#4): brain/memory facts older than
INSIGHT_STALE_DAYS with no recent citation are flagged needs_reverify, and a
summary insight surfaces the count.
"""
from __future__ import annotations

import os
import json
import logging

log = logging.getLogger("dash.insight_curator")

_STALE_DAYS = int(os.getenv("INSIGHT_STALE_DAYS", "120"))
_MAX_INSIGHTS = int(os.getenv("INSIGHT_MAX_PER_CYCLE", "12"))


def _conn():
    import psycopg
    c = psycopg.connect(
        host=os.getenv("GRAPH_DB_HOST", "dash-db"),
        port=int(os.getenv("GRAPH_DB_PORT", "5432")),
        user=os.getenv("DB_USER", "ai"),
        dbname=os.getenv("DB_DATABASE", "ai"),
        password=os.getenv("DB_PASS", ""),
        connect_timeout=8,
        autocommit=True,
    )
    cur = c.cursor()
    cur.execute("SET statement_timeout = '30s';")
    return c, cur


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


# ── insight generators (each returns a list of {kind,title,detail,evidence}) ──

def _gen_blind_spots(cur, schema: str) -> list[dict]:
    """Catalog products stocked at NO outlet — silent gaps nobody queried."""
    out = []
    flat = f'"{schema}"."shop_flat"'
    try:
        cur.execute(
            f"SELECT COUNT(DISTINCT art_key) FROM {flat} "
            f"WHERE COALESCE(NULLIF(link_status,''),'both') = 'catalog_only'")
        n = int((cur.fetchone() or [0])[0] or 0)
        if n > 0:
            out.append({
                "kind": "blind_spot",
                "title": f"{_fmt(n)} products stocked nowhere",
                "detail": (f"{_fmt(n)} catalog products are not in stock at ANY outlet "
                           f"right now — potential lost sales or delisting candidates."),
                "evidence": {"catalog_only_articles": n},
            })
    except Exception as e:
        log.debug(f"blind_spots failed: {e}")
    return out


def _gen_concentration(cur, schema: str) -> list[dict]:
    """Categories whose stock sits in very few outlets (transfer/balance signal)."""
    out = []
    flat = f'"{schema}"."shop_flat"'
    try:
        cur.execute(
            f"""SELECT category,
                       COUNT(DISTINCT site_code) FILTER (WHERE stock_qty > 0) AS outlets,
                       COALESCE(SUM(stock_qty),0) AS total_qty
                FROM {flat}
                WHERE category IS NOT NULL AND category <> ''
                GROUP BY category
                HAVING COALESCE(SUM(stock_qty),0) > 0
                ORDER BY total_qty DESC
                LIMIT 40""")
        rows = cur.fetchall()
        # total live outlets (denominator)
        cur.execute(
            f"SELECT COUNT(DISTINCT site_code) FROM {flat} WHERE site_code <> ''")
        total_outlets = int((cur.fetchone() or [1])[0] or 1) or 1
        for cat, outlets, total_qty in rows:
            outlets = int(outlets or 0)
            # flag a high-volume category held by <= 30% of outlets
            if total_qty and outlets and outlets <= max(1, int(total_outlets * 0.30)):
                out.append({
                    "kind": "concentration",
                    "title": f"{cat} concentrated in {outlets} outlets",
                    "detail": (f"Category '{cat}' ({_fmt(total_qty)} units) is stocked at only "
                               f"{outlets} of {total_outlets} outlets — uneven coverage; "
                               f"consider rebalancing."),
                    "evidence": {"category": cat, "outlets_with_stock": outlets,
                                 "total_outlets": total_outlets, "total_qty": int(total_qty)},
                })
            if len(out) >= 3:
                break
    except Exception as e:
        log.debug(f"concentration failed: {e}")
    return out


def _gen_coverage(cur, schema: str) -> list[dict]:
    """Outlet(s) with the weakest SKU coverage — under-stocked shops."""
    out = []
    flat = f'"{schema}"."shop_flat"'
    try:
        cur.execute(
            f"""SELECT site_code, COUNT(DISTINCT art_key) FILTER (WHERE stock_qty > 0) AS skus
                FROM {flat}
                WHERE site_code <> ''
                GROUP BY site_code
                ORDER BY skus ASC
                LIMIT 3""")
        rows = cur.fetchall()
        if rows:
            try:
                from dash.tools.shop_labels import shop_label
            except Exception:
                shop_label = lambda x: x  # noqa: E731
            worst = rows[0]
            out.append({
                "kind": "coverage",
                "title": f"{shop_label(worst[0])} has the thinnest range",
                "detail": (f"{shop_label(worst[0])} carries only {_fmt(worst[1])} in-stock SKUs — "
                           f"the lowest of all outlets; check for under-ordering."),
                "evidence": {"site_code": worst[0], "skus_in_stock": int(worst[1] or 0)},
            })
    except Exception as e:
        log.debug(f"coverage failed: {e}")
    return out


def _gen_demand_themes(cur, slug: str) -> list[dict]:
    """What staff most often ask — top recurring captured questions."""
    out = []
    try:
        cur.execute(
            """SELECT question, COALESCE(uses,1) AS uses
               FROM public.dash_query_patterns
               WHERE project_slug = %s AND source = 'chat'
                 AND question IS NOT NULL AND question <> ''
               ORDER BY uses DESC, last_used DESC NULLS LAST
               LIMIT 3""",
            (slug,))
        rows = [r for r in cur.fetchall() if int(r[1] or 0) >= 3]
        for q, uses in rows:
            short = (q or "").strip()
            if len(short) > 70:
                short = short[:70] + "…"
            # title MUST be unique per insight (unique index on project_slug,name)
            out.append({
                "kind": "demand_theme",
                "title": f'Frequent ask: "{short}"',
                "detail": f'Staff have asked this {_fmt(uses)} times — a recurring need.',
                "evidence": {"question": q, "uses": int(uses or 0)},
            })
    except Exception as e:
        log.debug(f"demand_themes failed: {e}")
    return out


def _freshness_pass(cur, slug: str, dry_run: bool) -> dict:
    """#4 spaced-repetition: flag stale facts for re-verify, return a summary."""
    flagged_brain = flagged_mem = 0
    try:
        if not dry_run:
            cur.execute(
                """UPDATE public.dash_company_brain
                   SET needs_reverify = TRUE
                   WHERE COALESCE(status,'active') = 'active'
                     AND COALESCE(source,'human') <> 'insight_daemon'
                     AND (project_slug = %s OR project_slug IS NULL)
                     AND created_at < (NOW() - (%s || ' days')::interval)
                     AND COALESCE(last_cited_at, created_at) < (NOW() - (%s || ' days')::interval)
                     AND needs_reverify = FALSE""",
                (slug, _STALE_DAYS, _STALE_DAYS))
            flagged_brain = cur.rowcount or 0
        # count stale memories (review-only; archiving stays with daily_decay_job)
        cur.execute(
            """SELECT COUNT(*) FROM public.dash_memories
               WHERE project_slug = %s AND COALESCE(archived,FALSE) = FALSE
                 AND created_at < (NOW() - (%s || ' days')::interval)
                 AND COALESCE(last_cited_at, created_at) < (NOW() - (%s || ' days')::interval)""",
            (slug, _STALE_DAYS, _STALE_DAYS))
        flagged_mem = int((cur.fetchone() or [0])[0] or 0)
    except Exception as e:
        log.debug(f"freshness_pass failed: {e}")
    return {"brain_flagged": flagged_brain, "memories_stale": flagged_mem}


def run_insight_curator(slug: str, dry_run: bool = False) -> dict:
    """Distil insights for one project. Writes pending brain rows unless dry_run.

    Returns {"insights": [...], "written": N, "freshness": {...}, "dry_run": bool}.
    """
    if not slug:
        return {"insights": [], "written": 0, "error": "no slug"}
    schema = slug  # single-tenant: schema name == slug
    insights: list[dict] = []
    fresh = {}
    try:
        c, cur = _conn()
        try:
            insights += _gen_blind_spots(cur, schema)
            insights += _gen_concentration(cur, schema)
            insights += _gen_coverage(cur, schema)
            insights += _gen_demand_themes(cur, slug)

            # freshness pass (#4)
            fresh = _freshness_pass(cur, slug, dry_run)
            if fresh.get("brain_flagged") or fresh.get("memories_stale"):
                total = int(fresh.get("brain_flagged", 0)) + int(fresh.get("memories_stale", 0))
                if total > 0:
                    insights.append({
                        "kind": "stale_fact",
                        "title": f"{_fmt(total)} stored facts may be stale",
                        "detail": (f"{_fmt(total)} knowledge facts have not been used in "
                                   f"{_STALE_DAYS}+ days — review them; some may be out of date."),
                        "evidence": fresh,
                    })

            # dedupe by title (unique index on project_slug, name) — first wins
            _seen = set()
            _uniq = []
            for ins in insights:
                t = ins["title"]
                if t in _seen:
                    continue
                _seen.add(t)
                _uniq.append(ins)
            insights = _uniq[:_MAX_INSIGHTS]

            written = 0
            if not dry_run and insights:
                # Clean slate of PENDING proposals from this daemon (approved
                # 'active' insights are untouched and survive), then re-propose
                # the current snapshot.
                cur.execute(
                    "DELETE FROM public.dash_company_brain "
                    "WHERE source = 'insight_daemon' AND status = 'pending' "
                    "AND (project_slug = %s OR project_slug IS NULL)",
                    (slug,))
                cur.execute(
                    "DELETE FROM public.dash_insights "
                    "WHERE project_slug = %s AND status = 'pending'",
                    (slug,))
                for ins in insights:
                    # ON CONFLICT DO NOTHING: a name already present is either an
                    # admin-APPROVED (active) insight or a rejected one — don't
                    # re-propose it. fetchone() → None on conflict, so we skip it.
                    cur.execute(
                        "INSERT INTO public.dash_company_brain "
                        "(category, name, definition, metadata, project_slug, "
                        " created_by, source, status) "
                        "VALUES ('insight', %s, %s, %s, %s, 'insight_daemon', "
                        " 'insight_daemon', 'pending') "
                        "ON CONFLICT (project_slug, name) WHERE project_slug IS NOT NULL "
                        "DO NOTHING RETURNING id",
                        (ins["title"], ins["detail"],
                         json.dumps({"kind": ins["kind"], "evidence": ins.get("evidence", {})}),
                         slug))
                    _r = cur.fetchone()
                    if not _r:
                        continue  # name already exists (approved/rejected) — skip
                    brain_id = _r[0]
                    cur.execute(
                        "INSERT INTO public.dash_insights "
                        "(project_slug, kind, title, detail, evidence, brain_id, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
                        (slug, ins["kind"], ins["title"], ins["detail"],
                         json.dumps(ins.get("evidence", {})), brain_id))
                    written += 1
            return {"insights": insights, "written": written,
                    "freshness": fresh, "dry_run": dry_run}
        finally:
            c.close()
    except Exception as e:
        log.warning(f"run_insight_curator({slug}) failed: {e}")
        return {"insights": insights, "written": 0, "error": str(e)[:300]}
