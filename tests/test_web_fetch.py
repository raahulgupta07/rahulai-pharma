"""Tests for dash/tools/web_fetch.py"""
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch
import pytest

# db.session stub
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock()
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock()
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


from dash.tools.web_fetch import fetch, _is_safe_url, _extract_text


class TestSafetyChecks:
    def test_blocks_localhost(self):
        ok, reason = _is_safe_url("http://localhost/api")
        assert not ok

    def test_blocks_private_ip(self):
        ok, _ = _is_safe_url("http://10.0.0.5/")
        assert not ok
        ok, _ = _is_safe_url("http://192.168.1.1/")
        assert not ok

    def test_blocks_file_scheme(self):
        ok, _ = _is_safe_url("file:///etc/passwd")
        assert not ok

    def test_allows_https(self):
        ok, _ = _is_safe_url("https://example.com/page")
        assert ok

    def test_allows_http(self):
        ok, _ = _is_safe_url("http://example.com/")
        assert ok


class TestExtractText:
    def test_html_strips_tags(self):
        html = b"<html><body><h1>Title</h1><p>Hello</p><script>x</script></body></html>"
        result = _extract_text(html, "text/html")
        assert "Title" in result
        assert "Hello" in result
        assert "<script>" not in result

    def test_plain_text_unchanged(self):
        result = _extract_text(b"plain text content", "text/plain")
        assert result == "plain text content"

    def test_caps_at_max(self):
        big = b"x" * 100000
        result = _extract_text(big, "text/plain")
        assert len(result) <= 8192


class TestFetch:
    def test_unsafe_url_returns_error(self):
        result = fetch("http://localhost/", use_cache=False)
        assert result.get("error")
        assert result["text"] == ""

    def test_blocked_scheme_returns_error(self):
        result = fetch("ftp://example.com/", use_cache=False)
        assert result.get("error")

    def test_successful_fetch_returns_text(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.iter_content = lambda chunk_size: [
            b"<html><body><h1>Test</h1></body></html>"
        ]
        with patch("requests.get", return_value=mock_response):
            with patch("dash.tools.web_fetch._cache_get", return_value=None):
                with patch("dash.tools.web_fetch._cache_put"):
                    result = fetch("https://example.com/", use_cache=True)
        assert result["status"] == 200
        assert "Test" in result.get("text", "")

    def test_cache_hit_skips_fetch(self):
        cached = {"url": "https://x.com/", "text": "cached body",
                   "status": 200, "title": "X", "error": None}
        with patch("dash.tools.web_fetch._cache_get", return_value=cached):
            result = fetch("https://x.com/")
        assert result["from_cache"] is True
        assert result["text"] == "cached body"

    def test_request_exception_returns_error(self):
        with patch("dash.tools.web_fetch._cache_get", return_value=None):
            with patch("requests.get", side_effect=RuntimeError("network down")):
                result = fetch("https://x.com/", use_cache=False)
        assert "network down" in result.get("error", "")


class TestMakeTool:
    def test_make_tool_returns_callable(self):
        from dash.tools.web_fetch import make_tool
        t = make_tool()
        # Either Agno @tool wrapper or callable
        assert t is not None
