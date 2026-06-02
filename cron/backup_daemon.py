"""Nightly pg_dump backup with 7-day retention.

Runs as its own compose service `dash-backup` (postgres:18-alpine).
Mounts /var/backups/dash. Connects to dash-db directly (NOT pgbouncer —
pg_dump needs a real session).

Schedule: cron `0 3 * * *` (03:00 UTC daily). Retention: 7 days, glob-based.
After successful dump, updates public.dash_system_status.last_backup_at
via psql so the /health endpoint surfaces it.

Env (passed by compose):
    DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_DATABASE
    BACKUP_DIR (default /var/backups/dash)
    BACKUP_RETENTION_DAYS (default 7)

Exit codes: 0 ok, 1 dump failed, 2 status update failed (dump still kept).
"""
from __future__ import annotations
import os
import sys
import glob
import gzip
import shutil
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, default)
    return v if v is not None else default


def _run_dump(dump_path: Path) -> bool:
    """pg_dump → gzip → dump_path. Returns True on success."""
    host = _env("DB_HOST", "dash-db")
    port = _env("DB_PORT", "5432")
    user = _env("DB_USER", "ai")
    db = _env("DB_DATABASE", "ai")
    env = os.environ.copy()
    env["PGPASSWORD"] = _env("DB_PASS", "ai")
    cmd = [
        "pg_dump",
        "-h", host, "-p", port, "-U", user,
        "-d", db, "--no-owner", "--no-privileges", "--format=plain",
    ]
    print(f"[backup] pg_dump → {dump_path}", flush=True)
    try:
        with gzip.open(dump_path, "wb") as gz:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            assert proc.stdout is not None
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                gz.write(chunk)
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            rc = proc.wait()
            if rc != 0:
                print(f"[backup] pg_dump FAILED rc={rc}: {stderr[:1000]}", flush=True)
                return False
        size_mb = dump_path.stat().st_size / (1024 * 1024)
        print(f"[backup] dump OK, size={size_mb:.1f} MB", flush=True)
        return True
    except Exception as exc:
        print(f"[backup] dump exception: {exc}", flush=True)
        return False


def _prune(backup_dir: Path, retention_days: int) -> int:
    """Delete dash_YYYYMMDD.sql.gz older than retention_days. Returns count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0
    for p in glob.glob(str(backup_dir / "dash_*.sql.gz")):
        try:
            mt = datetime.fromtimestamp(Path(p).stat().st_mtime, tz=timezone.utc)
            if mt < cutoff:
                Path(p).unlink(missing_ok=True)
                deleted += 1
                print(f"[backup] pruned {p}", flush=True)
        except Exception as exc:
            print(f"[backup] prune {p} failed: {exc}", flush=True)
    return deleted


def _update_status() -> bool:
    """UPDATE public.dash_system_status.last_backup_at via psql."""
    host = _env("DB_HOST", "dash-db")
    port = _env("DB_PORT", "5432")
    user = _env("DB_USER", "ai")
    db = _env("DB_DATABASE", "ai")
    env = os.environ.copy()
    env["PGPASSWORD"] = _env("DB_PASS", "ai")
    sql = (
        "INSERT INTO public.dash_system_status (id, last_backup_at, updated_at) "
        "VALUES (1, now(), now()) "
        "ON CONFLICT (id) DO UPDATE SET last_backup_at = EXCLUDED.last_backup_at, "
        "updated_at = EXCLUDED.updated_at;"
    )
    try:
        rc = subprocess.call(
            ["psql", "-h", host, "-p", port, "-U", user, "-d", db, "-c", sql],
            env=env,
        )
        if rc != 0:
            print(f"[backup] status update FAILED rc={rc}", flush=True)
            return False
        print("[backup] dash_system_status.last_backup_at updated", flush=True)
        return True
    except Exception as exc:
        print(f"[backup] status update exception: {exc}", flush=True)
        return False


def run_once() -> int:
    backup_dir = Path(_env("BACKUP_DIR", "/var/backups/dash"))
    retention = int(_env("BACKUP_RETENTION_DAYS", "7"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    dump_path = backup_dir / f"dash_{stamp}.sql.gz"
    ok = _run_dump(dump_path)
    if not ok:
        try:
            dump_path.unlink(missing_ok=True)
        except Exception:
            pass
        return 1
    _prune(backup_dir, retention)
    if not _update_status():
        return 2
    return 0


def main() -> int:
    """Entry. If $BACKUP_RUN_ONCE=1 → run once and exit. Otherwise loop daily."""
    if os.environ.get("BACKUP_RUN_ONCE") in ("1", "true", "TRUE", "yes"):
        return run_once()
    # Simple daemon loop: sleep until next 03:00 UTC, run, repeat.
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if target <= now:
            target = target + timedelta(days=1)
        sleep_s = max(60, int((target - now).total_seconds()))
        print(f"[backup] sleeping {sleep_s}s until {target.isoformat()}", flush=True)
        time.sleep(sleep_s)
        try:
            run_once()
        except Exception as exc:
            print(f"[backup] run_once crashed: {exc}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
