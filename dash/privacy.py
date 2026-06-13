"""Central privacy policy — dashboards never show raw chat text.

Manager rule: any admin / analytics / curation surface shows keyword or aggregate
analysis only, never the actual question or answer. Chat text is HARD-REMOVED
server-side by default; flip PRIVACY_SHOW_CHAT=1 only for a deliberate, audited
reveal build. Every admin endpoint that selects question/answer/content text should
pass it through redact() before returning it.

The single sanctioned exception is the audited per-row reveal used by the 👎
train-review flow (app/usage_api.py:/feedback/{fid}/reveal), which logs each reveal
to dash_audit_log.
"""
from __future__ import annotations

import os


def privacy_on() -> bool:
    return os.getenv("PRIVACY_SHOW_CHAT", "0").lower() not in ("1", "true", "yes", "on")


def redact(value):
    """Return None when privacy is on, else the value unchanged."""
    return None if privacy_on() else value


import re as _re

_STOP = set((
    "the a an and or but if of to in on for is are was how what which who this that "
    "with at by from as can could would should will may have has please show tell give "
    "want need get find list about all any some no not more me my our your you we they "
    "it do does at week month last this"
).split())
_TOK = _re.compile(r"[A-Za-z][A-Za-z0-9\-]+|[က-႟]+")


def keywords(text: str | None, n: int = 6) -> list[str]:
    """Top distinct keyword tokens from a question — the privacy-safe stand-in for
    the raw text. Always safe to return (no raw phrases)."""
    out, seen = [], set()
    for t in _TOK.findall(text or ""):
        low = t.lower()
        if len(low) <= 2 or low in _STOP or low in seen:
            continue
        seen.add(low)
        out.append(low)
        if len(out) >= n:
            break
    return out
