"""Drift detector — per-source schema/data drift detection.

Compares current state against drift_baseline stored on
dash_data_sources at training time. Emits dash_drift_events.

Drift types:
  schema       — column added/removed/renamed
  ndv           — dim cardinality jumped >20%
  row_count     — table row count changed >50%
  distribution  — mean/stddev shifted >2 sigma  (Phase 2 stub)
  watermark     — no new data in N days (default 7)
  pii_change   — classifier flagged new PII column

Run as cron (per-source, daily). Each event tagged
(project_slug, source_id) for tenant isolation.
"""
from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DriftEvent:
    project_slug: str
    source_id: int
    drift_type: str
    severity: str
    table_name: Optional[str] = None
    column_name: Optional[str] = None
    details: dict = field(default_factory=dict)


SEVERITY_RANK = {"low": 0, "med": 1, "high": 2, "critical": 3}


def detect_for_source(project_slug: str, source_id: int,
                       *, watermark_stale_days: int = 7,
                       ndv_threshold_pct: float = 0.20,
                       row_count_threshold_pct: float = 0.50,
                       dash_engine=None) -> list[DriftEvent]:
    """Run all drift checks for a single source. Returns events emitted."""
    events: list[DriftEvent] = []

    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()

        with eng.connect() as conn:
            # 1. Read drift_baseline + last_watermark for this source
            r = conn.execute(text(
                "SELECT drift_baseline, last_watermark, last_trained_at "
                "FROM public.dash_data_sources WHERE id = :id "
                "AND project_slug = :slug"
            ), {"id": source_id, "slug": project_slug}).fetchone()
            if not r:
                return events
            baseline = r[0] or {}
            watermark = r[1] or {}
            last_trained = r[2]

        # 2. Schema drift — compare current catalog vs baseline schema_hash
        schema_events = _check_schema_drift(project_slug, source_id, baseline)
        events.extend(schema_events)

        # 3. NDV drift — compare current dim catalog vs baseline NDV
        ndv_events = _check_ndv_drift(project_slug, source_id, baseline,
                                        threshold_pct=ndv_threshold_pct)
        events.extend(ndv_events)

        # 4. Row count drift
        rc_events = _check_row_count_drift(project_slug, source_id, baseline,
                                              threshold_pct=row_count_threshold_pct)
        events.extend(rc_events)

        # 5. Watermark stale
        wm_events = _check_watermark_stale(project_slug, source_id, watermark,
                                              stale_days=watermark_stale_days)
        events.extend(wm_events)

        # 6. PII change
        pii_events = _check_pii_drift(project_slug, source_id, baseline)
        events.extend(pii_events)

        # 7. Persist
        for ev in events:
            _persist_event(ev, dash_engine=eng)

    except Exception as e:
        logger.exception(f"detect_for_source failed for {project_slug}/{source_id}: {e}")

    return events


def _check_schema_drift(slug, source_id, baseline) -> list[DriftEvent]:
    """Compare current catalog schema_hash vs baseline."""
    events = []
    try:
        from pathlib import Path
        catalog_path = Path("knowledge") / slug / f"source_{source_id}" / "catalog.json"
        if not catalog_path.exists():
            return events
        catalog = json.loads(catalog_path.read_text())

        # Build current schema fingerprint (sorted col list per table)
        current_hash = _schema_hash(catalog)
        baseline_hash = baseline.get("schema_hash")

        if baseline_hash and current_hash != baseline_hash:
            # Detect specific changes (which cols added/removed)
            added, removed = _diff_schema(baseline.get("schema_snapshot", {}), catalog)
            for tbl, cols in added.items():
                for col in cols:
                    events.append(DriftEvent(
                        project_slug=slug, source_id=source_id,
                        drift_type="schema", severity="med",
                        table_name=tbl, column_name=col,
                        details={"action": "added", "old_hash": baseline_hash,
                                  "new_hash": current_hash},
                    ))
            for tbl, cols in removed.items():
                for col in cols:
                    events.append(DriftEvent(
                        project_slug=slug, source_id=source_id,
                        drift_type="schema", severity="critical",
                        table_name=tbl, column_name=col,
                        details={"action": "removed", "old_hash": baseline_hash,
                                  "new_hash": current_hash},
                    ))
    except Exception as e:
        logger.debug(f"schema_drift check: {e}")
    return events


def _check_ndv_drift(slug, source_id, baseline, threshold_pct) -> list[DriftEvent]:
    """Per-col NDV change vs baseline."""
    events = []
    try:
        from pathlib import Path
        dim_dir = Path("knowledge") / slug / f"source_{source_id}" / "dimensions"
        if not dim_dir.exists():
            return events
        baseline_ndvs = baseline.get("ndv_snapshot", {})
        for p in dim_dir.glob("*.json"):
            tbl = p.stem
            try:
                data = json.loads(p.read_text())
                if not isinstance(data, dict):
                    continue
                for col, freq_list in data.items():
                    current_ndv = len(freq_list) if isinstance(freq_list, list) else 0
                    baseline_ndv = (baseline_ndvs.get(tbl) or {}).get(col, 0)
                    if baseline_ndv == 0 or current_ndv == 0:
                        continue
                    pct = abs(current_ndv - baseline_ndv) / baseline_ndv
                    if pct >= threshold_pct:
                        sev = "high" if pct > 1.0 else "med"
                        events.append(DriftEvent(
                            project_slug=slug, source_id=source_id,
                            drift_type="ndv", severity=sev,
                            table_name=tbl, column_name=col,
                            details={
                                "old_ndv": baseline_ndv,
                                "new_ndv": current_ndv,
                                "pct_change": round(pct, 3),
                            },
                        ))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"ndv_drift check: {e}")
    return events


def _check_row_count_drift(slug, source_id, baseline, threshold_pct) -> list[DriftEvent]:
    """Per-table row count change."""
    events = []
    try:
        from pathlib import Path
        profile_dir = Path("knowledge") / slug / f"source_{source_id}" / "profile"
        if not profile_dir.exists():
            return events
        baseline_counts = baseline.get("row_counts", {})
        for p in profile_dir.glob("*.json"):
            tbl = p.stem
            try:
                data = json.loads(p.read_text())
                if not isinstance(data, dict):
                    continue
                # Pick first col's count as table row count proxy
                current_count = 0
                for col_data in data.values():
                    if isinstance(col_data, dict):
                        current_count = max(current_count,
                                              col_data.get("count", 0) or 0)
                baseline_count = baseline_counts.get(tbl, 0)
                if baseline_count == 0:
                    continue
                pct = abs(current_count - baseline_count) / baseline_count
                if pct >= threshold_pct:
                    sev = "high" if pct > 2.0 else "med"
                    events.append(DriftEvent(
                        project_slug=slug, source_id=source_id,
                        drift_type="row_count", severity=sev,
                        table_name=tbl,
                        details={
                            "old_count": baseline_count,
                            "new_count": current_count,
                            "pct_change": round(pct, 3),
                        },
                    ))
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"row_count_drift check: {e}")
    return events


def _check_watermark_stale(slug, source_id, watermark, stale_days) -> list[DriftEvent]:
    """Watermark column hasn't advanced in stale_days."""
    events = []
    try:
        observed_at = watermark.get("observed_at")
        if not observed_at:
            return events
        try:
            ts = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except Exception:
            return events
        age = (datetime.utcnow() - ts.replace(tzinfo=None)).days
        if age >= stale_days:
            sev = "critical" if age >= stale_days * 3 else "med"
            events.append(DriftEvent(
                project_slug=slug, source_id=source_id,
                drift_type="watermark", severity=sev,
                details={"days_stale": age, "watermark_col": watermark.get("col"),
                          "last_value": watermark.get("value")},
            ))
    except Exception as e:
        logger.debug(f"watermark check: {e}")
    return events


def _check_pii_drift(slug, source_id, baseline) -> list[DriftEvent]:
    """New columns flagged PII vs baseline pii_set."""
    events = []
    try:
        from pathlib import Path
        cls_path = Path("knowledge") / slug / f"source_{source_id}" / "column_classification.json"
        if not cls_path.exists():
            return events
        data = json.loads(cls_path.read_text())
        if not isinstance(data, dict):
            return events
        current_pii = set()
        for tbl, cols in data.items():
            if isinstance(cols, dict):
                for col, c in cols.items():
                    if isinstance(c, dict) and c.get("pii"):
                        current_pii.add(f"{tbl}.{col}")
        baseline_pii = set(baseline.get("pii_columns", []))
        new_pii = current_pii - baseline_pii
        for q in new_pii:
            if "." in q:
                tbl, col = q.split(".", 1)
            else:
                tbl, col = "?", q
            events.append(DriftEvent(
                project_slug=slug, source_id=source_id,
                drift_type="pii_change", severity="high",
                table_name=tbl, column_name=col,
                details={"action": "newly_flagged_pii"},
            ))
    except Exception as e:
        logger.debug(f"pii_drift check: {e}")
    return events


def _schema_hash(catalog: dict) -> str:
    """Deterministic hash of (table, sorted_columns) tuples."""
    cols_by_table = catalog.get("columns", {})
    parts = []
    for tbl in sorted(cols_by_table.keys() if isinstance(cols_by_table, dict) else []):
        cols = cols_by_table[tbl]
        names = []
        if isinstance(cols, list):
            for c in cols:
                if isinstance(c, str):
                    names.append(c)
                elif isinstance(c, dict):
                    n = c.get("name") or c.get("column_name") or ""
                    if n:
                        names.append(n)
        parts.append(f"{tbl}:{','.join(sorted(names))}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _diff_schema(old_schema: dict, new_catalog: dict) -> tuple[dict, dict]:
    """Compare old (snapshot from baseline) vs new (catalog.json).
    Returns (added_by_table, removed_by_table)."""
    added: dict = {}
    removed: dict = {}
    new_cols = new_catalog.get("columns", {})
    if not isinstance(new_cols, dict):
        return added, removed

    for tbl, new_list in new_cols.items():
        new_names = set()
        if isinstance(new_list, list):
            for c in new_list:
                n = c if isinstance(c, str) else (c.get("name") or "" if isinstance(c, dict) else "")
                if n:
                    new_names.add(n)
        old_list = old_schema.get(tbl, [])
        old_names = set(old_list) if isinstance(old_list, list) else set()
        if new_names - old_names:
            added.setdefault(tbl, []).extend(sorted(new_names - old_names))
        if old_names - new_names:
            removed.setdefault(tbl, []).extend(sorted(old_names - new_names))
    return added, removed


def _persist_event(ev: DriftEvent, dash_engine=None) -> None:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_drift_events "
                "(project_slug, source_id, drift_type, severity, "
                " table_name, column_name, details, status) "
                "VALUES (:slug, :sid, :type, :sev, :tbl, :col, :d, 'open')"
            ), {
                "slug": ev.project_slug, "sid": ev.source_id,
                "type": ev.drift_type, "sev": ev.severity,
                "tbl": ev.table_name, "col": ev.column_name,
                "d": json.dumps(ev.details),
            })
            conn.commit()
    except Exception as e:
        logger.warning(f"persist drift event failed: {e}")

    # AutoSim drift hook removed — dash/autosim/ deleted.


def list_recent(project_slug: str, *, status: Optional[str] = None,
                 limit: int = 50, dash_engine=None) -> list[dict]:
    """Return recent drift events for a project."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        params = {"slug": project_slug, "n": limit}
        sql = ("SELECT id, source_id, drift_type, severity, table_name, "
                "column_name, details, status, detected_at "
                "FROM public.dash_drift_events "
                "WHERE project_slug = :slug ")
        if status:
            sql += "AND status = :status "
            params["status"] = status
        sql += "ORDER BY detected_at DESC LIMIT :n"
        with eng.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [{
            "id": r[0], "source_id": r[1],
            "drift_type": r[2], "severity": r[3],
            "table_name": r[4], "column_name": r[5],
            "details": r[6] or {}, "status": r[7],
            "ts": r[8].isoformat() if r[8] else None,
        } for r in rows]
    except Exception as e:
        logger.warning(f"list_recent drift: {e}")
        return []


def acknowledge(event_id: int, user_id: int,
                  dash_engine=None) -> bool:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_drift_events SET "
                " status = 'acknowledged', acknowledged_at = NOW(), "
                " acknowledged_by = :uid WHERE id = :id"
            ), {"uid": user_id, "id": event_id})
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"acknowledge: {e}")
        return False


def list_open_count(project_slug: str, dash_engine=None) -> int:
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            r = conn.execute(text(
                "SELECT COUNT(*) FROM public.dash_drift_events "
                "WHERE project_slug = :slug AND status = 'open'"
            ), {"slug": project_slug}).fetchone()
        return int(r[0] or 0) if r else 0
    except Exception:
        return 0


def list_all_open(*, limit: int = 100, dash_engine=None) -> list[dict]:
    """Super-admin cross-project view."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = dash_engine or get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, project_slug, source_id, drift_type, severity, "
                " table_name, column_name, details, status, detected_at "
                "FROM public.dash_drift_events "
                "WHERE status = 'open' "
                "ORDER BY "
                "  CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "                 WHEN 'med' THEN 2 ELSE 3 END, "
                "  detected_at DESC LIMIT :n"
            ), {"n": limit}).fetchall()
        return [{
            "id": r[0], "project_slug": r[1], "source_id": r[2],
            "drift_type": r[3], "severity": r[4],
            "table_name": r[5], "column_name": r[6],
            "details": r[7] or {}, "status": r[8],
            "ts": r[9].isoformat() if r[9] else None,
        } for r in rows]
    except Exception as e:
        logger.warning(f"list_all_open drift: {e}")
        return []
