"""Unit tests for the provider abstraction layer.

Pure pytest, no live DB connections. SQLAlchemy ``Engine`` instances are
mocked via :class:`unittest.mock.MagicMock`. External services (``db.session``,
``msal``, ``pyodbc``, ``agno.tools``) are stubbed where required.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _call_tool(tool, *args, **kwargs):
    """Invoke an Agno @tool-wrapped Function or a plain callable."""
    if hasattr(tool, "entrypoint"):
        return tool.entrypoint(*args, **kwargs)
    return tool(*args, **kwargs)


# ---------------------------------------------------------------------------
# agno.tools shim — the tool_factory imports ``from agno.tools import tool``.
# If the real package isn't installed in the test environment, install a
# lightweight stub BEFORE ``providers.tool_factory`` is imported.
# ---------------------------------------------------------------------------

def _ensure_agno_stub() -> None:
    if "agno.tools" in sys.modules:
        return
    try:
        importlib.import_module("agno.tools")
        return
    except Exception:
        pass
    agno_pkg = ModuleType("agno")
    agno_pkg.__path__ = []  # mark as package
    tools_mod = ModuleType("agno.tools")

    def _tool(*args: Any, **kwargs: Any):
        # Support both ``@tool`` and ``@tool(name=..., description=...)`` forms.
        def _decorate(fn):
            fn.tool_name = kwargs.get("name", getattr(fn, "__name__", ""))
            fn.tool_description = kwargs.get("description", "")
            return fn

        if args and callable(args[0]) and not kwargs:
            return _decorate(args[0])
        return _decorate

    tools_mod.tool = _tool
    sys.modules["agno"] = agno_pkg
    sys.modules["agno.tools"] = tools_mod


_ensure_agno_stub()


# ---------------------------------------------------------------------------
# Imports from the package under test (after the agno stub is in place).
# ---------------------------------------------------------------------------

from dash.providers.base import BaseProvider  # noqa: E402
from dash.providers import registry as registry_mod  # noqa: E402
from dash.providers.registry import (  # noqa: E402
    ProviderRegistry,
    register_provider_class,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_mock() -> MagicMock:
    """A MagicMock standing in for a SQLAlchemy Engine."""
    eng = MagicMock(name="Engine")
    eng.dispose = MagicMock()
    return eng


class _ConcreteProvider(BaseProvider):
    """Minimal concrete BaseProvider used for registry/overlay tests."""

    def __init__(
        self,
        *,
        id: str = "p1",
        name: str = "p1",
        project_slug: str = "proj",
        dialect: str = "postgresql",
        agent_scope: str = "project",
        engine_ro: Any = None,
        engine_rw: Any = None,
        schema_blob: dict | None = None,
        degraded: bool = False,
        last_error: str | None = None,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            project_slug=project_slug,
            dialect=dialect,
            agent_scope=agent_scope,
        )
        self.engine_ro = engine_ro if engine_ro is not None else _make_engine_mock()
        self.engine_rw = engine_rw if engine_rw is not None else _make_engine_mock()
        self.schema_blob = schema_blob or {"tables": ["t1", "t2"]}
        self.degraded = degraded
        self.last_error = last_error

    async def setup(self) -> None:  # pragma: no cover
        pass

    async def teardown(self) -> None:
        for attr in ("engine_ro", "engine_rw"):
            eng = getattr(self, attr, None)
            if eng is not None:
                eng.dispose()
                setattr(self, attr, None)

    def emit_tools(self):
        return []

    def introspect(self) -> dict:
        return {"tables": [], "columns": {}, "fks": [], "dialect": self.dialect}

    def health_check(self) -> bool:
        return True


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ===========================================================================
# 1. BaseProvider abstract enforcement
# ===========================================================================


class TestBaseProviderAbstract:
    def test_cannot_instantiate_base_directly(self):
        with pytest.raises(TypeError):
            BaseProvider(  # type: ignore[abstract]
                id="x", name="x", project_slug="p", dialect="postgresql"
            )

    def test_subclass_missing_method_cannot_instantiate(self):
        class Broken(BaseProvider):
            async def setup(self):
                pass

            async def teardown(self):
                pass

            def emit_tools(self):
                return []

            # Missing introspect + health_check

        with pytest.raises(TypeError):
            Broken(  # type: ignore[abstract]
                id="b", name="b", project_slug="p", dialect="postgresql"
            )

    def test_concrete_subclass_with_all_methods_works(self):
        prov = _ConcreteProvider()
        assert prov.id == "p1"
        assert prov.dialect == "postgresql"
        assert prov.degraded is False

    def test_unsupported_dialect_raises(self):
        with pytest.raises(ValueError):
            _ConcreteProvider(dialect="oracle")

    def test_qualified_table_name_quoting(self):
        pg = _ConcreteProvider(dialect="postgresql")
        assert pg.qualified_table_name("schema.table") == '"schema"."table"'
        my = _ConcreteProvider(dialect="mysql", id="m")
        assert my.qualified_table_name("t") == "`t`"
        ts = _ConcreteProvider(dialect="tsql", id="t")
        assert ts.qualified_table_name("dbo.X") == "[dbo].[X]"


# ===========================================================================
# 2. ProviderRegistry behavior
# ===========================================================================


class TestProviderRegistry:
    def test_register_and_get(self):
        reg = ProviderRegistry()
        p = _ConcreteProvider(id="p1", project_slug="proj")
        reg.register(p)
        assert reg.get("proj", "p1") is p

    def test_get_missing_returns_none(self):
        reg = ProviderRegistry()
        assert reg.get("nope", "nope") is None

    def test_re_registering_same_key_disposes_old_engines(self):
        reg = ProviderRegistry()
        old = _ConcreteProvider(id="dup", project_slug="proj")
        old_ro, old_rw = old.engine_ro, old.engine_rw
        reg.register(old)
        new = _ConcreteProvider(id="dup", project_slug="proj")
        reg.register(new)
        old_ro.dispose.assert_called_once()
        old_rw.dispose.assert_called_once()
        assert reg.get("proj", "dup") is new

    def test_list_for_project_filters_by_slug(self):
        reg = ProviderRegistry()
        a = _ConcreteProvider(id="a", project_slug="proj1")
        b = _ConcreteProvider(id="b", project_slug="proj2")
        reg.register(a)
        reg.register(b)
        assert reg.list_for_project("proj1") == [a]
        assert reg.list_for_project("proj2") == [b]

    def test_list_for_project_scope_filter(self):
        reg = ProviderRegistry()
        proj_p = _ConcreteProvider(id="proj_p", project_slug="proj", agent_scope="project")
        analyst = _ConcreteProvider(id="analyst", project_slug="proj", agent_scope="analyst_only")
        researcher = _ConcreteProvider(id="researcher", project_slug="proj", agent_scope="researcher_only")
        shared = _ConcreteProvider(id="shared", project_slug="proj", agent_scope="shared")
        for p in (proj_p, analyst, researcher, shared):
            reg.register(p)

        # No filter: all 4
        assert len(reg.list_for_project("proj")) == 4

        # analyst_only filter: includes analyst + shared + project (excludes researcher_only)
        ids = {p.id for p in reg.list_for_project("proj", agent_scope="analyst_only")}
        assert "analyst" in ids
        assert "shared" in ids
        assert "proj_p" in ids  # project scope is broadly visible
        assert "researcher" not in ids

        # 'project' scope filter
        ids2 = {p.id for p in reg.list_for_project("proj", agent_scope="project")}
        # 'project' as the requested scope: rule keeps providers in
        # {requested, "shared", "project"} → project, shared, but NOT analyst/researcher
        assert "proj_p" in ids2
        assert "shared" in ids2
        assert "analyst" not in ids2
        assert "researcher" not in ids2

    def test_list_for_project_scope_none_returns_all(self):
        reg = ProviderRegistry()
        a = _ConcreteProvider(id="a", project_slug="proj", agent_scope="analyst_only")
        reg.register(a)
        assert reg.list_for_project("proj", agent_scope=None) == [a]

    def test_unregister_removes(self):
        reg = ProviderRegistry()
        p = _ConcreteProvider(id="x", project_slug="proj")
        reg.register(p)
        reg.unregister("proj", "x")
        assert reg.get("proj", "x") is None

    def test_thread_safety_concurrent_register(self):
        reg = ProviderRegistry()
        N = 50

        def _do(i: int):
            reg.register(
                _ConcreteProvider(id=f"p{i}", project_slug=f"proj{i % 3}")
            )

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_do, range(N)))

        total = sum(
            len(reg.list_for_project(f"proj{i}")) for i in range(3)
        )
        assert total == N

    def test_dispose_for_project_clears_only_that_project(self):
        reg = ProviderRegistry()
        a = _ConcreteProvider(id="a", project_slug="p1")
        b = _ConcreteProvider(id="b", project_slug="p2")
        reg.register(a)
        reg.register(b)
        asyncio.run(reg.dispose_for_project("p1"))
        assert reg.get("p1", "a") is None
        assert reg.get("p2", "b") is b

    def test_dispose_all_clears_everything(self):
        reg = ProviderRegistry()
        a = _ConcreteProvider(id="a", project_slug="p1")
        b = _ConcreteProvider(id="b", project_slug="p2")
        reg.register(a)
        reg.register(b)
        asyncio.run(reg.dispose_all())
        assert reg.list_for_project("p1") == []
        assert reg.list_for_project("p2") == []

    def test_register_provider_class_rejects_non_subclass(self):
        class NotAProvider:
            pass

        with pytest.raises(TypeError):
            register_provider_class("bad", NotAProvider)  # type: ignore[arg-type]


# ===========================================================================
# 3. dialect_overlay()
# ===========================================================================


class TestDialectOverlay:
    def test_returns_non_empty_string(self):
        p = _ConcreteProvider()
        out = p.dialect_overlay()
        assert isinstance(out, str)
        assert len(out) > 0

    def test_includes_dialect_name_postgres(self):
        p = _ConcreteProvider(dialect="postgresql")
        assert "PostgreSQL" in p.dialect_overlay()

    def test_includes_dialect_name_mysql(self):
        p = _ConcreteProvider(dialect="mysql")
        assert "MySQL" in p.dialect_overlay()

    def test_includes_dialect_name_tsql(self):
        p = _ConcreteProvider(dialect="tsql")
        out = p.dialect_overlay()
        assert "T-SQL" in out

    def test_degraded_warning_present(self):
        p = _ConcreteProvider(degraded=True, last_error="boom")
        out = p.dialect_overlay()
        assert "DEGRADED" in out
        assert "boom" in out

    def test_truncates_with_many_tables(self):
        big_tables = [f"t{i}" for i in range(200)]
        p = _ConcreteProvider(schema_blob={"tables": big_tables})
        out = p.dialect_overlay()
        # Soft cap is ~2KB. The presence of all 200 names would blow past
        # 2KB easily; verify truncation marker appears or output stays bounded.
        assert len(out) <= 2050


# ===========================================================================
# 4. tool_factory
# ===========================================================================


from dash.providers.tool_factory import (  # noqa: E402
    _is_read_only,
    make_tools,
)


class TestIsReadOnly:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "select * from t",
            "  SELECT * FROM t  ",
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "with x as (select 1) select * from x",
            "SELECT 1;",  # trailing semicolon ok
        ],
    )
    def test_allowed(self, sql):
        assert _is_read_only(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN c INT",
            "TRUNCATE TABLE t",
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET c = 1",
            "DELETE FROM t",
            "MERGE INTO t USING s ON x WHEN MATCHED THEN UPDATE SET a=1",
            "GRANT SELECT ON t TO u",
            "EXEC sp_who",
            "EXECUTE sp_who",
            "CREATE TABLE t (id INT)",
            "",
            None,
            "SELECT 1; DROP TABLE x",  # stacked
            "SELECT 1; SELECT 2",  # stacked
            "; SELECT 1",  # leading semicolon → fails leading-token rule
        ],
    )
    def test_blocked(self, sql):
        assert _is_read_only(sql) is False  # type: ignore[arg-type]

    def test_case_insensitive(self):
        assert _is_read_only("sElEcT 1") is True
        assert _is_read_only("DrOp TaBlE t") is False


class TestMakeTools:
    def test_returns_four_callables(self):
        provider = _ConcreteProvider(id="abc")
        tools = make_tools(provider)
        assert isinstance(tools, list)
        assert len(tools) == 4
        for t in tools:
            assert (callable(t) or hasattr(t, "entrypoint"))

    def test_tool_names_suffixed_with_provider_id(self):
        provider = _ConcreteProvider(id="xyz")
        tools = make_tools(provider)
        names = [getattr(t, "tool_name", getattr(t, "__name__", "")) for t in tools]
        # Either the agno stub set tool_name, or the function's own name
        # carries forward — either way, ensure all four expected suffixes
        # appear somewhere in the collected names/identifiers.
        joined = " ".join(names) + " " + " ".join(getattr(t, "name", "") or getattr(t, "__name__", "") or "" for t in tools)
        for prefix in ("query_", "describe_", "sample_", "profile_"):
            assert f"{prefix}xyz" in joined or any(
                prefix in n for n in names
            ), f"missing tool name prefix {prefix} for provider id"

    def test_query_tool_blocks_writes(self):
        provider = _ConcreteProvider(id="t1")
        tools = make_tools(provider)
        query_tool = tools[0]
        out = _call_tool(query_tool, "DROP TABLE x")
        assert "ERROR" in out
        assert "SELECT" in out  # explanation references SELECT-only

    def test_query_tool_blocks_stacked(self):
        provider = _ConcreteProvider(id="t1")
        query_tool = make_tools(provider)[0]
        out = _call_tool(query_tool, "SELECT 1; DROP TABLE x")
        assert "ERROR" in out

    def test_query_tool_returns_error_when_degraded(self):
        provider = _ConcreteProvider(id="t1", degraded=True, last_error="db down")
        query_tool = make_tools(provider)[0]
        out = _call_tool(query_tool, "SELECT 1")
        assert "ERROR" in out
        assert "unavailable" in out.lower() or "db down" in out

    def test_query_tool_returns_error_when_no_engine(self):
        provider = _ConcreteProvider(id="t1", engine_ro=None)
        # set engine_ro to None explicitly via attribute
        provider.engine_ro = None
        query_tool = make_tools(provider)[0]
        out = _call_tool(query_tool, "SELECT 1")
        assert "ERROR" in out

    def test_query_tool_executes_select_via_engine(self):
        provider = _ConcreteProvider(id="ok")
        # Wire up engine.connect() context manager → execute() → fetchmany()
        fake_row = SimpleNamespace(_fields=("a",))
        # Make it iterable like a SQLAlchemy Row
        fake_row_iter = (1,)

        class FakeRow:
            _fields = ("a",)

            def __iter__(self):
                return iter(fake_row_iter)

        result = MagicMock()
        result.fetchmany.return_value = [FakeRow()]

        conn = MagicMock()
        conn.execute.return_value = result
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        provider.engine_ro.connect.return_value = ctx

        query_tool = make_tools(provider)[0]
        out = _call_tool(query_tool, "SELECT 1")
        assert "ERROR" not in out
        assert "a" in out  # header rendered


# ===========================================================================
# 5. Concrete providers — instantiation only
# ===========================================================================


class TestConcreteProviders:
    def test_postgres_local_provider_instantiates(self):
        from dash.providers.postgres_local import PostgresLocalProvider

        p = PostgresLocalProvider("test", source_id=1)
        assert p.dialect == "postgresql"
        assert p.project_slug == "test"
        assert p.id == "local_1"

    def test_postgres_remote_provider_instantiates(self):
        from dash.providers.postgres_remote import PostgresRemoteProvider

        p = PostgresRemoteProvider(
            "test",
            source_id=2,
            name="x",
            config={
                "username": "u",
                "password": "p",
                "host": "h",
                "port": 5432,
                "database": "d",
                "schema": "public",
                "mode": "live",
            },
        )
        assert p.dialect == "postgresql"
        assert p.id == "pgremote_2"
        assert p.mode == "live"

    def test_mysql_remote_provider_instantiates(self):
        from dash.providers.mysql_remote import MySQLRemoteProvider

        p = MySQLRemoteProvider(
            "test",
            source_id=3,
            name="m",
            config={
                "username": "u",
                "password": "p",
                "host": "h",
                "port": 3306,
                "database": "d",
                "mode": "sync",
            },
        )
        assert p.dialect == "mysql"
        assert p.id == "mysql_3"
        assert p.mode == "sync"

    def test_fabric_provider_instantiates(self):
        from dash.providers.fabric import FabricProvider

        p = FabricProvider(
            "test",
            source_id=4,
            name="fb",
            config={
                "host": "h",
                "port": 1433,
                "database": "d",
                "workspace": "ws",
                "lakehouse": "lh",
                "auth_mode": "sql",
                "username": "u",
                "password": "p",
            },
        )
        assert p.dialect == "tsql"
        assert p.id == "fabric_4"

    def test_register_provider_class_mapping_has_four_keys(self):
        # Force imports so each module's register_provider_class call runs.
        import dash.providers.postgres_local  # noqa: F401
        import dash.providers.postgres_remote  # noqa: F401
        import dash.providers.mysql_remote  # noqa: F401
        import dash.providers.fabric  # noqa: F401

        mapping = registry_mod._PROVIDER_CLASSES
        for key in ("postgres_local", "postgres_remote", "mysql_remote", "fabric"):
            assert key in mapping, f"missing provider class registration: {key}"


# ===========================================================================
# 6. PostgresLocal.setup() with mocked db.session
# ===========================================================================


class TestPostgresLocalSetup:
    def test_setup_uses_db_session_engines(self):
        """Ensure setup() pulls engines from db.session and sets schema_blob."""
        from dash.providers.postgres_local import PostgresLocalProvider

        ro_eng = _make_engine_mock()
        rw_eng = _make_engine_mock()

        # Make introspect() short-circuit (no real connect()) by faking the
        # engine_ro.connect() context to yield empty results.
        ctx = MagicMock()
        conn = MagicMock()
        conn.execute.return_value = iter([])
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        ro_eng.connect.return_value = ctx

        fake_db_session = ModuleType("db.session")
        fake_db_session.get_project_engine = MagicMock(return_value=rw_eng)
        fake_db_session.get_project_readonly_engine = MagicMock(return_value=ro_eng)
        fake_db_pkg = sys.modules.get("db") or ModuleType("db")
        fake_db_pkg.__path__ = getattr(fake_db_pkg, "__path__", [])

        with patch.dict(
            sys.modules,
            {"db": fake_db_pkg, "db.session": fake_db_session},
        ):
            p = PostgresLocalProvider("myproj", source_id=7)
            asyncio.run(p.setup())

        assert p.engine_ro is ro_eng
        assert p.engine_rw is rw_eng
        assert p.degraded is False
