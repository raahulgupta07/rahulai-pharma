"""Phase 5 Comm surface smoke tests."""
import hashlib
import hmac
import sys
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_slack_signature_verify_ok():
    from dash.channels.slack import verify_signature
    secret = "test_secret"
    ts = str(int(time.time()))
    body = b'{"foo":"bar"}'
    base = f"v0:{ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert verify_signature(secret, ts, body, sig)


def test_slack_signature_verify_bad():
    from dash.channels.slack import verify_signature
    assert not verify_signature("secret", "1", b"body", "v0=deadbeef")


def test_slack_signature_verify_old_timestamp():
    from dash.channels.slack import verify_signature
    secret = "s"
    old_ts = str(int(time.time()) - 600)  # 10 min ago
    body = b"x"
    base = f"v0:{old_ts}:{body.decode()}"
    sig = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    assert not verify_signature(secret, old_ts, body, sig)


def test_slack_strip_mention():
    from dash.channels.slack import _strip_mention
    assert _strip_mention("<@U12345> hello world") == "hello world"
    assert _strip_mention("plain text") == "plain text"


def test_slack_url_verification_handled():
    from dash.channels.slack import handle_event
    out = handle_event({"type": "url_verification", "challenge": "abc123"})
    assert out["challenge"] == "abc123"


def test_slack_ignores_bot_messages():
    from dash.channels.slack import handle_event
    out = handle_event({
        "team_id": "T1",
        "event": {"type": "message", "bot_id": "B1", "text": "hi"},
    })
    assert out["action"] == "ignored"
    assert out["reason"] == "bot_message"


def test_email_extract_project_slug_default():
    from dash.channels.email import _extract_project_slug
    assert _extract_project_slug("[my-proj] Hello", None, None) == "my-proj"
    assert _extract_project_slug("[abc_123] Subject", None, None) == "abc_123"
    assert _extract_project_slug("No prefix", None, "fallback") == "fallback"
    assert _extract_project_slug("No prefix", None, None) is None


def test_email_extract_project_slug_custom_pattern():
    from dash.channels.email import _extract_project_slug
    assert _extract_project_slug("PRJ-mything | Subject", r"^PRJ-([a-z]+)", None) == "mything"


def test_email_strip_quoted():
    from dash.channels.email import _strip_quoted
    body = "Reply here\n\nOn Mon, Jan 1 2026, X wrote:\n> quoted"
    assert _strip_quoted(body) == "Reply here"
    body2 = "Hello\n> previous"
    assert _strip_quoted(body2) == "Hello"


def test_voice_inbound_unknown_number(monkeypatch):
    from dash.channels.voice import handle_inbound_call
    monkeypatch.setattr("dash.channels.voice._lookup_number", lambda n: None)
    twiml = handle_inbound_call({"To": "+15550000000"})
    assert "Number not configured" in twiml
    assert "<Response>" in twiml


def test_voice_inbound_known_number(monkeypatch):
    from dash.channels.voice import handle_inbound_call
    monkeypatch.setattr(
        "dash.channels.voice._lookup_number",
        lambda n: {"id": "vn_x", "default_project_slug": "my-proj"},
    )
    twiml = handle_inbound_call({"To": "+15551112222"})
    assert "my-proj" in twiml
    assert "<Gather" in twiml


def test_voice_speech_no_transcript(monkeypatch):
    from dash.channels.voice import handle_speech_result
    monkeypatch.setattr(
        "dash.channels.voice._lookup_number",
        lambda n: {"id": "vn_x", "default_project_slug": "p"},
    )
    twiml = handle_speech_result({"To": "+1", "CallSid": "CA1", "SpeechResult": ""})
    assert "did not catch" in twiml


def test_voice_xml_escape():
    from dash.channels.voice import _xml_escape
    assert _xml_escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_channels_api_imports():
    from app import channels_api
    assert channels_api.router is not None


def test_email_poller_disabled_paths(monkeypatch):
    from dash.channels import email
    monkeypatch.setenv("EMAIL_POLLER_DISABLED", "1")
    assert email._disabled() is True
    monkeypatch.delenv("EMAIL_POLLER_DISABLED")
    monkeypatch.setenv("DAEMONS_DISABLED", "1")
    assert email._disabled() is True
