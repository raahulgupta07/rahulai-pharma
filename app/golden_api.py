"""
Golden Q&A admin API — view/add/edit/delete golden pairs + drift status + on-demand eval run.

Storage: file-based JSON at `KNOWLEDGE_DIR/{slug}/training/_golden.json` (see dash.learning.golden).
Schema per entry (from golden.promote()):
    question, sql, source, promoted_by, promoted_at, [expected_rowcount], [expected_value]

This admin UI allows broader fields too (expected_answer, tags, notes) — stored alongside,
preserved across reads/writes. We do NOT enforce SQL-only here (golden.py does that for promote).

Lock strategy: POSIX fcntl.flock (LOCK_EX) wraps every read-modify-write. Fail-soft.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/golden", tags=["golden"])

GOLDEN_FILE = "_golden.json"


def _qhash(text: str | None) -> str:
    """Stable short key for a question — lets the UI match drift regressions to
    list rows WITHOUT exposing the question text."""
    return hashlib.sha1((text or "")[:200].encode("utf-8")).hexdigest()[:12]


def _golden_path(slug: str) -> Path:
    """Return path to _golden.json; raises 503 if KNOWLEDGE_DIR unreachable."""
    try:
        from dash.paths import KNOWLEDGE_DIR
    except Exception as e:
        raise HTTPException(503, f"golden storage unavailable: {e}")
    d = KNOWLEDGE_DIR / slug / "training"
    d.mkdir(parents=True, exist_ok=True)
    return d / GOLDEN_FILE


def _load_locked(slug: str) -> tuple[Path, list[dict]]:
    """Open + flock + load. Returns (path, entries). Caller must release lock via _save_locked."""
    p = _golden_path(slug)
    if not p.exists():
        return p, []
    try:
        with open(p, "r") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            except Exception:
                pass
            data = json.load(f)
        return p, data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        logger.warning(f"golden file corrupt for {slug}: {e}")
        return p, []
    except Exception as e:
        logger.warning(f"golden load failed for {slug}: {e}")
        return p, []


def _save_atomic(path: Path, entries: list[dict]) -> None:
    """Write under exclusive lock + atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            except Exception:
                pass
            json.dump(entries, f, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.exception(f"golden save failed for {path}: {e}")
        raise HTTPException(500, f"golden save failed: {e}")


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------- endpoints ----------


@router.get("")
def list_golden(project_slug: str = Query(..., min_length=1)) -> dict[str, Any]:
    """List all golden Q&A pairs for a project (newest first by index)."""
    try:
        _, entries = _load_locked(project_slug)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"list_golden failed: {e}")
        raise HTTPException(503, f"golden corpus unavailable: {e}")

    # Privacy: the corpus list shows keyword chips only, never the raw question or
    # answer. Admin reveals a single row (audited) via /{id}/reveal to edit it.
    from dash.privacy import redact as _r, keywords as _kw
    indexed = []
    for i, e in enumerate(entries):
        row = {**e, "id": i,
               "question": _r(e.get("question")),
               "keywords": _kw(e.get("question")),
               "expected_answer": _r(e.get("expected_answer")),
               "qhash": _qhash(e.get("question"))}
        indexed.append(row)
    return {"project_slug": project_slug, "count": len(indexed), "entries": list(reversed(indexed))}


@router.get("/{entry_id}/reveal")
def reveal_golden(entry_id: int, request: Request,
                  project_slug: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Deliberate, AUDITED reveal of ONE golden entry's raw text so an admin can
    edit it. The only path that returns question/expected_answer in cleartext.
    Logged to dash_audit_log."""
    _, entries = _load_locked(project_slug)
    if entry_id < 0 or entry_id >= len(entries):
        raise HTTPException(404, f"entry {entry_id} not found (have {len(entries)})")
    e = entries[entry_id]
    try:
        from app.auth import log_action
        user = getattr(getattr(request, "state", None), "user", None)
        log_action(user, "golden.reveal", "golden", f"{project_slug}#{entry_id}",
                   "revealed golden entry for editing")
    except Exception:
        pass
    return {"id": entry_id, "question": e.get("question"),
            "expected_answer": e.get("expected_answer"), "sql": e.get("sql"),
            "tags": e.get("tags") or [], "notes": e.get("notes")}


@router.post("")
def add_golden(body: dict[str, Any]) -> dict[str, Any]:
    """Append a golden entry. Body: {project_slug, question, expected_answer, sql?, tags?, notes?}."""
    slug = (body.get("project_slug") or "").strip()
    question = (body.get("question") or "").strip()
    expected = (body.get("expected_answer") or "").strip()
    sql = (body.get("sql") or "").strip().rstrip(";")
    tags = body.get("tags") or []
    notes = (body.get("notes") or "").strip()

    if not slug or not question:
        raise HTTPException(400, "project_slug and question required")
    if not expected and not sql:
        raise HTTPException(400, "expected_answer or sql required")

    path, entries = _load_locked(slug)
    entry: dict[str, Any] = {
        "question": question,
        "expected_answer": expected,
        "sql": sql,
        "tags": tags if isinstance(tags, list) else [],
        "notes": notes,
        "source": body.get("source") or "admin_ui",
        "promoted_by": body.get("promoted_by") or "admin",
        "promoted_at": _ts(),
    }
    if "expected_rowcount" in body:
        try:
            entry["expected_rowcount"] = int(body["expected_rowcount"])
        except Exception:
            pass
    if "expected_value" in body and body["expected_value"] is not None:
        entry["expected_value"] = str(body["expected_value"])[:200]

    entries.append(entry)
    _save_atomic(path, entries)
    return {"ok": True, "id": len(entries) - 1, "total": len(entries), "entry": entry}


@router.put("/{entry_id}")
def update_golden(entry_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """Update entry at index. Body: {project_slug, ...fields to overwrite}."""
    slug = (body.get("project_slug") or "").strip()
    if not slug:
        raise HTTPException(400, "project_slug required")

    path, entries = _load_locked(slug)
    if entry_id < 0 or entry_id >= len(entries):
        raise HTTPException(404, f"entry {entry_id} not found (have {len(entries)})")

    current = dict(entries[entry_id])
    for key in ("question", "expected_answer", "sql", "tags", "notes",
                "expected_rowcount", "expected_value"):
        if key in body:
            v = body[key]
            if key == "sql" and isinstance(v, str):
                v = v.strip().rstrip(";")
            elif key in ("question", "expected_answer", "notes") and isinstance(v, str):
                v = v.strip()
            elif key == "expected_rowcount":
                try:
                    v = int(v)
                except Exception:
                    continue
            current[key] = v
    current["updated_at"] = _ts()
    entries[entry_id] = current
    _save_atomic(path, entries)
    return {"ok": True, "id": entry_id, "entry": current}


@router.delete("/{entry_id}")
def delete_golden(entry_id: int, project_slug: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Remove entry at index."""
    path, entries = _load_locked(project_slug)
    if entry_id < 0 or entry_id >= len(entries):
        raise HTTPException(404, f"entry {entry_id} not found (have {len(entries)})")
    removed = entries.pop(entry_id)
    _save_atomic(path, entries)
    return {"ok": True, "removed": removed, "remaining": len(entries)}


@router.get("/drift")
def drift_status(project_slug: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Last drift-check result for this project (dry-run rescore, no demotions).

    Returns: { last_run_at, checked, drifted_count, pass_rate, regressions: [...] }
    """
    try:
        from dash.cron.golden_drift import check_project
    except Exception as e:
        raise HTTPException(503, f"drift module unavailable: {e}")

    try:
        res = check_project(project_slug, dry_run=True)
    except Exception as e:
        logger.exception(f"drift_status failed for {project_slug}: {e}")
        raise HTTPException(503, f"drift check failed: {e}")

    checked = res.get("checked", 0)
    drifted = res.get("drifted", []) or []
    pass_count = max(0, checked - len(drifted))
    pass_rate = (pass_count / checked) if checked > 0 else 0.0
    from dash.privacy import redact as _r, keywords as _kw
    regressions = [
        {
            # privacy: match to list rows by qhash, show keyword chips, not text
            "question": _r((d.get("entry") or {}).get("question", "")),
            "keywords": _kw((d.get("entry") or {}).get("question", "")),
            "qhash": _qhash((d.get("entry") or {}).get("question", "")),
            "reason": d.get("reason", ""),
        }
        for d in drifted
    ][:50]

    return {
        "project_slug": project_slug,
        "last_run_at": _ts(),
        "checked": checked,
        "passed": pass_count,
        "drifted_count": len(drifted),
        "pass_rate": round(pass_rate, 4),
        "regressions": regressions,
    }


@router.post("/run")
def run_eval_now(project_slug: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Trigger a drift check immediately (dry-run, no demote). Synchronous; small corpora finish fast."""
    try:
        from dash.cron.golden_drift import check_project
    except Exception as e:
        raise HTTPException(503, f"drift module unavailable: {e}")

    t0 = time.time()
    try:
        res = check_project(project_slug, dry_run=True)
    except Exception as e:
        logger.exception(f"run_eval_now failed for {project_slug}: {e}")
        raise HTTPException(500, f"eval failed: {e}")

    checked = res.get("checked", 0)
    drifted = res.get("drifted", []) or []
    pass_count = max(0, checked - len(drifted))
    return {
        "ok": True,
        "project_slug": project_slug,
        "checked": checked,
        "passed": pass_count,
        "drifted_count": len(drifted),
        "pass_rate": round((pass_count / checked) if checked > 0 else 0.0, 4),
        "elapsed_s": round(time.time() - t0, 2),
        "regressions": [
            {
                "question": (d.get("entry") or {}).get("question", "")[:200],
                "reason": d.get("reason", ""),
            }
            for d in drifted
        ][:50],
    }
