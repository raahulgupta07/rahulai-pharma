"""Artifact registry — register/list/get/delete generated files.

Extends ``dash.dash_generated_files`` (migration 042 + 054). Files live on
disk under ``/app/knowledge/generated/{project_slug or '_global'}/{file_id}.{ext}``
(same pattern as 042 Reporter). Thumbnails for images stored inline as BYTEA.

This module is the canonical entry point for any pipeline that wants to
surface a file in the Artifact Gallery (CSV, PNG, JSON, MD, PDF, etc.).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Storage root — matches the Reporter 042 convention.
_STORAGE_ROOT = os.environ.get(
    "DASH_GENERATED_FILES_DIR", "/app/knowledge/generated"
)

_MIME = {
    "csv":  "text/csv",
    "json": "application/json",
    "md":   "text/markdown",
    "html": "text/html",
    "txt":  "text/plain",
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "svg":  "image/svg+xml",
    "pdf":  "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "parquet": "application/octet-stream",
}

_IMAGE_KINDS = {"png", "jpg", "jpeg"}

_KIND_CHOICES = {
    "csv", "json", "md", "html", "txt", "png", "jpg", "jpeg",
    "svg", "pdf", "pptx", "xlsx", "docx", "parquet", "other",
}


def _engine() -> Engine:
    try:
        from db.session import get_sql_engine  # type: ignore
        return get_sql_engine()
    except Exception:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool
        from db.url import db_url  # type: ignore
        return create_engine(db_url, poolclass=NullPool)


def _detect_kind(name: str, content: bytes) -> str:
    ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
    if ext in _KIND_CHOICES:
        return ext
    # Magic byte fallbacks
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"PK\x03\x04"):
        if ext == "":
            return "other"  # could be pptx/xlsx/docx/zip
    if content.startswith(b"<?xml") or content.startswith(b"<svg"):
        return "svg"
    if content.lstrip()[:1] in (b"{", b"["):
        return "json"
    return "other"


def mime_for(kind: str, filename: str) -> str:
    k = (kind or "").lower()
    if k in _MIME:
        return _MIME[k]
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()
    return _MIME.get(ext, "application/octet-stream")


def _make_thumbnail(content: bytes, max_dim: int = 240) -> Optional[bytes]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(content))
        im.thumbnail((max_dim, max_dim))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as e:
        logger.debug("thumbnail failed: %s", e)
        return None


def _storage_path(project_slug: Optional[str], file_id: str, kind: str) -> Path:
    bucket = project_slug or "_global"
    root = Path(_STORAGE_ROOT) / bucket
    root.mkdir(parents=True, exist_ok=True)
    ext = kind if kind and kind != "other" else "bin"
    return root / f"{file_id}.{ext}"


def register_artifact(
    project: Optional[str],
    run_id: Optional[str],
    name: str,
    content_bytes: bytes,
    kind: Optional[str] = None,
    *,
    user_id: Optional[int] = None,
    agent_name: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Persist an artifact to disk + register row in dash_generated_files.

    Returns the new artifact id (``gen_<8hex>``). Idempotent only by id.
    Safe to call from background pipelines — failures logged, never raised
    out of this function unless storage write itself fails.
    """
    if not isinstance(content_bytes, (bytes, bytearray)):
        raise TypeError("content_bytes must be bytes")
    content_bytes = bytes(content_bytes)

    detected = (kind or _detect_kind(name, content_bytes)).lower()
    if detected not in _KIND_CHOICES:
        detected = "other"

    file_id = f"gen_{secrets.token_hex(4)}"
    path = _storage_path(project, file_id, detected)
    path.write_bytes(content_bytes)

    thumb: Optional[bytes] = None
    if detected in _IMAGE_KINDS:
        thumb = _make_thumbnail(content_bytes)

    size_bytes = len(content_bytes)

    eng = _engine()
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO dash.dash_generated_files
                      (id, project_slug, user_id, agent_name, run_id,
                       file_type, kind, filename, storage_path,
                       size_bytes, thumbnail, metadata)
                    VALUES
                      (:id, :ps, :uid, :ag, :rid,
                       :ft, :kd, :fn, :sp,
                       :sz, :th, CAST(:meta AS jsonb))
                    """
                ),
                {
                    "id": file_id,
                    "ps": project,
                    "uid": user_id,
                    "ag": agent_name,
                    "rid": run_id,
                    "ft": detected,
                    "kd": detected,
                    "fn": name,
                    "sp": str(path),
                    "sz": size_bytes,
                    "th": thumb,
                    "meta": _json_dumps(metadata),
                },
            )
    except Exception:
        logger.exception("register_artifact: db insert failed (file kept on disk)")
        raise
    return file_id


def _json_dumps(d: Optional[dict]) -> Optional[str]:
    if d is None:
        return None
    import json
    try:
        return json.dumps(d, default=str)
    except Exception:
        return None


def get_artifact(artifact_id: str) -> Optional[dict]:
    """Return artifact with raw content bytes loaded from disk."""
    meta = _get_meta(artifact_id, include_thumbnail=False)
    if not meta:
        return None
    path = Path(meta["storage_path"]) if meta.get("storage_path") else None
    content: bytes = b""
    if path and path.exists():
        try:
            content = path.read_bytes()
        except Exception:
            logger.exception("get_artifact: read failed for %s", path)
    return {
        "id": meta["id"],
        "name": meta["filename"],
        "kind": meta.get("kind") or meta.get("file_type") or "other",
        "size_bytes": meta.get("size_bytes") or len(content),
        "mime": mime_for(meta.get("kind") or "", meta["filename"]),
        "content_bytes": content,
        "project_slug": meta.get("project_slug"),
        "run_id": meta.get("run_id"),
    }


def _get_meta(artifact_id: str, *, include_thumbnail: bool = False) -> Optional[dict]:
    cols = (
        "id, project_slug, user_id, agent_name, run_id, file_type, kind, "
        "filename, storage_path, size_bytes, metadata, created_at, deleted_at"
    )
    if include_thumbnail:
        cols += ", thumbnail"
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT {cols} FROM dash.dash_generated_files "
                f"WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": artifact_id},
        ).mappings().first()
    return dict(row) if row else None


def get_thumbnail(artifact_id: str) -> Optional[bytes]:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT thumbnail FROM dash.dash_generated_files "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": artifact_id},
        ).first()
    if not row:
        return None
    raw = row[0]
    if raw is None:
        return None
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    return bytes(raw) if not isinstance(raw, bytes) else raw


def list_artifacts(
    project: Optional[str],
    run_id: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Metadata + base64 thumbnail (for images). No raw content."""
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, project_slug, run_id, agent_name, file_type, kind,
                       filename, size_bytes, metadata, created_at,
                       (thumbnail IS NOT NULL) AS has_thumbnail,
                       thumbnail
                  FROM dash.dash_generated_files
                 WHERE deleted_at IS NULL
                   AND (CAST(:ps  AS TEXT) IS NULL OR project_slug = :ps)
                   AND (CAST(:rid AS TEXT) IS NULL OR run_id = :rid)
                   AND (CAST(:kd  AS TEXT) IS NULL OR kind = :kd OR file_type = :kd)
                 ORDER BY created_at DESC
                 LIMIT :lim OFFSET :off
                """
            ),
            {
                "ps": project,
                "rid": run_id,
                "kd": kind,
                "lim": limit,
                "off": offset,
            },
        ).mappings().all()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        thumb = d.pop("thumbnail", None)
        if thumb is not None:
            try:
                if isinstance(thumb, memoryview):
                    thumb = thumb.tobytes()
                d["thumbnail_b64"] = base64.b64encode(bytes(thumb)).decode("ascii")
            except Exception:
                d["thumbnail_b64"] = None
        else:
            d["thumbnail_b64"] = None
        d["kind"] = d.get("kind") or d.get("file_type") or "other"
        if d.get("created_at") is not None:
            d["created_at"] = str(d["created_at"])
        out.append(d)
    return out


def count_artifacts(
    project: Optional[str],
    run_id: Optional[str] = None,
    kind: Optional[str] = None,
) -> int:
    eng = _engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM dash.dash_generated_files
                 WHERE deleted_at IS NULL
                   AND (CAST(:ps  AS TEXT) IS NULL OR project_slug = :ps)
                   AND (CAST(:rid AS TEXT) IS NULL OR run_id = :rid)
                   AND (CAST(:kd  AS TEXT) IS NULL OR kind = :kd OR file_type = :kd)
                """
            ),
            {"ps": project, "rid": run_id, "kd": kind},
        ).first()
    return int(row[0]) if row else 0


def delete_artifact(artifact_id: str, *, hard: bool = False) -> bool:
    """Soft delete by default (sets deleted_at). hard=True removes file too."""
    eng = _engine()
    if hard:
        meta = _get_meta(artifact_id)
        if meta and meta.get("storage_path"):
            try:
                Path(meta["storage_path"]).unlink(missing_ok=True)
            except Exception:
                logger.exception("delete_artifact: unlink failed")
    with eng.begin() as conn:
        res = conn.execute(
            text(
                "UPDATE dash.dash_generated_files SET deleted_at = now() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": artifact_id},
        )
    return (res.rowcount or 0) > 0
