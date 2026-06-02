"""Phase 6 Reasoner + Evals + Secret-leak smoke tests."""
import os
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_reasoner_module_imports():
    from dash.agents import reasoner
    assert hasattr(reasoner, "build_reasoner_agent")


def test_evals_runner_imports():
    from dash.evals import runner
    assert hasattr(runner, "run_suite")
    assert hasattr(runner, "set_baseline")


def test_evals_api_imports():
    from app import evals_api
    assert evals_api.router is not None


# ── Secret leak detector ────────────────────────────────────────────────
def test_scan_finds_openai_key():
    from dash.agentic.secret_leak import scan
    text = "Here is my key: sk-proj-abcdef1234567890abcdefXYZ"
    matches = scan(text)
    assert any(m["pattern"] == "openai_key" for m in matches)


def test_scan_finds_jwt():
    from dash.agentic.secret_leak import scan
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    matches = scan(f"Token: {jwt}")
    assert any(m["pattern"] == "jwt" for m in matches)


def test_scan_finds_aws_key():
    from dash.agentic.secret_leak import scan
    matches = scan("creds: AKIAIOSFODNN7EXAMPLE end")
    assert any(m["pattern"] == "aws_access_key" for m in matches)


def test_scan_finds_db_url():
    from dash.agentic.secret_leak import scan
    matches = scan("connect to postgres://user:supersecret@host:5432/db now")
    assert any(m["pattern"] == "db_url_password" for m in matches)


def test_scan_no_false_positives():
    from dash.agentic.secret_leak import scan
    matches = scan("This is a normal response with no secrets in it whatsoever.")
    assert matches == []


def test_apply_redacts(monkeypatch):
    from dash.agentic import secret_leak
    monkeypatch.setattr(secret_leak, "_get_engine", lambda: None)  # skip audit
    text = "use sk-proj-abcdef1234567890abcdefXYZ to call api"
    result = secret_leak.scan_and_apply(text)
    assert result["action_taken"] == "redacted"
    assert "[REDACTED:openai_key]" in result["text"]
    assert "sk-proj-abcdef" not in result["text"]


def test_apply_blocks_on_jwt(monkeypatch):
    from dash.agentic import secret_leak
    monkeypatch.setattr(secret_leak, "_get_engine", lambda: None)
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    result = secret_leak.scan_and_apply(f"here is {jwt}")
    assert result["action_taken"] == "blocked"
    assert "blocked" in result["text"].lower()


def test_apply_passes_clean_text(monkeypatch):
    from dash.agentic import secret_leak
    monkeypatch.setattr(secret_leak, "_get_engine", lambda: None)
    result = secret_leak.scan_and_apply("hello world, no secrets here")
    assert result["action_taken"] == "none"
    assert result["text"] == "hello world, no secrets here"


# ── Eval judges ─────────────────────────────────────────────────────────
def test_judge_smoke_pass():
    from dash.evals.runner import _judge_smoke
    status, score, reason = _judge_smoke({}, "hello", [], 100)
    assert status == "pass"
    assert score == 1.0


def test_judge_smoke_fail_empty():
    from dash.evals.runner import _judge_smoke
    status, score, _ = _judge_smoke({}, "", [], 100)
    assert status == "fail"
    assert score == 0.0


def test_judge_reliability_pass():
    from dash.evals.runner import _judge_reliability
    status, score, _ = _judge_reliability(
        {"expected_tool_calls": ["run_sql_query"]}, "ans", ["run_sql_query"], 100,
    )
    assert status == "pass"


def test_judge_reliability_missing_tool():
    from dash.evals.runner import _judge_reliability
    status, score, reason = _judge_reliability(
        {"expected_tool_calls": ["run_sql_query"]}, "ans", [], 100,
    )
    assert status == "fail"
    assert "missing" in reason


def test_judge_llm_stub_when_off(monkeypatch):
    from dash.evals.runner import _judge_llm
    monkeypatch.delenv("EXPERIMENTAL_AGI", raising=False)
    status, score, reason = _judge_llm({"input_prompt": "x"}, "y", [], 100)
    assert status == "pass"
    assert "stub" in reason
