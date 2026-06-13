"""S3 auto-sync: poll a bucket, replace tables from changed objects, retrain.

Per source (public.dash_s3_sources):
  1. list objects under prefix
  2. match each object's filename to a file_map rule -> (table, action)
  3. sync only objects whose ETag changed since last time (or force=True)
  4. for each changed object: download -> POST /api/upload (action='replace' by
     default) so the FULL existing ingest+profile pipeline runs and the old table
     is dropped + replaced
  5. if anything changed and retrain_after: POST /api/projects/{slug}/retrain?force=1
  6. record per-object ETag + the source's last_status/last_log

Reuses the running app's own HTTP endpoints (internal call to 127.0.0.1:8000) so
the entire battle-tested upload/train pipeline is reused, not reimplemented.
Credentials are Fernet-decrypted only in memory, never logged.
"""
from __future__ import annotations

import fnmatch
import logging
import os
import secrets
import tempfile
import time
from typing import Any

import httpx
from sqlalchemy import create_engine as _create_engine, text
from sqlalchemy.pool import NullPool

from db import db_url

logger = logging.getLogger(__name__)
_engine = _create_engine(db_url, poolclass=NullPool)

# Internal app base (gunicorn binds 0.0.0.0:8000 inside the container).
_INTERNAL_BASE = os.getenv("AGENTOS_URL", "http://127.0.0.1:8000").rstrip("/")
_SUPER_ADMIN = os.getenv("SUPER_ADMIN", "admin")


# ---------------------------------------------------------------------------
# credentials + service auth
# ---------------------------------------------------------------------------

def _decrypt_creds(creds_enc: str | None) -> dict:
    if not creds_enc:
        return {}
    try:
        from dash.connectors.crypto import decrypt_credentials
        return decrypt_credentials(creds_enc) or {}
    except Exception:
        logger.exception("s3_sync: failed to decrypt credentials")
        return {}


def encrypt_creds(access_key: str, secret_key: str) -> str:
    from dash.connectors.crypto import encrypt_credentials
    return encrypt_credentials({"access_key": access_key or "", "secret_key": secret_key or ""})


def _service_token(ttl_seconds: int = 3600) -> str | None:
    """Mint a short-lived token for the super-admin so the internal upload/retrain
    calls authenticate as an owner. Cleaned up by normal token expiry."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT id, username FROM public.dash_users WHERE username = :u "
                "OR role = 'super' ORDER BY (username = :u) DESC, id ASC LIMIT 1"
            ), {"u": _SUPER_ADMIN}).fetchone()
            if not row:
                return None
            tok = "s3svc-" + secrets.token_urlsafe(32)
            expiry = int(time.time()) + ttl_seconds
            conn.execute(text(
                "INSERT INTO public.dash_tokens (token, user_id, username, expiry) "
                "VALUES (:t, :uid, :u, :e)"
            ), {"t": tok, "uid": row[0], "u": row[1], "e": expiry})
            conn.commit()
            return tok
    except Exception:
        logger.exception("s3_sync: service token mint failed")
        return None


def _revoke_token(tok: str | None) -> None:
    if not tok:
        return
    try:
        with _engine.connect() as conn:
            conn.execute(text("DELETE FROM public.dash_tokens WHERE token = :t"), {"t": tok})
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# source CRUD helpers (used by the API + daemon)
# ---------------------------------------------------------------------------

def get_source(source_id: int) -> dict | None:
    with _engine.connect() as conn:
        r = conn.execute(text(
            "SELECT id, project_slug, name, bucket, prefix, region, endpoint_url, creds_enc, "
            "file_map, schedule_seconds, retrain_after, enabled, last_sync_at, last_status, last_log "
            "FROM public.dash_s3_sources WHERE id = :i"
        ), {"i": source_id}).fetchone()
    if not r:
        return None
    return {
        "id": r[0], "project_slug": r[1], "name": r[2], "bucket": r[3], "prefix": r[4],
        "region": r[5], "endpoint_url": r[6], "creds_enc": r[7], "file_map": r[8] or [],
        "schedule_seconds": r[9], "retrain_after": r[10], "enabled": r[11],
        "last_sync_at": str(r[12]) if r[12] else None, "last_status": r[13], "last_log": r[14],
    }


def _match_rule(filename: str, file_map: list[dict]) -> dict | None:
    """First file_map rule whose glob pattern matches the object's basename."""
    base = filename.rsplit("/", 1)[-1]
    for rule in file_map or []:
        pat = (rule.get("pattern") or "").strip()
        if pat and fnmatch.fnmatch(base, pat):
            return rule
    return None


def _set_status(source_id: int, status: str, log: str | None = None, touch_sync: bool = False) -> None:
    try:
        with _engine.connect() as conn:
            if touch_sync:
                conn.execute(text(
                    "UPDATE public.dash_s3_sources SET last_status=:s, last_log=:l, "
                    "last_sync_at=now(), updated_at=now() WHERE id=:i"
                ), {"s": status, "l": (log or "")[:8000], "i": source_id})
            else:
                conn.execute(text(
                    "UPDATE public.dash_s3_sources SET last_status=:s, last_log=:l, "
                    "updated_at=now() WHERE id=:i"
                ), {"s": status, "l": (log or "")[:8000], "i": source_id})
            conn.commit()
    except Exception:
        logger.exception("s3_sync: status update failed")


# ---------------------------------------------------------------------------
# the sync itself
# ---------------------------------------------------------------------------

def test_connection(source_id: int) -> dict:
    """List objects (read-only) to verify bucket + creds + prefix. No ingest."""
    src = get_source(source_id)
    if not src:
        return {"ok": False, "error": "source not found"}
    from dash.connectors import s3_client
    if not s3_client.boto3_available():
        return {"ok": False, "error": "S3 support not installed (boto3 missing)"}
    creds = _decrypt_creds(src["creds_enc"])
    try:
        cl = s3_client.make_client(src["region"], creds.get("access_key", ""),
                                   creds.get("secret_key", ""), src["endpoint_url"])
        objs = s3_client.list_objects(cl, src["bucket"], src["prefix"])
        matched = [o["key"] for o in objs if _match_rule(o["key"], src["file_map"])]
        return {"ok": True, "objects": len(objs), "matched": len(matched),
                "sample": [o["key"] for o in objs[:10]]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _notify_admins(ntype: str, title: str, message: str) -> None:
    """Drop a notification into the bell for every admin/super user. Fail-soft —
    a notification failure must never break or mask a sync result. (#6)"""
    try:
        with _engine.begin() as conn:
            admins = [r[0] for r in conn.execute(text(
                "SELECT id FROM public.dash_users WHERE role IN ('admin','super') AND COALESCE(is_active, TRUE)"
            )).fetchall()]
            for uid in admins:
                conn.execute(text(
                    "INSERT INTO public.dash_notifications (user_id, type, title, message) "
                    "VALUES (:uid, :t, :ti, :m)"
                ), {"uid": uid, "t": ntype, "ti": title[:200], "m": message[:2000]})
    except Exception as e:
        logger.warning("s3_sync: notify_admins failed (non-fatal): %s", e)


def run_s3_sync(source_id: int, force: bool = False, triggered_by: str = "daemon") -> dict:
    """Sync one source. Returns a summary dict (also stored on the source row)."""
    src = get_source(source_id)
    if not src:
        return {"ok": False, "error": "source not found"}

    from dash.connectors import s3_client
    if not s3_client.boto3_available():
        _set_status(source_id, "error", "S3 support not installed (boto3 missing)")
        return {"ok": False, "error": "boto3 missing"}

    slug = src["project_slug"]
    creds = _decrypt_creds(src["creds_enc"])
    lines: list[str] = [f"[{triggered_by}] sync start bucket={src['bucket']} prefix={src['prefix']!r}"]
    _set_status(source_id, "running", "\n".join(lines))

    changed = 0
    errors = 0
    token = _service_token()
    if not token:
        _set_status(source_id, "error", "could not mint service token")
        return {"ok": False, "error": "no service token"}

    try:
        cl = s3_client.make_client(src["region"], creds.get("access_key", ""),
                                   creds.get("secret_key", ""), src["endpoint_url"])
        objects = s3_client.list_objects(cl, src["bucket"], src["prefix"])
        lines.append(f"listed {len(objects)} object(s)")

        # current known etags
        with _engine.connect() as conn:
            known = {r[0]: r[1] for r in conn.execute(text(
                "SELECT object_key, etag FROM public.dash_s3_object_state WHERE source_id=:i"
            ), {"i": source_id}).fetchall()}

        with httpx.Client(base_url=_INTERNAL_BASE, timeout=600) as http:
            headers = {"Authorization": f"Bearer {token}"}
            for obj in objects:
                rule = _match_rule(obj["key"], src["file_map"])
                if not rule:
                    continue
                table = (rule.get("table") or "").strip()
                action = (rule.get("action") or "replace").strip() or "replace"
                if not table:
                    continue
                if not force and known.get(obj["key"]) == obj["etag"]:
                    continue  # unchanged

                # download → upload(action) → record etag
                tmp = tempfile.NamedTemporaryFile(delete=False,
                                                  suffix="_" + obj["key"].rsplit("/", 1)[-1])
                tmp.close()
                try:
                    s3_client.download_object(cl, src["bucket"], obj["key"], tmp.name)
                    with open(tmp.name, "rb") as fh:
                        # guarded=1 → upload enforces the safety holds (empty file,
                        # schema drift, row-count cliff) since this is unattended.
                        resp = http.post(
                            "/api/upload",
                            headers=headers,
                            params={"project": slug, "table_name": table, "action": action, "guarded": "1"},
                            data={"project": slug, "table_name": table, "action": action, "guarded": "1"},
                            files={"file": (obj["key"].rsplit("/", 1)[-1], fh, "application/octet-stream")},
                        )
                    if resp.status_code >= 400:
                        errors += 1
                        # A 409 = a safety hold (drift/cliff/empty) deliberately kept
                        # the old data. We DON'T advance the etag (no state write
                        # below — the `continue` skips it) so a corrected file
                        # re-syncs automatically next cycle.
                        _held = " [HELD — old data kept]" if resp.status_code == 409 else ""
                        lines.append(f"✗ {obj['key']} -> {table} ({action}): HTTP {resp.status_code}{_held} {resp.text[:200]}")
                        continue
                    rows = 0
                    try:
                        rows = int(resp.json().get("rows") or 0)
                    except Exception:
                        pass
                    changed += 1
                    lines.append(f"✓ {obj['key']} -> {table} ({action}) {rows} rows")
                    with _engine.connect() as conn:
                        conn.execute(text(
                            "INSERT INTO public.dash_s3_object_state "
                            "(source_id, object_key, etag, last_modified, table_name, rows_loaded, synced_at) "
                            "VALUES (:i,:k,:e,:lm,:t,:r, now()) "
                            "ON CONFLICT (source_id, object_key) DO UPDATE SET "
                            "etag=EXCLUDED.etag, last_modified=EXCLUDED.last_modified, "
                            "table_name=EXCLUDED.table_name, rows_loaded=EXCLUDED.rows_loaded, synced_at=now()"
                        ), {"i": source_id, "k": obj["key"], "e": obj["etag"],
                            "lm": obj["last_modified"], "t": table, "r": rows})
                        conn.commit()
                finally:
                    try:
                        os.unlink(tmp.name)
                    except Exception:
                        pass

            # retrain once if anything changed
            if changed and src["retrain_after"]:
                lines.append(f"retraining {slug} (force) …")
                try:
                    rt = http.post(f"/api/projects/{slug}/retrain",
                                   headers=headers, params={"force": "1"}, json={"force": True})
                    lines.append(f"retrain HTTP {rt.status_code}")
                except Exception as e:
                    lines.append(f"retrain call failed: {e}")

        status = "error" if errors else "ok"
        lines.append(f"done: {changed} changed, {errors} error(s)")
        _set_status(source_id, status, "\n".join(lines), touch_sync=True)
        if errors:
            # #6 surface sync failures/holds in the notification bell — a disabled
            # or erroring source was otherwise silent (only in the log text).
            _tail = "\n".join(lines[-6:])
            _notify_admins(
                "s3_sync_error",
                f"S3 sync '{src.get('name') or source_id}': {errors} error(s)",
                _tail,
            )
        return {"ok": errors == 0, "changed": changed, "errors": errors, "log": "\n".join(lines)}
    except Exception as e:
        logger.exception("s3_sync: run failed for source %s", source_id)
        lines.append(f"FAILED: {e}")
        _set_status(source_id, "error", "\n".join(lines), touch_sync=True)
        _notify_admins("s3_sync_error",
                       f"S3 sync '{src.get('name') if isinstance(src, dict) else source_id}' FAILED",
                       str(e)[:400])
        return {"ok": False, "error": str(e), "log": "\n".join(lines)}
    finally:
        _revoke_token(token)


def due_source_ids() -> list[int]:
    """Enabled sources whose schedule interval has elapsed since last_sync_at."""
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id FROM public.dash_s3_sources WHERE enabled = TRUE AND ("
                "  last_sync_at IS NULL OR "
                "  now() - last_sync_at >= make_interval(secs => GREATEST(schedule_seconds, 60))"
                ")"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception:
        logger.exception("s3_sync: due_source_ids failed")
        return []
