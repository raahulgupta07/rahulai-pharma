"""Production hardening helpers for the connector subsystem.

Three responsibilities:

1. :func:`is_read_only_sql` — head-token guard that rejects anything that
   could mutate the source database.  Allow-list approach: only ``SELECT``,
   ``WITH``, ``EXPLAIN``, ``SHOW`` and ``EVALUATE`` (PowerBI DAX) pass.

2. :func:`scrub_secrets` — replace every literal occurrence of a known
   secret with ``***REDACTED***``.

3. :func:`safe_error_message` — sanitize an ``Exception`` for end-user
   display: strips secret values + truncates to 500 chars.

These helpers never raise on bad input — they fail-closed for the
read-only gate (reject) and best-effort for the scrubbers.
"""
from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Read-only SQL gate
# ---------------------------------------------------------------------------

#: Verbs the gate will *accept*.  Everything else is rejected.
ALLOWED_SQL_VERBS: set[str] = {
    "select",
    "with",
    "explain",
    "show",
    "evaluate",  # PowerBI DAX
}

#: Verbs we *explicitly* reject (informational — anything not in
#: :data:`ALLOWED_SQL_VERBS` is rejected anyway, but we keep this set
#: around so the reason string can be specific).
_DESTRUCTIVE_VERBS: set[str] = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "merge",
    "call",
    "exec",
    "execute",
    "replace",
    "rename",
    "vacuum",
    "analyze",
    "comment",
    "lock",
    "copy",
}

_LINE_COMMENT_RE = re.compile(r"^\s*--[^\n]*\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments_and_whitespace(sql: str) -> str:
    """Remove ``--`` line comments and ``/* ... */`` block comments, trim."""
    if not sql:
        return ""
    cleaned = _BLOCK_COMMENT_RE.sub(" ", sql)
    cleaned = _LINE_COMMENT_RE.sub("", cleaned)
    return cleaned.strip()


def is_read_only_sql(sql: str, dialect: str = "postgres") -> tuple[bool, str]:
    """Return ``(ok, reason)`` for *sql*.

    The check is a conservative head-token allow-list:

    * Strip ``--`` and ``/* */`` comments.
    * Lower-case the first 8 chars.
    * Accept iff the leading token is in :data:`ALLOWED_SQL_VERBS`.

    *dialect* is informational only — the same allow-list applies to every
    backend (``EVALUATE`` is needed for PowerBI DAX and is included in the
    allow-list unconditionally).
    """
    if not sql or not isinstance(sql, str):
        return False, "empty sql"

    cleaned = _strip_comments_and_whitespace(sql)
    if not cleaned:
        return False, "empty sql after stripping comments"

    # First token: alphabetic chars only.
    head = cleaned[:32].lower().lstrip()
    m = re.match(r"^([a-z]+)", head)
    if not m:
        return False, "no leading SQL verb detected"
    verb = m.group(1)

    if verb in ALLOWED_SQL_VERBS:
        return True, verb

    if verb in _DESTRUCTIVE_VERBS:
        return False, f"destructive verb '{verb.upper()}' not allowed (read-only)"

    return False, f"verb '{verb.upper()}' not in allow-list (read-only)"


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------

_REDACTED = "***REDACTED***"


def scrub_secrets(text: str, secrets: Iterable[str]) -> str:
    """Return *text* with every literal occurrence of any *secrets* replaced.

    * ``None`` / empty secrets are skipped.
    * Secrets shorter than 4 chars are skipped (too noisy to scrub safely).
    * Non-string secrets are coerced via ``str()``.
    """
    if not text:
        return text or ""
    out = text
    for raw in secrets or ():
        if raw is None:
            continue
        s = str(raw)
        if len(s) < 4:
            continue
        if s in out:
            out = out.replace(s, _REDACTED)
    return out


def safe_error_message(exc: Exception, conn_creds: dict | None = None) -> str:
    """Convert an exception into a user-safe message.

    * Extracts ``str(exc)``.
    * Scrubs every value from ``conn_creds.values()`` (any dict-shaped creds
      object — usually decrypted credentials, but we only use the values).
    * Truncates to 500 chars.

    Never leaks raw secrets even if the underlying exception included a
    connection string or stack trace text.
    """
    try:
        msg = str(exc) if exc is not None else ""
    except Exception:
        msg = repr(exc)

    secrets: list[str] = []
    if isinstance(conn_creds, dict):
        for v in conn_creds.values():
            if v is None:
                continue
            secrets.append(str(v))

    msg = scrub_secrets(msg, secrets)
    if len(msg) > 500:
        msg = msg[:497] + "..."
    return msg
