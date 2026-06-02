"""
Daily journal generator — Obsidian-style daily notes per project.

Usage:
    python -m scripts.daily_journal                          # yesterday, all projects
    python -m scripts.daily_journal --date 2026-05-25        # specific date, all projects
    python -m scripts.daily_journal --project proj_demo_x    # yesterday, one project
    python -m scripts.daily_journal --date 2026-05-25 --project proj_demo_x

Counts:
  - queries from ai.agno_sessions (chat activity for date)
  - uploads from public.dash_table_metadata + public.dash_documents (created_at::date = date)
  - KPI snapshot diffs (best-effort, tolerate missing table)
  - anomalies via rows-added > 3σ vs 30d avg per table (best-effort)

LLM: dash.settings.training_llm_call(prompt, task='extraction'). 3-bullet exec summary.
Upsert: ON CONFLICT (project_slug, journal_date) DO UPDATE.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from datetime import date as date_cls, datetime, timedelta
from typing import Any

logger = logging.getLogger("daily_journal")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _write_engine():
    try:
        from db.session import get_write_engine  # type: ignore
        return get_write_engine()
    except Exception:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()


def _read_engine():
    from db.session import get_sql_engine  # type: ignore
    return get_sql_engine()


def _llm_call(prompt: str) -> str | None:
    """Wrap LLM client w/ fallback. Returns text or None."""
    # Preferred: dash.settings.training_llm_call (per CLAUDE.md).
    try:
        from dash.settings import training_llm_call  # type: ignore
        return training_llm_call(prompt, task="extraction")
    except Exception as e:
        logger.warning(f"training_llm_call unavailable: {e}")
    # Fallback: app.llm or dash.llm.client
    for mod_path, fn_name in (
        ("app.llm", "llm_call"),
        ("dash.llm.client", "call"),
        ("dash.llm.client", "llm_call"),
    ):
        try:
            mod = __import__(mod_path, fromlist=[fn_name])
            fn = getattr(mod, fn_name, None)
            if fn:
                return fn(prompt)
        except Exception:
            continue
    logger.error("no LLM client available — summary will be empty")
    return None


def _list_projects() -> list[str]:
    """Return all active project slugs."""
    from sqlalchemy import text
    eng = _read_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT slug FROM public.dash_projects ORDER BY slug"
        )).fetchall()
    return [r[0] for r in rows]


def _count_queries(conn, slug: str, d: date_cls) -> int:
    """Count chat turns from ai.agno_sessions w/ updated_at::date = d. Best-effort."""
    from sqlalchemy import text
    try:
        row = conn.execute(text(
            "SELECT COUNT(*) FROM ai.agno_sessions "
            "WHERE team_id = 'dash' "
            "  AND (user_id = :slug OR session_id LIKE :pat) "
            "  AND updated_at::date = :d"
        ), {"slug": slug, "pat": f"%{slug}%", "d": d}).fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        logger.debug(f"count_queries fallback (no agno_sessions): {e}")
        return 0


def _count_uploads(conn, slug: str, d: date_cls) -> dict[str, int]:
    """Count tables + documents created on date."""
    from sqlalchemy import text
    out = {"tables": 0, "documents": 0}
    try:
        row = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_table_metadata "
            "WHERE project_slug = :s AND created_at::date = :d"
        ), {"s": slug, "d": d}).fetchone()
        out["tables"] = int(row[0]) if row else 0
    except Exception as e:
        logger.debug(f"count tables: {e}")
    try:
        row = conn.execute(text(
            "SELECT COUNT(*) FROM public.dash_documents "
            "WHERE project_slug = :s AND created_at::date = :d"
        ), {"s": slug, "d": d}).fetchone()
        out["documents"] = int(row[0]) if row else 0
    except Exception as e:
        logger.debug(f"count documents: {e}")
    return out


def _kpi_diff(conn, slug: str, d: date_cls) -> dict[str, Any]:
    """Diff KPI snapshots between d and d-1. Tolerates missing table."""
    from sqlalchemy import text
    try:
        rows = conn.execute(text(
            "SELECT kpi_name, value, snapshot_date FROM public.dash_kpi_snapshots "
            "WHERE project_slug = :s AND snapshot_date IN (:d, :d2) "
            "ORDER BY snapshot_date"
        ), {"s": slug, "d": d, "d2": d - timedelta(days=1)}).fetchall()
    except Exception:
        return {}  # table doesn't exist
    if not rows:
        return {}

    today: dict[str, float] = {}
    yest: dict[str, float] = {}
    for r in rows:
        try:
            val = float(r[1])
        except Exception:
            continue
        bucket = today if r[2] == d else yest
        bucket[r[0]] = val

    diffs: dict[str, Any] = {}
    for k, v in today.items():
        prev = yest.get(k)
        if prev is None:
            diffs[k] = {"current": v, "previous": None, "delta": None}
        else:
            delta = v - prev
            pct = (delta / prev * 100.0) if prev else None
            diffs[k] = {"current": v, "previous": prev, "delta": delta, "pct": pct}
    return diffs


def _detect_anomalies(conn, slug: str, d: date_cls) -> list[dict[str, Any]]:
    """For each table in project, check if rows added on d exceed 3σ vs 30d avg.

    Best-effort — requires a `created_at` column on the table. Skips on error.
    """
    from sqlalchemy import text
    anomalies: list[dict[str, Any]] = []

    # Find project schema
    try:
        row = conn.execute(text(
            "SELECT schema_name FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).fetchone()
    except Exception:
        return anomalies
    if not row or not row[0]:
        return anomalies
    schema = row[0]

    # List tables
    try:
        tbls = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :sch AND table_type = 'BASE TABLE'"
        ), {"sch": schema}).fetchall()
    except Exception:
        return anomalies

    for (tname,) in tbls:
        # Check for created_at-ish column
        try:
            ccol = conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :sch AND table_name = :t "
                "  AND column_name IN ('created_at','inserted_at','ingested_at') "
                "LIMIT 1"
            ), {"sch": schema, "t": tname}).fetchone()
        except Exception:
            continue
        if not ccol:
            continue
        col = ccol[0]

        # Daily counts last 30 days + today
        try:
            day_rows = conn.execute(text(
                f"SELECT {col}::date AS d, COUNT(*) AS n "
                f'FROM "{schema}"."{tname}" '
                f"WHERE {col} >= :start AND {col} < :end "
                f"GROUP BY 1"
            ), {"start": d - timedelta(days=30), "end": d + timedelta(days=1)}).fetchall()
        except Exception:
            continue

        if not day_rows:
            continue
        counts = {r[0]: int(r[1]) for r in day_rows}
        today_n = counts.get(d, 0)
        hist = [n for dd, n in counts.items() if dd != d]
        if len(hist) < 5 or today_n == 0:
            continue
        try:
            mean = statistics.mean(hist)
            stdev = statistics.pstdev(hist) or 0.0
        except Exception:
            continue
        if stdev <= 0:
            continue
        z = (today_n - mean) / stdev
        if abs(z) > 3:
            anomalies.append({
                "table": tname,
                "rows_added": today_n,
                "avg_30d": round(mean, 2),
                "z_score": round(z, 2),
            })
    return anomalies


def _build_summary(stats: dict[str, Any]) -> str:
    """Call LLM for 3-bullet exec summary. Returns markdown or fallback."""
    prompt = (
        "Summarize today for an analyst standup. Return exactly 3 bullets, "
        "action-oriented, terse. No preamble.\n\n"
        f"Stats:\n{json.dumps(stats, indent=2, default=str)}"
    )
    text = _llm_call(prompt)
    if text and text.strip():
        return text.strip()
    # Fallback deterministic summary
    bullets = []
    if stats.get("queries"):
        bullets.append(f"- {stats['queries']} chat queries handled today.")
    up = stats.get("uploads") or {}
    if up.get("tables") or up.get("documents"):
        bullets.append(
            f"- Ingested {up.get('tables', 0)} table(s), {up.get('documents', 0)} doc(s)."
        )
    if stats.get("anomalies"):
        bullets.append(f"- {len(stats['anomalies'])} anomaly signal(s); review tables.")
    if not bullets:
        bullets = ["- No notable activity today."]
    return "\n".join(bullets)


def generate_for_project(slug: str, d: date_cls) -> dict[str, Any]:
    """Build stats + summary, upsert into dash_journal. Returns counts."""
    from sqlalchemy import text

    read_eng = _read_engine()
    with read_eng.connect() as conn:
        queries = _count_queries(conn, slug, d)
        uploads = _count_uploads(conn, slug, d)
        kpis = _kpi_diff(conn, slug, d)
        anomalies = _detect_anomalies(conn, slug, d)

    stats = {
        "queries": queries,
        "uploads": uploads,
        "kpi_diffs": kpis,
        "anomalies": anomalies,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    summary_md = _build_summary(stats)

    # Upsert (idempotent for repeated runs of same date).
    write_eng = _write_engine()
    with write_eng.begin() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_journal (project_slug, journal_date, stats, summary_md) "
            "VALUES (:s, :d, CAST(:stats AS jsonb), :sum) "
            "ON CONFLICT (project_slug, journal_date) DO UPDATE "
            "SET stats = EXCLUDED.stats, summary_md = EXCLUDED.summary_md"
        ), {
            "s": slug,
            "d": d,
            "stats": json.dumps(stats, default=str),
            "sum": summary_md,
        })

    return {
        "project_slug": slug,
        "journal_date": str(d),
        "queries": queries,
        "uploads": uploads,
        "anomaly_count": len(anomalies),
        "summary_chars": len(summary_md or ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate daily journal entries.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--project", help="optional project slug filter")
    args = ap.parse_args()

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            logger.error("--date must be YYYY-MM-DD")
            return 2
    else:
        d = date_cls.today() - timedelta(days=1)

    if args.project:
        slugs = [args.project]
    else:
        try:
            slugs = _list_projects()
        except Exception as e:
            logger.exception(f"list_projects failed: {e}")
            return 1

    logger.info(f"generating journal for {len(slugs)} project(s) on {d}")
    ok = 0
    for slug in slugs:
        try:
            res = generate_for_project(slug, d)
            logger.info(f"  ✓ {slug}: {res}")
            ok += 1
        except Exception as e:
            logger.exception(f"  ✗ {slug}: {e}")
    logger.info(f"done — {ok}/{len(slugs)} succeeded")
    return 0 if ok == len(slugs) else 1


if __name__ == "__main__":
    sys.exit(main())
