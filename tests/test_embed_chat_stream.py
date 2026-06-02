"""SSE streaming embed chat endpoint tests.

Tests are unit-style: monkeypatch the heavy collaborators (validate_session,
embed lookup, scope classifier, team build, rate limiter) and exercise the
SSE producer + 400-on-consumer-mode + error path. No DB, no LLM.

Skipped automatically when FastAPI + httpx not importable.
"""
from __future__ import annotations

import json
import sys as _sys
import types as _types

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

try:
    from fastapi.testclient import TestClient  # noqa: F401
    from fastapi import FastAPI
except Exception:  # pragma: no cover
    pytest.skip("fastapi/httpx not installed in test env", allow_module_level=True)


def _make_client(app):
    """Construct a TestClient that works across httpx versions.

    httpx >= 0.28 dropped the `app=` kwarg on `Client.__init__` and starlette
    >= 0.41 calls it positionally. On hosts w/ a mismatch we skip — the
    streaming endpoint is exercised end-to-end via the integration smoke
    (curl in the deploy verify step).
    """
    try:
        return TestClient(app)
    except TypeError as exc:  # httpx/starlette ABI drift
        pytest.skip(f"TestClient incompatible w/ host httpx: {exc}")


# --------------------------------------------------------------------------- #
# Pre-inject stub submodules BEFORE app.embed_public lazy-imports them.
# Some hosts don't have the agno version that exports TeamMode → dash.team
# fails to import. Same for scope_classifier + skill_refinery (transitive
# deps). Stub modules expose the names embed_public.chat/stream pulls.
# --------------------------------------------------------------------------- #

def _ensure_stub(modname: str, **attrs):
    parts = modname.split(".")
    # Make sure parent packages exist as real modules (don't overwrite).
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in _sys.modules:
            m = _types.ModuleType(parent)
            m.__path__ = []  # mark as package
            _sys.modules[parent] = m
    if modname in _sys.modules:
        mod = _sys.modules[modname]
    else:
        mod = _types.ModuleType(modname)
        _sys.modules[modname] = mod
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Replace dash.team module entirely — its real version fails to import on
# hosts missing agno.team.TeamMode. We provide a stub create_project_team
# which each test then overrides via _set().
_team_stub = _types.ModuleType("dash.team")
_team_stub.create_project_team = lambda *a, **k: None
_sys.modules["dash.team"] = _team_stub


class _NoRefusal:
    refused = False
    refusal_message = None


_sc_stub = _types.ModuleType("dash.scope_classifier")
_sc_stub.classify_question = lambda *a, **k: _NoRefusal()
_sc_stub.log_refusal = lambda *a, **k: None
_sys.modules["dash.scope_classifier"] = _sc_stub

# dash.tools.skill_refinery exists; ensure set_request_context attr is set
# so the import inside embed_public doesn't fail (the real module may have
# heavier imports). We don't override if it's already there.
try:
    import dash.tools.skill_refinery  # noqa: F401
except Exception:
    _ensure_stub("dash.tools.skill_refinery", set_request_context=lambda **k: None)


# --------------------------------------------------------------------------- #
# Fake events + team that yields a few tokens.
# --------------------------------------------------------------------------- #


class _FakeContentEvent:
    """Mimics agno TeamRunContent: has .event + .content + .to_dict()."""

    def __init__(self, content: str):
        self.event = "TeamRunContent"
        self.content = content

    def to_dict(self):
        return {"event": self.event, "content": self.content}


class _FakeTeam:
    def __init__(self, tokens):
        self._tokens = tokens

    def run(self, _msg, **_kwargs):  # mirrors team.run(stream=True, stream_events=True)
        for t in self._tokens:
            yield _FakeContentEvent(t)


def _make_app(monkeypatch, *, response_style="analyst", tokens=None,
              raise_in_run=False, embed_enabled=True):
    """Build a FastAPI app w/ the streaming route + all collaborators stubbed."""
    from app import embed_public

    def _set(target, val):
        monkeypatch.setattr(target, val, raising=False)

    # validate_session always returns a session for any non-empty token.
    _set("dash.embed.session.validate_session",
         lambda token: {
             "embed_id": "emb_test",
             "external_user": None,
             "user_attrs": {},
         } if token else None)

    # Embed DB row + allowed_origins lookup come back from a fake engine.
    embed_row = [
        "test_proj",                  # project_slug
        30,                            # rate_limit_per_min
        {},                            # feature_config
        embed_enabled,                 # enabled
        1,                             # id
        None,                          # bound_scope_id
        "public",                      # bound_intent
        None,                          # bound_role
        response_style,                # response_style
        600,                           # max_reply_chars
    ]

    class _Result:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _FakeConn:
        def __init__(self):
            self._call_n = 0

        def execute(self, _stmt, _params=None):
            self._call_n += 1
            if self._call_n == 1:
                return _Result(embed_row)
            # allowed_origins lookup → one-tuple containing empty list.
            return _Result([[]])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

        def begin(self):
            return _FakeConn()

    _set("dash.embed._get_engine", lambda: _FakeEngine())

    # Scope classifier never refuses (stub at module level already does this
    # but reset per-test in case a prior test changed it).
    _sc_stub.classify_question = lambda *a, **k: _NoRefusal()
    _sc_stub.log_refusal = lambda *a, **k: None

    # Team builder per-test.
    def _build_team(*_a, **_kw):
        if raise_in_run:
            class _BoomTeam:
                def run(self, *a, **k):
                    raise RuntimeError("agent exploded")
            return _BoomTeam()
        return _FakeTeam(tokens or ["Hello ", "world!"])

    _team_stub.create_project_team = _build_team

    # No-op skill_refinery context setter.
    try:
        import dash.tools.skill_refinery as _sr
        _sr.set_request_context = lambda **k: None
    except Exception:
        pass

    # Rate limit always allows.
    _set("app.embed_public._rate_limit_check", lambda *a, **k: True)

    app = FastAPI()
    app.include_router(embed_public.router)
    return app


def _read_sse_events(resp):
    """Parse a streaming response into list of {event, data_obj} dicts."""
    events = []
    raw = resp.text
    for frame in raw.split("\n\n"):
        if not frame.strip() or frame.lstrip().startswith(":"):
            continue
        ev_type = "message"
        data_str = ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev_type = line[6:].strip()
            elif line.startswith("data:"):
                data_str += line[5:].strip()
        if not data_str:
            continue
        try:
            data_obj = json.loads(data_str)
        except Exception:
            data_obj = {"_raw": data_str}
        events.append({"event": ev_type, "data": data_obj})
    return events


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_stream_emits_meta_first(monkeypatch):
    app = _make_app(monkeypatch, tokens=["A ", "B"])
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    assert r.status_code == 200, r.text
    events = _read_sse_events(r)
    assert events, "no SSE events received"
    assert events[0]["event"] == "meta"
    assert events[0]["data"].get("embed_id") == "emb_test"
    assert events[0]["data"].get("session_token") == "sess_x"


def test_stream_emits_tokens(monkeypatch):
    app = _make_app(monkeypatch, tokens=["Hello ", "world!"])
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    events = _read_sse_events(r)
    tokens = [e for e in events if e["event"] == "token"]
    assert len(tokens) >= 1
    assert any(t["data"].get("delta") for t in tokens)
    assembled = "".join(t["data"]["delta"] for t in tokens)
    assert "Hello" in assembled and "world" in assembled


def test_stream_emits_done_last(monkeypatch):
    app = _make_app(monkeypatch, tokens=["x"])
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    events = _read_sse_events(r)
    assert events[-1]["event"] == "done"
    assert "latency_ms" in events[-1]["data"]
    assert isinstance(events[-1]["data"]["latency_ms"], int)


def test_stream_consumer_mode_returns_400(monkeypatch):
    app = _make_app(monkeypatch, response_style="consumer", tokens=["x"])
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    assert r.status_code == 400
    assert "consumer" in r.text.lower() or "stream" in r.text.lower()


def test_stream_error_yields_error_event(monkeypatch):
    app = _make_app(monkeypatch, raise_in_run=True)
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    assert r.status_code == 200, r.text
    events = _read_sse_events(r)
    err = [e for e in events if e["event"] == "error"]
    assert err, f"no error event; got: {[e['event'] for e in events]}"
    assert "exploded" in err[0]["data"].get("detail", "")
    # No done event after error.
    assert not any(e["event"] == "done" for e in events)


def test_stream_rejects_missing_session_token(monkeypatch):
    app = _make_app(monkeypatch, tokens=["x"])
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "message": "hi"},
    )
    assert r.status_code == 400


def test_stream_rejects_disabled_embed(monkeypatch):
    app = _make_app(monkeypatch, tokens=["x"], embed_enabled=False)
    client = _make_client(app)
    r = client.post(
        "/api/embed/chat/stream",
        json={"embed_id": "emb_test", "session_token": "sess_x", "message": "hi"},
    )
    assert r.status_code == 403
