"""Golden SQL promotion — append verified Q→SQL pairs to training corpus.

Pattern matches `try_metric_shortcut` (verified_reward.py): reads
KNOWLEDGE_DIR/{slug}/training/*.json, expects [{question, sql}] list shape.

Underscore-prefix filename (`_golden.json`) sorts first alphabetically →
loaded ahead of auto-generated *_qa.json files.

Fail-soft: any error returns {"ok": False, "error": ...}, never raises.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from dash.paths import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

GOLDEN_FILE = "_golden.json"
MAX_ENTRIES = 500  # cap per project to prevent runaway disk usage


def _path(slug: str) -> Path:
    d = KNOWLEDGE_DIR / slug / "training"
    d.mkdir(parents=True, exist_ok=True)
    return d / GOLDEN_FILE


def _load(slug: str) -> list[dict]:
    p = _path(slug)
    if not p.exists():
        return []
    try:
        with open(p) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"golden load failed for {slug}: {e}")
        return []


def _save(slug: str, entries: list[dict]) -> None:
    p = _path(slug)
    # cap + dedup-by-sql (keep newest)
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in reversed(entries):
        h = hashlib.sha256((e.get("sql") or "").strip().encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        deduped.append(e)
    deduped.reverse()
    deduped = deduped[-MAX_ENTRIES:]
    with open(p, "w") as f:
        json.dump(deduped, f, indent=2)


def promote(
    slug: str,
    *,
    question: str,
    sql: str,
    source: str = "user_thumb",
    promoted_by: str | None = None,
    expected_rowcount: int | None = None,
    expected_value: str | None = None,
) -> dict:
    """Promote a Q→SQL pair to the golden corpus.

    On next chat, `try_metric_shortcut` matches against this entry first
    (rare-term lexical overlap ≥3 terms) → runs SQL deterministically,
    zero LLM tokens, ~7ms.

    Idempotent: re-promoting same SQL replaces prior entry (dedup by sha256
    of SQL text).
    """
    question = (question or "").strip()
    sql = (sql or "").strip().rstrip(";")
    if not question or not sql:
        return {"ok": False, "error": "question and sql required"}

    # Quick safety: read-only check
    head = sql.upper().lstrip()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return {"ok": False, "error": "only SELECT/WITH allowed"}

    entries = _load(slug)
    entry = {
        "question": question,
        "sql": sql,
        "source": source,
        "promoted_by": promoted_by or "anonymous",
        "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if expected_rowcount is not None:
        entry["expected_rowcount"] = int(expected_rowcount)
    if expected_value is not None:
        entry["expected_value"] = str(expected_value)[:200]

    entries.append(entry)
    _save(slug, entries)
    return {"ok": True, "total_goldens": len(_load(slug)), "promoted": entry}


def demote(slug: str, sql: str) -> dict:
    """Remove a golden by SQL match (sha256). Returns count removed."""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return {"ok": False, "error": "sql required"}
    target_hash = hashlib.sha256(sql.encode()).hexdigest()
    entries = _load(slug)
    before = len(entries)
    kept = [
        e for e in entries
        if hashlib.sha256((e.get("sql") or "").strip().encode()).hexdigest() != target_hash
    ]
    _save(slug, kept)
    return {"ok": True, "removed": before - len(kept), "remaining": len(kept)}


def list_goldens(slug: str) -> list[dict]:
    """Return all golden entries for a project (newest first)."""
    entries = _load(slug)
    return list(reversed(entries))
