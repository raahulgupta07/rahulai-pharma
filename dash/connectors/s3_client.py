"""S3 / S3-compatible object access for the auto-sync pipeline.

Thin wrapper around boto3 so the rest of the app never imports it directly.
Fail-soft: if boto3 isn't installed the helpers raise a clear RuntimeError that
the caller turns into a user-facing "S3 support not installed" message rather
than crashing a worker at import time.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

try:  # boto3 is an optional dependency (added to requirements for S3 sync)
    import boto3  # type: ignore
    from botocore.config import Config as _BotoConfig  # type: ignore
    _BOTO_OK = True
except Exception:  # pragma: no cover - exercised only when boto3 absent
    boto3 = None  # type: ignore
    _BotoConfig = None  # type: ignore
    _BOTO_OK = False


def boto3_available() -> bool:
    return _BOTO_OK


def make_client(region: str, access_key: str, secret_key: str, endpoint_url: str | None = None):
    """Build an S3 client. Raises RuntimeError if boto3 is missing."""
    if not _BOTO_OK:
        raise RuntimeError("S3 support not installed (boto3 missing)")
    cfg = _BotoConfig(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=15, read_timeout=60)
    return boto3.client(
        "s3",
        region_name=region or "us-east-1",
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        endpoint_url=(endpoint_url or None),
        config=cfg,
    )


def list_objects(client, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
    """Return [{key, etag, last_modified, size}] for every object under prefix.

    ETag is normalised (quotes stripped). last_modified is a tz-aware datetime.
    """
    out: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix or ""):
        for obj in page.get("Contents", []) or []:
            key = obj.get("Key", "")
            if not key or key.endswith("/"):
                continue  # skip folder placeholders
            lm = obj.get("LastModified")
            if isinstance(lm, _dt.datetime) and lm.tzinfo is None:
                lm = lm.replace(tzinfo=_dt.timezone.utc)
            out.append({
                "key": key,
                "etag": (obj.get("ETag") or "").strip('"'),
                "last_modified": lm,
                "size": int(obj.get("Size") or 0),
            })
    return out


def download_object(client, bucket: str, key: str, dest_path: str) -> None:
    """Download one object to a local path."""
    client.download_file(bucket, key, dest_path)


def head_bucket(client, bucket: str) -> None:
    """Cheap connectivity / permission check. Raises on failure."""
    client.head_bucket(Bucket=bucket)
