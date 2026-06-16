"""SFTP drop-folder administration — superadmin-only.

Thin control plane over the SFTPGo sidecar (container `cp-sftpgo`). The admin
console (/command-center → "SFTP Access") drives these endpoints to manage the
virtual SFTP users that pharmacies use to drop data files. SFTP users live in
SFTPGo's own Postgres tables (prefix `sftpgo_`) — they are NEVER app login
(dash_users) accounts and have zero access to the app, DB, or other users.

This module only TALKS to SFTPGo's REST API (localhost-bound admin port, proxied
server-side here) + reads the dropped files via the read-only `sftp_data` mount.
The Phase-2 watcher ingests those files via the existing /api/upload pipeline.

Security:
- Every endpoint gated by `_require_super` (superadmin only).
- Per-user metadata (which table they may feed) stored in the SFTPGo user
  `description` as JSON — the watcher reads it; the user can never see it.
- "drop" permission = upload-only (can't list/download/delete) → pure dropbox.
- All mutations written to dash_audit_log via log_action.
"""

from __future__ import annotations

import json
import logging
import time
from os import getenv
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/sftp", tags=["sftp-admin"])

# ── config ───────────────────────────────────────────────────────────────────
SFTPGO_BASE_URL = getenv("SFTPGO_BASE_URL", "http://cp-sftpgo:8080").rstrip("/")
SFTPGO_ADMIN_USER = getenv("SFTPGO_ADMIN_USER", "sftpadmin")
SFTPGO_ADMIN_PASS = getenv("SFTPGO_ADMIN_PASS", "")
SFTP_DATA_DIR = getenv("SFTP_DATA_DIR", "/app/sftp_data")
SFTP_USERS_BASE_DIR = getenv("SFTP_USERS_BASE_DIR", "/srv/sftpgo/data")  # path INSIDE sftpgo
SFTP_PUBLIC_HOST = getenv("SFTP_PUBLIC_HOST", "")  # shown in UI connection hint
SFTP_PUBLIC_PORT = getenv("SFTP_PUBLIC_PORT", "2222")

# permission presets
# "drop" = list + upload only: they can SEE their own folder and put files, but
# CANNOT download (no data exfiltration) or delete. NOTE: "list" is required —
# the OpenSSH sftp client does realpath(".") on connect and fails without it.
_PERMS_DROP = ["list", "upload"]
_PERMS_RW = ["list", "upload", "download", "create_dirs", "rename", "delete"]


# ── auth helpers (mirror app/admin_api.py house style) ───────────────────────
def _get_user(request: Request) -> dict:
    from app.auth import get_current_user

    user = getattr(getattr(request, "state", None), "user", None) or get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_super(user: dict):
    if not user.get("is_super") and not user.get("is_super_admin"):
        raise HTTPException(403, "super-admin only")


# ── SFTPGo REST client (token cached ~15m; localhost/internal call) ──────────
_token_cache: dict[str, Any] = {"tok": "", "exp": 0.0}


def _sftpgo_token() -> str:
    now = time.time()
    if _token_cache["tok"] and _token_cache["exp"] > now + 30:
        return _token_cache["tok"]
    if not SFTPGO_ADMIN_PASS:
        raise HTTPException(503, "SFTP server not configured (SFTPGO_ADMIN_PASS unset)")
    try:
        r = httpx.get(
            f"{SFTPGO_BASE_URL}/api/v2/token",
            auth=(SFTPGO_ADMIN_USER, SFTPGO_ADMIN_PASS),
            timeout=8,
        )
        r.raise_for_status()
        tok = r.json().get("access_token", "")
    except Exception as e:  # noqa: BLE001
        logger.warning("sftp: token fetch failed: %s", e)
        raise HTTPException(502, "SFTP server unreachable")
    if not tok:
        raise HTTPException(502, "SFTP server returned no token")
    _token_cache["tok"] = tok
    _token_cache["exp"] = now + 15 * 60  # SFTPGo default JWT life 20m; refresh early
    return tok


def _sftpgo(method: str, path: str, *, json_body: Optional[dict] = None,
            params: Optional[dict] = None) -> httpx.Response:
    tok = _sftpgo_token()
    try:
        r = httpx.request(
            method,
            f"{SFTPGO_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {tok}"},
            json=json_body,
            params=params,
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("sftp: %s %s failed: %s", method, path, e)
        raise HTTPException(502, "SFTP server unreachable")
    if r.status_code == 401:  # token expired mid-flight → one retry
        _token_cache["tok"] = ""
        tok = _sftpgo_token()
        r = httpx.request(
            method, f"{SFTPGO_BASE_URL}{path}",
            headers={"Authorization": f"Bearer {tok}"},
            json=json_body, params=params, timeout=15,
        )
    return r


# ── per-user metadata stashed in SFTPGo `description` (JSON) ──────────────────
def _pack_meta(feeds_table: str, ingest_action: str) -> str:
    return json.dumps({"feeds_table": feeds_table or "", "ingest_action": ingest_action or "replace"})


def _unpack_meta(description: str) -> dict:
    try:
        d = json.loads(description or "{}")
        if isinstance(d, dict):
            return {"feeds_table": d.get("feeds_table", ""), "ingest_action": d.get("ingest_action", "replace")}
    except Exception:  # noqa: BLE001
        pass
    return {"feeds_table": "", "ingest_action": "replace"}


def _shape_user(u: dict) -> dict:
    """Trim SFTPGo's verbose user record to what the console needs (no secrets)."""
    meta = _unpack_meta(u.get("description", ""))
    perms = (u.get("permissions") or {}).get("/", [])
    mode = "drop" if perms == _PERMS_DROP else "readwrite"
    auth = "key" if (u.get("public_keys") or []) else "password"
    return {
        "username": u.get("username", ""),
        "status": u.get("status", 1),
        "auth": auth,
        "mode": mode,
        "feeds_table": meta["feeds_table"],
        "ingest_action": meta["ingest_action"],
        "quota_mb": round((u.get("quota_size") or 0) / (1024 * 1024)) or 0,
        "max_sessions": u.get("max_sessions", 0),
        "expiration_date": u.get("expiration_date", 0),
        "allowed_extensions": (((u.get("filters") or {}).get("file_patterns") or [{}])[0].get("allowed_patterns") or []),
        "last_login": u.get("last_login", 0),
        "num_keys": len(u.get("public_keys") or []),
    }


# ── request models ───────────────────────────────────────────────────────────
class SftpUserIn(BaseModel):
    username: str
    auth: str = "key"                # "key" | "password"
    public_key: Optional[str] = None  # required when auth == "key"
    password: Optional[str] = None    # optional; if blank + auth==password, server generates
    mode: str = "drop"               # "drop" (upload-only) | "readwrite"
    feeds_table: str = ""            # table this user is allowed to feed (Phase 2 watcher)
    ingest_action: str = "replace"   # replace | append | upsert
    allowed_extensions: list[str] = [".csv", ".xlsx", ".parquet"]
    quota_mb: int = 500
    max_sessions: int = 2
    expiration_date: int = 0         # ms epoch; 0 = never


class SftpUserPatch(BaseModel):
    mode: Optional[str] = None
    feeds_table: Optional[str] = None
    ingest_action: Optional[str] = None
    quota_mb: Optional[int] = None
    max_sessions: Optional[int] = None
    expiration_date: Optional[int] = None
    status: Optional[int] = None        # 1 enabled / 0 disabled
    new_password: Optional[str] = None  # reset password
    public_key: Optional[str] = None    # rotate/replace key


def _gen_password(n: int = 24) -> str:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _build_sftpgo_payload(body: SftpUserIn) -> tuple[dict, Optional[str]]:
    """Return (sftpgo_user_json, plaintext_password_if_generated)."""
    perms = _PERMS_DROP if body.mode == "drop" else _PERMS_RW
    gen_pw = None
    payload: dict[str, Any] = {
        "username": body.username,
        "status": 1,
        "home_dir": f"{SFTP_USERS_BASE_DIR}/{body.username}",
        "permissions": {"/": perms},
        "quota_size": max(0, int(body.quota_mb)) * 1024 * 1024,
        "max_sessions": max(0, int(body.max_sessions)),
        "expiration_date": max(0, int(body.expiration_date)),
        "description": _pack_meta(body.feeds_table, body.ingest_action),
    }
    if body.allowed_extensions:
        payload["filters"] = {
            "file_patterns": [{"path": "/", "allowed_patterns": [
                ("*" + e if e.startswith(".") else e) for e in body.allowed_extensions
            ]}]
        }
    if body.auth == "key":
        if not body.public_key:
            raise HTTPException(400, "public_key required for key auth")
        payload["public_keys"] = [body.public_key.strip()]
    else:
        pw = (body.password or "").strip() or _gen_password()
        if not body.password:
            gen_pw = pw
        payload["password"] = pw
    return payload, gen_pw


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("/status")
def sftp_status(request: Request):
    _require_super(_get_user(request))
    out = {"configured": bool(SFTPGO_ADMIN_PASS), "reachable": False, "user_count": 0,
           "sftp_port": SFTP_PUBLIC_PORT, "host": SFTP_PUBLIC_HOST}
    if not SFTPGO_ADMIN_PASS:
        return out
    try:
        r = _sftpgo("GET", "/api/v2/users", params={"limit": 500})
        if r.status_code == 200:
            out["reachable"] = True
            out["user_count"] = len(r.json() or [])
    except HTTPException:
        pass
    return out


@router.get("/users")
def sftp_list_users(request: Request):
    _require_super(_get_user(request))
    r = _sftpgo("GET", "/api/v2/users", params={"limit": 500, "order": "ASC"})
    if r.status_code != 200:
        raise HTTPException(502, f"SFTP list failed ({r.status_code})")
    return {"users": [_shape_user(u) for u in (r.json() or [])]}


@router.post("/users")
def sftp_create_user(body: SftpUserIn, request: Request):
    user = _get_user(request)
    _require_super(user)
    if not body.username or not body.username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "username must be alphanumeric (-/_ allowed)")
    payload, gen_pw = _build_sftpgo_payload(body)
    r = _sftpgo("POST", "/api/v2/users", json_body=payload)
    if r.status_code not in (200, 201):
        detail = ""
        try:
            detail = r.json().get("error", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise HTTPException(400, f"create failed: {detail or r.status_code}")
    _audit(user, "sftp_user_create", body.username,
           f"mode={body.mode} table={body.feeds_table} auth={body.auth}")
    resp: dict[str, Any] = {"status": "ok", "username": body.username}
    if gen_pw:
        resp["generated_password"] = gen_pw  # shown ONCE in the UI
    return resp


@router.patch("/users/{username}")
def sftp_update_user(username: str, body: SftpUserPatch, request: Request):
    user = _get_user(request)
    _require_super(user)
    # fetch current, then merge (SFTPGo PUT replaces the record)
    cur = _sftpgo("GET", f"/api/v2/users/{username}")
    if cur.status_code != 200:
        raise HTTPException(404, "user not found")
    rec = cur.json()
    meta = _unpack_meta(rec.get("description", ""))
    if body.mode is not None:
        rec["permissions"] = {"/": _PERMS_DROP if body.mode == "drop" else _PERMS_RW}
    if body.feeds_table is not None:
        meta["feeds_table"] = body.feeds_table
    if body.ingest_action is not None:
        meta["ingest_action"] = body.ingest_action
    rec["description"] = _pack_meta(meta["feeds_table"], meta["ingest_action"])
    if body.quota_mb is not None:
        rec["quota_size"] = max(0, int(body.quota_mb)) * 1024 * 1024
    if body.max_sessions is not None:
        rec["max_sessions"] = max(0, int(body.max_sessions))
    if body.expiration_date is not None:
        rec["expiration_date"] = max(0, int(body.expiration_date))
    if body.status is not None:
        rec["status"] = 1 if body.status else 0
    gen_pw = None
    if body.new_password is not None:
        pw = body.new_password.strip() or _gen_password()
        if not body.new_password:
            gen_pw = pw
        rec["password"] = pw
    if body.public_key is not None:
        rec["public_keys"] = [body.public_key.strip()] if body.public_key.strip() else []
    r = _sftpgo("PUT", f"/api/v2/users/{username}", json_body=rec)
    if r.status_code != 200:
        raise HTTPException(400, f"update failed ({r.status_code})")
    _audit(user, "sftp_user_update", username,
           f"fields={[k for k,v in body.model_dump().items() if v is not None]}")
    resp: dict[str, Any] = {"status": "ok"}
    if gen_pw:
        resp["generated_password"] = gen_pw
    return resp


@router.delete("/users/{username}")
def sftp_delete_user(username: str, request: Request):
    user = _get_user(request)
    _require_super(user)
    r = _sftpgo("DELETE", f"/api/v2/users/{username}")
    if r.status_code not in (200, 404):
        raise HTTPException(400, f"delete failed ({r.status_code})")
    _audit(user, "sftp_user_delete", username, "deleted")
    return {"status": "ok"}


@router.get("/keygen")
def sftp_keygen(request: Request):
    """Generate an ed25519 keypair server-side. Private key returned ONCE so the
    admin can hand it to a non-technical pharmacy. We keep only the public key
    (set on the user)."""
    _require_super(_get_user(request))
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception:  # noqa: BLE001
        raise HTTPException(500, "key generation unavailable (cryptography missing)")
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    pub = k.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    return {"private_key": priv, "public_key": pub}


@router.get("/files")
def sftp_browse(request: Request, username: str = ""):
    """List dropped files from the read-only mount. If username given, that user's
    folder; else a summary across all users."""
    _require_super(_get_user(request))
    import os
    root = SFTP_DATA_DIR
    if not os.path.isdir(root):
        return {"users": [], "files": []}
    if username:
        base = os.path.join(root, username)
        files = []
        if os.path.isdir(base):
            for dirpath, _dirs, names in os.walk(base):
                for n in names:
                    fp = os.path.join(dirpath, n)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    files.append({
                        "name": n,
                        "rel": os.path.relpath(fp, base),
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                    })
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return {"username": username, "files": files}
    # summary per user
    users = []
    for name in sorted(os.listdir(root)):
        base = os.path.join(root, name)
        if not os.path.isdir(base):
            continue
        cnt = 0
        newest = 0
        total = 0
        for dirpath, _dirs, names in os.walk(base):
            for n in names:
                fp = os.path.join(dirpath, n)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                cnt += 1
                total += st.st_size
                newest = max(newest, int(st.st_mtime))
        users.append({"username": name, "file_count": cnt, "total_bytes": total, "newest": newest})
    return {"users": users}


@router.get("/files-all")
def sftp_browse_all(request: Request, limit: int = 1000):
    """Global file explorer: every dropped file across every user, joined with its
    latest ingest result so the admin can see at a glance which files landed,
    which are still uploading, and which failed to ingest."""
    _require_super(_get_user(request))
    import os
    root = SFTP_DATA_DIR

    # latest ingest status per (username, rel_path) from the watcher log
    ingest: dict[tuple, dict] = {}
    try:
        from db import get_sql_engine
        from sqlalchemy import text as _t
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(_t(
                "SELECT DISTINCT ON (username, rel_path) username, rel_path, table_name, "
                "action, status, rows_loaded, message, "
                "to_char(created_at,'YYYY-MM-DD HH24:MI') AS ts "
                "FROM public.dash_sftp_ingest_log "
                "ORDER BY username, rel_path, created_at DESC"
            )).fetchall()
        for r in rows:
            ingest[(r[0], r[1])] = {
                "table_name": r[2], "action": r[3], "ingest_status": r[4],
                "rows_loaded": r[5], "message": r[6], "ingested_at": r[7],
            }
    except Exception:  # log table not created yet → no ingest info
        pass

    files = []
    total_bytes = 0
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            base = os.path.join(root, name)
            if not os.path.isdir(base):
                continue
            for dirpath, _dirs, names in os.walk(base):
                for n in names:
                    fp = os.path.join(dirpath, n)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    rel = os.path.relpath(fp, base)
                    total_bytes += st.st_size
                    # partial upload markers SFTPGo/clients leave mid-transfer
                    partial = n.endswith((".part", ".filepart", ".tmp", ".uploading"))
                    ing = ingest.get((name, rel))
                    if partial:
                        status = "uploading"
                    elif ing:
                        raw = ing.get("ingest_status")
                        status = {"ok": "ingested", "error": "failed"}.get(raw, raw or "pending")
                    else:
                        status = "pending"
                    files.append({
                        "username": name, "name": n, "rel": rel,
                        "size": st.st_size, "mtime": int(st.st_mtime),
                        "status": status,
                        "table_name": (ing or {}).get("table_name"),
                        "action": (ing or {}).get("action"),
                        "rows_loaded": (ing or {}).get("rows_loaded"),
                        "message": (ing or {}).get("message"),
                        "ingested_at": (ing or {}).get("ingested_at"),
                    })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {
        "files": files[: max(1, min(int(limit), 5000))],
        "file_count": len(files),
        "total_bytes": total_bytes,
    }


def _retention_check(username: str, hours: int) -> int:
    """Ask SFTPGo to delete files older than `hours` in this user's folder.
    SFTPGo does the deletion server-side (correct ownership) — the app mount is
    read-only so we never delete files ourselves. Returns the HTTP status."""
    body = [{
        "path": "/", "retention": max(1, int(hours)),
        "delete_empty_dirs": False, "ignore_user_permissions": True,
    }]
    r = _sftpgo("POST", f"/api/v2/retention/users/{username}/check", json_body=body)
    return r.status_code


@router.post("/retention/{username}")
def sftp_retention(username: str, request: Request, hours: int = 168):
    user = _get_user(request)
    _require_super(user)
    sc = _retention_check(username, hours)
    if sc not in (200, 202):
        raise HTTPException(400, f"retention failed ({sc})")
    _audit(user, "sftp_retention", username, f"purge>{hours}h")
    return {"status": "ok"}


@router.post("/retention-all")
def sftp_retention_all(request: Request, hours: int = 168):
    """Purge old drops across every SFTP user (manual 'clean up now')."""
    user = _get_user(request)
    _require_super(user)
    r = _sftpgo("GET", "/api/v2/users", params={"limit": 500})
    if r.status_code != 200:
        raise HTTPException(502, "could not list users")
    done = 0
    for u in (r.json() or []):
        try:
            if _retention_check(u.get("username", ""), hours) in (200, 202):
                done += 1
        except Exception:  # noqa: BLE001
            continue
    _audit(user, "sftp_retention_all", "*", f"purge>{hours}h users={done}")
    return {"status": "ok", "users_swept": done}


@router.get("/ingest-log")
def sftp_ingest_log(request: Request, limit: int = 50):
    """Recent SFTP ingest results (from the Phase-2 watcher) for the console feed."""
    _require_super(_get_user(request))
    try:
        from db import get_sql_engine
        from sqlalchemy import text as _t
        eng = get_sql_engine()
        with eng.connect() as conn:
            rows = conn.execute(_t(
                "SELECT username, rel_path, table_name, action, status, rows_loaded, message, "
                "to_char(created_at,'YYYY-MM-DD HH24:MI') AS ts "
                "FROM public.dash_sftp_ingest_log ORDER BY created_at DESC LIMIT :l"
            ), {"l": max(1, min(int(limit), 200))}).fetchall()
        return {"log": [{
            "username": r[0], "rel_path": r[1], "table_name": r[2], "action": r[3],
            "status": r[4], "rows_loaded": r[5], "message": r[6], "ts": r[7],
        } for r in rows]}
    except Exception:  # table not created yet (watcher never ran) → empty
        return {"log": []}


# ── audit helper ─────────────────────────────────────────────────────────────
def _audit(user: dict, action: str, username: str, details: str):
    try:
        from app.auth import log_action
        log_action(user, action, "sftp_user", username, details)
    except Exception:  # noqa: BLE001
        logger.exception("sftp: audit log failed")
