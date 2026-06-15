"""SFTP drop-folder watcher — ingest dropped files into their tables.

Pharmacies `sftp put` a CSV/Excel into their jailed SFTPGo folder (managed in
/command-center → SFTP Access). This leader-gated daemon polls the shared
read-only `sftp_data` mount, and for each NEW/CHANGED file it:

  1. resolves the target table from the SFTP user's metadata (feeds_table),
  2. ingests it through the EXISTING /api/upload?guarded=1 pipeline (same parse,
     same empty/drift/cliff guards + pre-replace backup as S3 sync) — which also
     fires auto-train,
  3. records the result in `dash_sftp_ingest_log` (dedup + the console feed),
  4. notifies admins on failure.

Design notes:
- The app mount is READ-ONLY (the app never writes into a user's jail), so we do
  NOT move files. Instead we DEDUP on (username, rel_path, size, mtime): a file
  is ingested once; re-dropping the same name with new content (new size/mtime —
  e.g. a daily stock refresh) re-ingests under its `replace` action.
- Stability hold: a file younger than SFTP_STABLE_SECONDS is skipped this tick
  (it may still be uploading) — avoids ingesting a half-written file.
- Fail-soft everywhere: one bad file never stops the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from sqlalchemy import create_engine as _create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)

_engine = _create_engine(db_url, poolclass=NullPool)

_TICK_SECONDS = int(os.getenv("SFTP_WATCH_TICK_SECONDS", "30"))
_STABLE_SECONDS = int(os.getenv("SFTP_STABLE_SECONDS", "20"))
_DATA_DIR = os.getenv("SFTP_DATA_DIR", "/app/sftp_data")
_SLUG = os.getenv("LOCKED_PROJECT_SLUG", "citypharma")
_INTERNAL_BASE = os.getenv("AGENTOS_URL", "http://127.0.0.1:8000").rstrip("/")
_MAX_BYTES = int(os.getenv("SFTP_MAX_FILE_MB", "1024")) * 1024 * 1024
_ALLOWED_EXT = {".csv", ".xlsx", ".xls", ".parquet", ".json"}

# Retention auto-sweep: delete drops older than N hours (0 = off). SFTPGo does the
# deletion server-side (correct ownership). Keep generous so admins can review.
_RETENTION_HOURS = int(os.getenv("SFTP_RETENTION_HOURS", "0"))
_RETENTION_SWEEP_SECONDS = int(os.getenv("SFTP_RETENTION_SWEEP_SECONDS", "21600"))  # 6h
_last_sweep = 0.0


def _enabled() -> bool:
    if os.getenv("SFTP_WATCH_DISABLED") in ("1", "true", "TRUE", "yes"):
        return False
    return os.getenv("SFTP_WATCH_ENABLED", "1") in ("1", "true", "TRUE", "yes")


def _ensure_table() -> None:
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS public.dash_sftp_ingest_log ("
                " id BIGSERIAL PRIMARY KEY,"
                " username TEXT NOT NULL,"
                " rel_path TEXT NOT NULL,"
                " size BIGINT NOT NULL,"
                " mtime BIGINT NOT NULL,"
                " table_name TEXT,"
                " action TEXT,"
                " status TEXT,"             # ok | error | held | skipped
                " rows_loaded INTEGER,"
                " message TEXT,"
                " created_at TIMESTAMPTZ DEFAULT now(),"
                " UNIQUE (username, rel_path, size, mtime)"
                ")"
            ))
    except Exception:
        logger.exception("sftp_watch: ensure table failed")


def _already_done(username: str, rel: str, size: int, mtime: int) -> bool:
    try:
        with _engine.connect() as conn:
            r = conn.execute(text(
                "SELECT status FROM public.dash_sftp_ingest_log "
                "WHERE username=:u AND rel_path=:r AND size=:s AND mtime=:m LIMIT 1"
            ), {"u": username, "r": rel, "s": size, "m": mtime}).fetchone()
        # ok/held/error all count as 'seen' — don't reprocess the SAME bytes.
        # (a corrected re-drop changes size/mtime → new key → reprocessed)
        return r is not None
    except Exception:
        logger.exception("sftp_watch: dedup check failed")
        return True  # fail-safe: skip rather than risk a double ingest


def _log_result(username, rel, size, mtime, table, action, status, rows, message):
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_sftp_ingest_log "
                "(username, rel_path, size, mtime, table_name, action, status, rows_loaded, message) "
                "VALUES (:u,:r,:s,:m,:t,:a,:st,:rows,:msg) "
                "ON CONFLICT (username, rel_path, size, mtime) DO UPDATE SET "
                "status=EXCLUDED.status, rows_loaded=EXCLUDED.rows_loaded, message=EXCLUDED.message, "
                "table_name=EXCLUDED.table_name, action=EXCLUDED.action, created_at=now()"
            ), {"u": username, "r": rel, "s": size, "m": mtime, "t": table, "a": action,
                "st": status, "rows": rows, "msg": (message or "")[:2000]})
    except Exception:
        logger.exception("sftp_watch: log result failed")


def _user_table_map() -> dict[str, dict]:
    """username -> {feeds_table, ingest_action} from SFTPGo user metadata."""
    out: dict[str, dict] = {}
    try:
        from app.sftp_admin import _sftpgo, _unpack_meta
        r = _sftpgo("GET", "/api/v2/users", params={"limit": 500})
        if r.status_code == 200:
            for u in (r.json() or []):
                out[u.get("username", "")] = _unpack_meta(u.get("description", ""))
    except Exception:
        logger.exception("sftp_watch: fetch user map failed")
    return out


def _notify(title: str, message: str) -> None:
    try:
        from app.s3_sync import _notify_admins
        _notify_admins("sftp_ingest_error", title, message)
    except Exception:
        logger.warning("sftp_watch: notify failed (non-fatal)")


def _ingest_one(http, headers, username, base, fp, rel, st, meta) -> None:
    size = st.st_size
    mtime = int(st.st_mtime)
    ext = os.path.splitext(fp)[1].lower()
    table = (meta.get("feeds_table") or "").strip()
    action = (meta.get("ingest_action") or "replace").strip() or "replace"

    # ── pre-flight rejects (logged, never retried for same bytes) ──
    if ext not in _ALLOWED_EXT:
        _log_result(username, rel, size, mtime, table, action, "skipped", 0, f"unsupported type {ext}")
        return
    if size <= 0:
        _log_result(username, rel, size, mtime, table, action, "error", 0, "empty file")
        _notify(f"SFTP: empty file from {username}", f"{rel} is 0 bytes — not ingested.")
        return
    if size > _MAX_BYTES:
        _log_result(username, rel, size, mtime, table, action, "error", 0, f"over size cap ({size} bytes)")
        _notify(f"SFTP: oversized file from {username}", f"{rel} exceeds the size cap — not ingested.")
        return
    if not table:
        _log_result(username, rel, size, mtime, "", action, "error", 0, "no feeds_table mapped for this user")
        _notify(f"SFTP: no table mapped for {username}",
                f"{rel} dropped but user has no Feeds Table set in SFTP Access — not ingested.")
        return

    # ── ingest via the existing guarded upload pipeline ──
    try:
        with open(fp, "rb") as fh:
            resp = http.post(
                "/api/upload",
                headers=headers,
                params={"project": _SLUG, "table_name": table, "action": action, "guarded": "1"},
                data={"project": _SLUG, "table_name": table, "action": action, "guarded": "1"},
                files={"file": (os.path.basename(fp), fh, "application/octet-stream")},
            )
    except Exception as e:  # noqa: BLE001
        _log_result(username, rel, size, mtime, table, action, "error", 0, f"upload call failed: {e}")
        logger.exception("sftp_watch: upload call failed for %s", rel)
        return

    if resp.status_code == 409:  # a guard deliberately held the old data
        _log_result(username, rel, size, mtime, table, action, "held", 0, resp.text[:400])
        _notify(f"SFTP: ingest HELD for {username}",
                f"{rel} -> {table}: a safety guard kept the old data ({resp.text[:200]}). "
                f"Fix the file and re-drop it.")
        return
    if resp.status_code >= 400:
        _log_result(username, rel, size, mtime, table, action, "error", 0, f"HTTP {resp.status_code} {resp.text[:300]}")
        _notify(f"SFTP: ingest FAILED for {username}", f"{rel} -> {table}: HTTP {resp.status_code} {resp.text[:200]}")
        return

    rows = 0
    try:
        rows = int(resp.json().get("rows") or 0)
    except Exception:  # noqa: BLE001
        pass
    _log_result(username, rel, size, mtime, table, action, "ok", rows, f"{rows} rows")
    logger.info("sftp_watch: ingested %s -> %s (%s) %s rows", rel, table, action, rows)


def _scan_and_ingest() -> int:
    """One pass. Returns count of files ingested this tick."""
    if not os.path.isdir(_DATA_DIR):
        return 0
    umap = _user_table_map()
    now = time.time()

    from app.s3_sync import _service_token, _revoke_token
    token = _service_token()
    if not token:
        logger.warning("sftp_watch: could not mint service token — skipping tick")
        return 0

    import httpx
    done = 0
    try:
        with httpx.Client(base_url=_INTERNAL_BASE, timeout=600) as http:
            headers = {"Authorization": f"Bearer {token}"}
            for username in sorted(os.listdir(_DATA_DIR)):
                base = os.path.join(_DATA_DIR, username)
                if not os.path.isdir(base):
                    continue
                meta = umap.get(username, {"feeds_table": "", "ingest_action": "replace"})
                for dirpath, _dirs, names in os.walk(base):
                    for n in names:
                        fp = os.path.join(dirpath, n)
                        if n.startswith(".") or n.endswith((".part", ".filepart", ".tmp")):
                            continue
                        try:
                            st = os.stat(fp)
                        except OSError:
                            continue
                        if now - st.st_mtime < _STABLE_SECONDS:
                            continue  # still settling
                        rel = os.path.relpath(fp, base)
                        if _already_done(username, rel, st.st_size, int(st.st_mtime)):
                            continue
                        _ingest_one(http, headers, username, base, fp, rel, st, meta)
                        done += 1
    finally:
        _revoke_token(token)
    return done


def _retention_sweep() -> int:
    """Delete drops older than _RETENTION_HOURS for every user (SFTPGo-side)."""
    if _RETENTION_HOURS <= 0:
        return 0
    swept = 0
    try:
        from app.sftp_admin import _sftpgo, _retention_check
        r = _sftpgo("GET", "/api/v2/users", params={"limit": 500})
        if r.status_code == 200:
            for u in (r.json() or []):
                try:
                    if _retention_check(u.get("username", ""), _RETENTION_HOURS) in (200, 202):
                        swept += 1
                except Exception:  # noqa: BLE001
                    continue
    except Exception:
        logger.exception("sftp_watch: retention sweep failed")
    return swept


async def sftp_watch_loop(tick_seconds: int | None = None) -> None:
    global _last_sweep
    tick = tick_seconds or _TICK_SECONDS
    logger.info("sftp_watch daemon started (tick=%ss, dir=%s, retention=%sh)",
                tick, _DATA_DIR, _RETENTION_HOURS)
    _ensure_table()
    while True:
        try:
            if _enabled():
                n = await asyncio.to_thread(_scan_and_ingest)
                if n:
                    logger.info("sftp_watch: processed %s file(s) this tick", n)
                # periodic retention sweep
                if _RETENTION_HOURS > 0 and (time.time() - _last_sweep) > _RETENTION_SWEEP_SECONDS:
                    _last_sweep = time.time()
                    s = await asyncio.to_thread(_retention_sweep)
                    if s:
                        logger.info("sftp_watch: retention swept %s user folder(s)", s)
        except Exception:
            logger.exception("sftp_watch loop tick failed")
        await asyncio.sleep(tick)
