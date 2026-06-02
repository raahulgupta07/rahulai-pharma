"""Tests for dash.providers.xmla_pull and the xmla_pull_step trainer hook."""
from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# Stub `db.session` before any import chain reaches it. The real module uses
# Python 3.10+ union syntax in annotations and would fail to import on 3.9
# test runners; tests don't need a real engine anyway.
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub


from dash.providers import xmla_pull
from dash.providers.xmla_pull import (
    XMLAMeasure,
    XMLAResult,
    _get_aad_token,
    _parse_measures_xml,
    _parse_relationships_xml,
    import_to_dash,
    pull_semantic_model,
)


# ---------------------------------------------------------------------------
# _get_aad_token
# ---------------------------------------------------------------------------


def test_get_aad_token_returns_none_when_env_missing(monkeypatch):
    monkeypatch.delenv("MS_CLIENT_ID", raising=False)
    monkeypatch.delenv("MS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MS_TENANT_ID", raising=False)
    # Even if msal imports fine, missing env vars must short-circuit.
    fake_msal = MagicMock()
    with patch.dict("sys.modules", {"msal": fake_msal}):
        assert _get_aad_token() is None
    fake_msal.ConfidentialClientApplication.assert_not_called()


def test_get_aad_token_returns_token_on_success(monkeypatch):
    monkeypatch.setenv("MS_CLIENT_ID", "cid")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MS_TENANT_ID", "tid")

    fake_msal = MagicMock()
    fake_app = MagicMock()
    fake_app.acquire_token_for_client.return_value = {"access_token": "abc"}
    fake_msal.ConfidentialClientApplication.return_value = fake_app

    with patch.dict("sys.modules", {"msal": fake_msal}):
        token = _get_aad_token()
    assert token == "abc"


def test_get_aad_token_returns_none_on_error_payload(monkeypatch):
    monkeypatch.setenv("MS_CLIENT_ID", "cid")
    monkeypatch.setenv("MS_CLIENT_SECRET", "secret")

    fake_msal = MagicMock()
    fake_app = MagicMock()
    fake_app.acquire_token_for_client.return_value = {
        "error": "invalid_client",
        "error_description": "bad creds",
    }
    fake_msal.ConfidentialClientApplication.return_value = fake_app

    with patch.dict("sys.modules", {"msal": fake_msal}):
        assert _get_aad_token() is None


# ---------------------------------------------------------------------------
# _parse_measures_xml / _parse_relationships_xml
# ---------------------------------------------------------------------------


SAMPLE_MEASURES_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-microsoft-com:xml-analysis:rowset">
  <row>
    <MEASURE_NAME>Total Sales</MEASURE_NAME>
    <MEASUREGROUP_NAME>Sales</MEASUREGROUP_NAME>
    <EXPRESSION>SUM(Sales[Amount])</EXPRESSION>
    <DESCRIPTION>Sum of sales amount</DESCRIPTION>
    <DEFAULT_FORMAT_STRING>$#,##0</DEFAULT_FORMAT_STRING>
    <MEASURE_DISPLAY_FOLDER>KPIs</MEASURE_DISPLAY_FOLDER>
  </row>
  <row>
    <MEASURE_NAME>Avg Price</MEASURE_NAME>
    <MEASUREGROUP_NAME>Sales</MEASUREGROUP_NAME>
    <EXPRESSION>AVERAGE(Sales[Price])</EXPRESSION>
    <DESCRIPTION>Average unit price</DESCRIPTION>
  </row>
</root>"""


def test_parse_measures_xml_extracts_two_measures():
    measures = _parse_measures_xml(SAMPLE_MEASURES_XML)
    assert len(measures) == 2
    assert measures[0].name == "Total Sales"
    assert measures[0].table == "Sales"
    assert "SUM(Sales[Amount])" in measures[0].expression
    assert measures[0].description == "Sum of sales amount"
    assert measures[0].format_string == "$#,##0"
    assert measures[0].folder == "KPIs"
    assert measures[1].name == "Avg Price"


def test_parse_measures_xml_returns_empty_on_garbage():
    assert _parse_measures_xml("<not valid xml") == []


SAMPLE_RELATIONSHIPS_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-microsoft-com:xml-analysis:rowset">
  <row>
    <FromTableName>Sales</FromTableName>
    <FromColumnName>CustomerID</FromColumnName>
    <ToTableName>Customer</ToTableName>
    <ToColumnName>ID</ToColumnName>
    <FromCardinality>Many</FromCardinality>
  </row>
</root>"""


def test_parse_relationships_xml_extracts_one():
    rels = _parse_relationships_xml(SAMPLE_RELATIONSHIPS_XML)
    assert len(rels) == 1
    r = rels[0]
    assert r.from_table == "Sales"
    assert r.from_col == "CustomerID"
    assert r.to_table == "Customer"
    assert r.to_col == "ID"
    assert r.cardinality == "Many"


# ---------------------------------------------------------------------------
# pull_semantic_model
# ---------------------------------------------------------------------------


def test_pull_semantic_model_error_when_no_token():
    with patch.object(xmla_pull, "_get_aad_token", return_value=None):
        result = pull_semantic_model("ws", "ds")
    assert isinstance(result, XMLAResult)
    assert result.error == "AAD token unavailable"
    assert result.measures == []
    assert result.relationships == []


def test_pull_semantic_model_populates_measures():
    with patch.object(xmla_pull, "_get_aad_token", return_value="t"), \
         patch.object(xmla_pull, "_xmla_soap_query") as fake_q:
        fake_q.side_effect = [SAMPLE_MEASURES_XML, SAMPLE_RELATIONSHIPS_XML]
        result = pull_semantic_model("ws", "ds")
    assert result.error is None
    assert len(result.measures) == 2
    assert len(result.relationships) == 1


def test_pull_semantic_model_error_when_nothing_returned():
    with patch.object(xmla_pull, "_get_aad_token", return_value="t"), \
         patch.object(xmla_pull, "_xmla_soap_query", return_value=None):
        result = pull_semantic_model("ws", "ds")
    assert result.error is not None
    assert "no measures" in result.error


# ---------------------------------------------------------------------------
# import_to_dash
# ---------------------------------------------------------------------------


def test_import_to_dash_skips_when_error_set():
    res = XMLAResult(workspace="w", dataset="d", error="boom")
    out = import_to_dash(res, "slug", 1)
    assert out["measures_inserted"] == 0
    assert out["brain_entries_inserted"] == 0
    assert "boom" in out["errors"][0]


def test_import_to_dash_inserts_measures_with_mocked_engine():
    res = XMLAResult(
        workspace="w",
        dataset="d",
        measures=[
            XMLAMeasure(
                name="Total Sales", table="Sales",
                expression="SUM(Sales[Amount])", description="d1",
            ),
            XMLAMeasure(
                name="Avg Price", table="Sales",
                expression="AVG(...)", description=None,
            ),
        ],
    )

    fake_engine = MagicMock()
    fake_conn = MagicMock()
    fake_engine.connect.return_value.__enter__.return_value = fake_conn
    fake_engine.connect.return_value.__exit__.return_value = False

    with patch("db.session.get_sql_engine", return_value=fake_engine):
        out = import_to_dash(res, "slug", 7)

    # 2 measures × 2 inserts (rules + brain) = 4 execute calls.
    assert fake_conn.execute.call_count == 4
    assert out["measures_inserted"] == 2
    assert out["brain_entries_inserted"] == 2
    assert out["errors"] == []
    fake_conn.commit.assert_called_once()


def test_import_to_dash_records_engine_failure():
    res = XMLAResult(
        workspace="w",
        dataset="d",
        measures=[XMLAMeasure(name="m", table="t", expression="1")],
    )
    with patch("db.session.get_sql_engine",
               side_effect=RuntimeError("no db")):
        out = import_to_dash(res, "slug", 1)
    assert out["measures_inserted"] == 0
    assert any("no db" in e for e in out["errors"])


# ---------------------------------------------------------------------------
# xmla_pull_step (trainer hook)
# ---------------------------------------------------------------------------


def _drain(agen):
    async def _run():
        out = []
        async for ev in agen:
            out.append(ev)
        return out
    return asyncio.get_event_loop().run_until_complete(_run()) \
        if False else asyncio.run(_run())


def test_xmla_pull_step_skips_without_workspace_or_dataset():
    from dash.providers.training_steps_v2 import xmla_pull_step

    provider = MagicMock()
    provider.config = {}
    provider.project_slug = "slug"

    events = _drain(xmla_pull_step(provider, source_id=1))
    assert len(events) == 1
    assert events[0].step == "xmla_pull"
    assert events[0].status == "done"
    assert "skipped" in events[0].message


def test_xmla_pull_step_runs_full_path_when_configured():
    from dash.providers import training_steps_v2

    provider = MagicMock()
    provider.config = {"workspace": "ws", "dataset": "ds"}
    provider.project_slug = "slug"

    fake_result = XMLAResult(
        workspace="ws", dataset="ds",
        measures=[XMLAMeasure(name="m", table="t", expression="1")],
    )

    with patch.object(
        training_steps_v2,
        "xmla_pull_step",
        wraps=training_steps_v2.xmla_pull_step,
    ):
        with patch(
            "dash.providers.xmla_pull.pull_semantic_model",
            return_value=fake_result,
        ), patch(
            "dash.providers.xmla_pull.import_to_dash",
            return_value={
                "measures_inserted": 1,
                "brain_entries_inserted": 1,
                "errors": [],
            },
        ):
            events = _drain(
                training_steps_v2.xmla_pull_step(provider, source_id=2)
            )

    statuses = [e.status for e in events]
    assert "start" in statuses
    assert events[-1].status == "done"
    assert "measures=1" in events[-1].message


def test_xmla_pull_step_emits_error_when_pull_returns_error():
    from dash.providers import training_steps_v2

    provider = MagicMock()
    provider.config = {"workspace": "ws", "dataset": "ds"}
    provider.project_slug = "slug"

    fake_result = XMLAResult(workspace="ws", dataset="ds", error="auth fail")

    with patch(
        "dash.providers.xmla_pull.pull_semantic_model",
        return_value=fake_result,
    ):
        events = _drain(
            training_steps_v2.xmla_pull_step(provider, source_id=2)
        )

    assert events[-1].status == "error"
    assert "auth fail" in events[-1].message


def test_xmla_pull_registered_as_optional_step():
    from dash.providers.training_steps_v2 import EnhancedProviderTrainer

    names = [s[0] for s in EnhancedProviderTrainer.OPTIONAL_STEPS]
    assert "xmla_pull" in names
    # Default-off so existing tests/training runs are untouched.
    entry = next(
        s for s in EnhancedProviderTrainer.OPTIONAL_STEPS if s[0] == "xmla_pull"
    )
    assert entry[2] is False  # default_on
    assert entry[3] == "disable_xmla"
