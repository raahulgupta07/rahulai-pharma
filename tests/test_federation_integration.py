"""Federation end-to-end integration tests.

Mocks providers w/ pandas DataFrames + fake registry. Validates
the full pipeline: parse -> resolve -> split -> execute -> merge.

Tests intra-project only (cross-agent leaks are NEVER allowed by design).
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Stub db.session before any subsystem import
# ---------------------------------------------------------------------------
if "db.session" not in sys.modules:
    _stub = ModuleType("db.session")
    _stub.get_sql_engine = MagicMock(name="get_sql_engine_stub")
    _db_pkg = sys.modules.setdefault("db", ModuleType("db"))
    _db_pkg.session = _stub
    sys.modules["db.session"] = _stub
    _url_stub = ModuleType("db.url")
    _url_stub.db_url = MagicMock(return_value="postgresql://stub")
    _db_pkg.url = _url_stub
    sys.modules["db.url"] = _url_stub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_mock_provider(
    provider_id: str,
    dialect: str = "postgresql",
    tables: list = None,
    agent_scope: str = "project",
    degraded: bool = False,
    schema_cols: dict = None,
):
    """Build a provider stub w/ schema_blob + dialect."""
    p = SimpleNamespace()
    p.id = provider_id
    p.dialect = dialect
    p.agent_scope = agent_scope
    p.project_slug = "test_proj"
    p.degraded = degraded
    p.last_error = None
    p.schema_blob = {
        "tables": tables or [],
        "columns": schema_cols or {},
        "fks": [],
    }
    p.engine_ro = MagicMock()
    p.engine_rw = MagicMock()
    return p


def _mock_registry(providers: list):
    """Patch get_registry to return list of providers."""
    fake_reg = MagicMock()
    fake_reg.list_for_project.return_value = providers
    return fake_reg


# ---------------------------------------------------------------------------
# 1. Parser + Resolver chain
# ---------------------------------------------------------------------------
class TestParserResolver:
    def test_single_source_not_federated(self):
        from dash.providers.federation.parser import parse
        result = parse("SELECT * FROM only_src.table1")
        assert not result.is_federated

    def test_two_sources_federated(self):
        from dash.providers.federation.parser import parse
        result = parse(
            "SELECT * FROM src_a.t1 a JOIN src_b.t2 b ON a.id = b.id"
        )
        if result.error == "sqlglot not installed":
            pytest.skip("sqlglot required")
        assert result.is_federated
        assert "src_a" in result.provider_ids
        assert "src_b" in result.provider_ids

    def test_resolver_unknown_source_rejects(self):
        from dash.providers.federation.resolver import resolve
        fake_reg = _mock_registry([])
        with patch("dash.providers.get_registry", return_value=fake_reg):
            result = resolve(["fake_src"], "test_proj")
        assert not result.all_accessible
        assert any(("project" in e.lower() or "unknown" in e.lower()) for e in result.errors)

    def test_resolver_blocks_wrong_scope(self):
        p = _make_mock_provider("hidden_src", agent_scope="researcher_only")
        fake_reg = _mock_registry([p])
        from dash.providers.federation.resolver import resolve
        with patch("dash.providers.get_registry", return_value=fake_reg):
            result = resolve(
                ["hidden_src"],
                "test_proj",
                requesting_agent_scope="analyst",
            )
        assert not result.all_accessible

    def test_resolver_blocks_degraded(self):
        p = _make_mock_provider("dead_src", degraded=True)
        p.last_error = "connection lost"
        fake_reg = _mock_registry([p])
        from dash.providers.federation.resolver import resolve
        with patch("dash.providers.get_registry", return_value=fake_reg):
            result = resolve(["dead_src"], "test_proj")
        assert not result.all_accessible


# ---------------------------------------------------------------------------
# 2. Splitter
# ---------------------------------------------------------------------------
class TestSplitter:
    def test_split_two_source_join(self):
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            pytest.skip("sqlglot required")
        from dash.providers.federation.parser import parse
        from dash.providers.federation.splitter import split
        parsed = parse(
            "SELECT a.x, b.y FROM src_a.t1 a JOIN src_b.t2 b "
            "ON a.id = b.id WHERE a.y > 10"
        )
        plan = split(parsed)
        assert plan.error is None
        assert len(plan.subqueries) == 2
        assert len(plan.join_keys) == 1

    def test_split_pushdown_filter(self):
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            pytest.skip("sqlglot required")
        from dash.providers.federation.parser import parse
        from dash.providers.federation.splitter import split
        parsed = parse(
            "SELECT a.x FROM src_a.t1 a JOIN src_b.t2 b ON a.id = b.id "
            "WHERE a.region = 'NA' AND b.qty > 100"
        )
        plan = split(parsed)
        sql_a = next(
            sq.sql for sq in plan.subqueries if sq.provider_id == "src_a"
        )
        sql_b = next(
            sq.sql for sq in plan.subqueries if sq.provider_id == "src_b"
        )
        assert "region" in sql_a.lower()
        assert "qty" in sql_b.lower()


# ---------------------------------------------------------------------------
# 3. Executor + Merge end-to-end
# ---------------------------------------------------------------------------
class TestExecutorMerge:
    @pytest.mark.asyncio
    async def test_executor_two_sources_with_dataframes(self):
        try:
            import pandas as pd
        except ImportError:
            pytest.skip("pandas required")

        from dash.providers.federation.executor import execute_split_plan

        df_a = pd.DataFrame({"id": [1, 2, 3], "x": [10, 20, 30]})
        df_b = pd.DataFrame({"id": [1, 2, 3], "y": [100, 200, 300]})

        p_a = _make_mock_provider("src_a")
        p_b = _make_mock_provider("src_b")
        fake_reg = _mock_registry([p_a, p_b])

        plan = SimpleNamespace()
        plan.subqueries = [
            SimpleNamespace(
                provider_id="src_a",
                sql="SELECT * FROM t1",
                columns_needed=["id", "x"],
            ),
            SimpleNamespace(
                provider_id="src_b",
                sql="SELECT * FROM t2",
                columns_needed=["id", "y"],
            ),
        ]

        def fake_read_sql(sql, conn):
            sql_str = str(sql)
            if "t1" in sql_str:
                return df_a
            return df_b

        with patch("dash.providers.get_registry", return_value=fake_reg):
            with patch("pandas.read_sql", side_effect=fake_read_sql):
                result = await execute_split_plan(
                    plan, project_slug="test_proj"
                )

        assert "src_a" in result.per_source
        assert "src_b" in result.per_source
        assert len(result.per_source["src_a"]) == 3

    def test_pandas_merge_two_dfs(self):
        try:
            import pandas as pd
        except ImportError:
            pytest.skip("pandas required")

        from dash.providers.federation.merge_pandas import merge as pd_merge

        df_a = pd.DataFrame({"id": [1, 2], "x": [10, 20]})
        df_b = pd.DataFrame({"id": [1, 2], "y": [100, 200]})

        plan = SimpleNamespace()
        plan.join_keys = [
            SimpleNamespace(
                left_provider="src_a",
                left_column="id",
                right_provider="src_b",
                right_column="id",
                op="=",
            )
        ]
        plan.final_select = "*"
        plan.final_where = ""
        plan.final_order_by = ""
        plan.final_limit = None

        exec_result = SimpleNamespace(
            per_source={"src_a": df_a, "src_b": df_b},
        )

        result = pd_merge(plan, exec_result)
        assert result.error is None
        assert result.row_count == 2


# ---------------------------------------------------------------------------
# 4. Tool integration
# ---------------------------------------------------------------------------
class TestFederatedTool:
    def test_unknown_source_returns_resolve_error(self):
        from dash.tools.federated_query import _run_federated_sync
        with patch(
            "dash.providers.get_registry", return_value=_mock_registry([])
        ):
            with patch(
                "dash.admin.settings.get_setting", return_value=True
            ):
                result = _run_federated_sync(
                    "SELECT * FROM unknown.t1 a "
                    "JOIN other.t2 b ON a.id = b.id",
                    "test_proj",
                    "analyst",
                    user_id=1,
                )
        assert "RESOLVE" in result.upper() or "ERROR" in result.upper()

    def test_disabled_via_admin_setting(self):
        try:
            import sqlglot  # noqa
        except ImportError:
            pytest.skip("sqlglot required")
        from dash.tools.federated_query import _run_federated_sync
        with patch("dash.admin.settings.get_setting") as mock_get:
            def setting(key, project_slug=None):
                if key == "enable_federation_join":
                    return False
                return None
            mock_get.side_effect = setting
            result = _run_federated_sync(
                "SELECT * FROM a.t1 x JOIN b.t2 y ON x.id = y.id",
                "test_proj",
                "analyst",
                user_id=1,
            )
        assert "DISABLED" in result.upper()

    def test_single_source_routes_back(self):
        try:
            import sqlglot  # noqa
        except ImportError:
            pytest.skip("sqlglot required")
        from dash.tools.federated_query import _run_federated_sync
        result = _run_federated_sync(
            "SELECT * FROM only_src.t1",
            "test_proj",
            "analyst",
            user_id=1,
        )
        assert (
            "NOT FEDERATED" in result.upper() or "single" in result.lower()
        )


# ---------------------------------------------------------------------------
# 5. Cross-agent isolation (CRITICAL - never read other projects)
# ---------------------------------------------------------------------------
class TestCrossAgentIsolation:
    def test_project_a_cannot_see_project_b_sources(self):
        """Sources registered under project B must not appear when querying for project A."""
        from dash.providers.federation.resolver import resolve

        p_x = _make_mock_provider("src_x")
        p_x.project_slug = "project_a"
        p_y = _make_mock_provider("src_y")
        p_y.project_slug = "project_b"

        def list_for_project(slug, *args, **kwargs):
            if slug == "project_a":
                return [p_x]
            elif slug == "project_b":
                return [p_y]
            return []

        fake_reg = MagicMock()
        fake_reg.list_for_project.side_effect = list_for_project

        with patch("dash.providers.get_registry", return_value=fake_reg):
            result = resolve(["src_y"], "project_a")

        assert not result.all_accessible
        # Verify isolation: src_y NOT visible to project_a
        assert any(
            ("src_y" in e and "project_a" in e) or "unknown source 'src_y'" in e
            for e in result.errors
        )
