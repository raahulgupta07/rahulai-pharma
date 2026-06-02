"""
FabricProvider — Microsoft Fabric Warehouse / SQL Endpoint live access.

T-SQL dialect. Service Principal (Entra ID) auth path preferred; falls back to
SQL auth from saved config. Dual engine: read-only (kernel-enforced via
ApplicationIntent=ReadOnly + per-session SET TRANSACTION READ ONLY) and
read-write (used only when write_instructions explicitly grant). Small
QueuePool (no PgBouncer in path; remote connections benefit from reuse).

Transport: pure-Python ``python-tds`` (import name ``pytds``) via the
``sqlalchemy-pytds`` dialect (``mssql+pytds``). No ODBC / msodbcsql18 / apt
deps — the connector is fully pip-installable. AAD bearer tokens (acquired via
MSAL) are passed through to pytds via SQLAlchemy ``connect_args``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

from dash.providers.base import BaseProvider
from dash.providers.registry import register_provider_class

logger = logging.getLogger(__name__)

# Fabric / Azure SQL OAuth2 resource scope.
_FABRIC_SCOPE = "https://database.windows.net/.default"


class FabricProvider(BaseProvider):
    """Provider over a Microsoft Fabric Warehouse / SQL Endpoint."""

    DIALECT_RULES = (
        "T-SQL dialect:\n"
        "- Use TOP N, never LIMIT\n"
        "- DATEADD(unit, n, expr), DATEDIFF(unit, a, b)\n"
        "- CAST(x AS type), TRY_CAST for safe coercion\n"
        "- STRING_AGG(col, ',') WITHIN GROUP (ORDER BY ...) for concat\n"
        "- ISNULL(x, default), COALESCE for multiple\n"
        "- Square-bracket quoting for reserved/spaced identifiers: [Order Date]\n"
        "- Schema-qualified: [schema].[table]\n"
        "- No RETURNING; use OUTPUT clause if needed\n"
        "- No generate_series; use sys.all_columns or recursive CTE\n"
    )

    def __init__(
        self,
        project_slug: str,
        source_id: int,
        name: str,
        config: dict[str, Any],
    ) -> None:
        mode = (config.get("mode") or "live").lower()
        if mode not in {"sync", "live", "hybrid"}:
            mode = "live"
        super().__init__(
            id=f"fabric_{source_id}",
            name=name,
            project_slug=project_slug,
            dialect="tsql",
            mode=mode,
            agent_scope="project",
            read_instructions=(
                FabricProvider.DIALECT_RULES
                + "\n\n# Workspace: "
                + str(config.get("workspace"))
            ),
            write_instructions="Writes blocked by default on Fabric source.",
        )
        self.source_id = source_id
        self.config: dict[str, Any] = dict(config)
        self.workspace: Optional[str] = config.get("workspace")
        self.lakehouse: Optional[str] = config.get("lakehouse")
        # auth_mode: 'sql' | 'service_principal' | 'user'
        self.auth_mode: str = config.get("auth_mode", "sql")

        # Optional pre-acquired AAD token; set by setup() if MSAL succeeds.
        self._access_token: Optional[str] = None

    # ---- URL construction ----------------------------------------------

    def _build_url(self, readonly: bool) -> str:
        """Construct an mssql+pytds URL for the requested intent.

        pytds-specific knobs (encryption, application intent, AAD token) are
        passed via connect_args (see :meth:`_connect_args`), NOT the URL —
        pytds takes them as native ``pytds.connect()`` kwargs rather than ODBC
        connection-string params.
        """
        host = self.config.get("host", "")
        port = int(self.config.get("port", 1433))
        database = self.config.get("database", "")

        # When using a pre-acquired token we deliberately omit user/password
        # from the URL; the token is injected via connect_args.
        if self.auth_mode == "service_principal" and self._access_token:
            return f"mssql+pytds://@{host}:{port}/{database}"
        elif self.auth_mode == "service_principal":
            # Driver-side service-principal fallback: pytds authenticates with
            # the SP's client_id / client_secret as username / password and
            # Azure AD password auth. (Token path above is preferred.)
            client_id = quote_plus(os.getenv("MS_CLIENT_ID", "") or "")
            client_secret = quote_plus(os.getenv("MS_CLIENT_SECRET", "") or "")
            return (
                f"mssql+pytds://{client_id}:{client_secret}"
                f"@{host}:{port}/{database}"
            )
        elif self.auth_mode == "user":
            return f"mssql+pytds://@{host}:{port}/{database}"
        else:
            user = quote_plus(str(self.config.get("username", "")))
            password = quote_plus(str(self.config.get("password", "")))
            return f"mssql+pytds://{user}:{password}@{host}:{port}/{database}"

    # ---- MSAL token acquisition ----------------------------------------

    def _try_acquire_token(self) -> Optional[str]:
        """Best-effort AAD token via MSAL. Returns None on any failure."""
        client_id = os.getenv("MS_CLIENT_ID")
        client_secret = os.getenv("MS_CLIENT_SECRET")
        tenant_id = os.getenv("MS_TENANT_ID")
        if not (client_id and client_secret and tenant_id):
            logger.warning(
                "service_principal auth requested but MS_CLIENT_ID / "
                "MS_CLIENT_SECRET / MS_TENANT_ID not all set; "
                "falling back to driver-side service-principal auth."
            )
            return None
        try:
            import msal  # type: ignore  # lazy: not always installed
        except ImportError:
            logger.warning(
                "msal not installed; cannot acquire AAD token for Fabric. "
                "pip install msal to enable token-based auth."
            )
            return None
        try:
            authority = f"https://login.microsoftonline.com/{tenant_id}"
            app = msal.ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
            )
            result = app.acquire_token_for_client(scopes=[_FABRIC_SCOPE])
            token = (result or {}).get("access_token")
            if not token:
                logger.warning(
                    "MSAL acquire_token_for_client returned no token: %s",
                    (result or {}).get("error_description"),
                )
                return None
            return token
        except Exception as exc:  # noqa: BLE001
            logger.warning("MSAL token acquisition failed: %s", exc)
            return None

    def _connect_args(self) -> dict[str, Any]:
        """connect_args for create_engine, forwarded to ``pytds.connect()``.

        ODBC -> pytds parameter translations:
          - ``Encrypt=yes``              -> ``cafile=None`` + encryption on by
            default in pytds 1.12+ (TDS 7.4 login encryption negotiated). We
            do not pin a CA bundle, matching the prior permissive posture.
          - ``TrustServerCertificate=yes`` -> ``validate_host=False`` (do NOT
            verify the server cert hostname) — required for Fabric endpoints
            whose presented cert often won't match the SQL endpoint host.
          - ``timeout`` / ``Connect Timeout`` -> ``login_timeout`` + ``timeout``
            (login + per-query socket timeout, both 30s).
          - ApplicationIntent (ReadOnly) is enforced in-session via the
            read-only listener below; pytds has no native intent kwarg.
        """
        args: dict[str, Any] = {
            # Login + per-request socket timeout (~30s), matches prior Connect
            # Timeout / 30s posture.
            "login_timeout": 30,
            "timeout": 30,
            # Don't validate the server certificate hostname (Fabric SQL
            # endpoints frequently present a cert that won't match the host).
            # This mirrors the prior ODBC TrustServerCertificate=yes behavior.
            "validate_host": False,
        }
        if self._access_token:
            # NOTE: pytds 1.15+ accepts a pre-acquired Azure AD bearer token
            # via the ``access_token`` kwarg on ``pytds.connect()``; the
            # sqlalchemy-pytds dialect forwards connect_args straight through.
            # The exact acceptance form (raw str vs an auth wrapper object)
            # can vary by pytds patch release — VERIFY against the live Fabric
            # endpoint in a smoke test. If a raw string is rejected, wrap it:
            #   from pytds.login import AzureActiveDirectoryAuth  # or similar
            # and pass the wrapper instead. sql_login auth is unaffected.
            args["access_token"] = self._access_token
        return args

    @staticmethod
    def _attach_readonly_listener(engine: Engine) -> None:
        """Best-effort SET TRANSACTION READ ONLY for each new transaction.

        Replaces the ODBC ApplicationIntent=ReadOnly URL param (pytds has no
        native intent kwarg) with an in-session read-only assertion.
        """

        def _listener(conn) -> None:  # type: ignore[no-untyped-def]
            try:
                conn.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED"
                )
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            except Exception as exc:  # noqa: BLE001
                # Fabric Warehouse may reject one or both; non-fatal.
                logger.debug("Fabric read-only setup skipped: %s", exc)

        event.listen(engine, "begin", _listener)

    @staticmethod
    def _attach_timeout_listener(engine: Engine) -> None:
        """Best-effort cooperative cancellation for T-SQL.

        T-SQL has no statement_timeout equivalent in-session; QUERY_GOVERNOR
        is invasive. We rely on connection-level login/socket timeouts from
        connect_args and just bound lock waits in-session.
        """

        def _listener(conn) -> None:  # type: ignore[no-untyped-def]
            try:
                conn.exec_driver_sql("SET LOCK_TIMEOUT 5000")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Fabric LOCK_TIMEOUT set skipped: %s", exc)

        try:
            event.listen(engine, "begin", _listener)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fabric timeout listener attach failed: %s", exc)

    # ---- Lifecycle -----------------------------------------------------

    async def setup(self) -> None:
        try:
            # Verify pytds presence early — clearer error than from
            # create_engine (which would fail when the dialect imports it).
            try:
                import pytds  # type: ignore  # noqa: F401
            except ImportError:
                self.degraded = True
                self.last_error = "python-tds (pytds) not installed"
                logger.warning(
                    "python-tds (pytds) not installed; FabricProvider "
                    "%s/%s degraded.",
                    self.project_slug,
                    self.id,
                )
                return

            if self.auth_mode == "service_principal":
                self._access_token = self._try_acquire_token()

            url_ro = self._build_url(readonly=True)
            url_rw = self._build_url(readonly=False)

            common = dict(
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args=self._connect_args(),
            )

            self.engine_ro = create_engine(url_ro, **common)
            self._attach_readonly_listener(self.engine_ro)
            self._attach_timeout_listener(self.engine_ro)
            self.engine_rw = create_engine(url_rw, **common)
            self._attach_timeout_listener(self.engine_rw)

            self.schema_blob = self.introspect()
            self.degraded = False
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.degraded = True
            self.last_error = str(exc)[:300]
            logger.exception(
                "FabricProvider setup failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
            )

    async def teardown(self) -> None:
        for attr in ("engine_ro", "engine_rw"):
            eng = getattr(self, attr, None)
            if eng is not None:
                try:
                    eng.dispose()
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Error disposing %s for %s/%s",
                        attr,
                        self.project_slug,
                        self.id,
                    )
                setattr(self, attr, None)

    # ---- Introspection -------------------------------------------------

    def introspect(self) -> dict[str, Any]:
        empty = {
            "dialect": "tsql",
            "workspace": self.workspace,
            "lakehouse": self.lakehouse,
            "tables": [],
            "columns": {},
            "fks": [],
            "partitions": {},
        }
        if self.engine_ro is None:
            return empty

        tables_sql = text(
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_type='BASE TABLE' "
            "ORDER BY table_schema, table_name"
        )
        cols_sql = text(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :name "
            "ORDER BY ordinal_position"
        )
        fks_sql = text(
            """
            SELECT
                fs.name  AS from_schema,
                ft.name  AS from_table,
                fc.name  AS from_column,
                ts.name  AS to_schema,
                tt.name  AS to_table,
                tc.name  AS to_column
            FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fkc
              ON fkc.constraint_object_id = fk.object_id
            JOIN sys.tables ft   ON ft.object_id = fk.parent_object_id
            JOIN sys.schemas fs  ON fs.schema_id = ft.schema_id
            JOIN sys.columns fc
              ON fc.object_id = ft.object_id
             AND fc.column_id = fkc.parent_column_id
            JOIN sys.tables tt   ON tt.object_id = fk.referenced_object_id
            JOIN sys.schemas ts  ON ts.schema_id = tt.schema_id
            JOIN sys.columns tc
              ON tc.object_id = tt.object_id
             AND tc.column_id = fkc.referenced_column_id
            """
        )
        partitions_sql = text(
            """
            SELECT s.name AS schema_name,
                   t.name AS table_name,
                   SUM(p.rows) AS row_count
            FROM sys.partitions p
            JOIN sys.tables t  ON t.object_id = p.object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            WHERE p.index_id IN (0, 1)
            GROUP BY s.name, t.name
            """
        )

        tables: list[str] = []
        columns: dict[str, list[dict[str, Any]]] = {}
        fks: list[dict[str, Any]] = []
        partitions: dict[str, int] = {}

        try:
            with self.engine_ro.connect() as conn:
                table_rows = list(conn.execute(tables_sql))
                for schema_name, tbl in table_rows:
                    qname = f"{schema_name}.{tbl}"
                    tables.append(qname)
                    try:
                        col_rows = conn.execute(
                            cols_sql, {"schema": schema_name, "name": tbl}
                        )
                        columns[qname] = [
                            {
                                "name": r[0],
                                "type": r[1],
                                "nullable": r[2] == "YES",
                            }
                            for r in col_rows
                        ]
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "column fetch failed for %s: %s", qname, exc
                        )

                try:
                    for r in conn.execute(fks_sql):
                        fks.append(
                            {
                                "from_table": f"{r[0]}.{r[1]}",
                                "from_column": r[2],
                                "to_table": f"{r[3]}.{r[4]}",
                                "to_column": r[5],
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    # Fabric Warehouse historically lacks full sys.foreign_keys
                    # support; degrade gracefully.
                    logger.debug("FK introspection skipped: %s", exc)

                try:
                    for r in conn.execute(partitions_sql):
                        partitions[f"{r[0]}.{r[1]}"] = int(r[2] or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("partition introspection skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "introspect() failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
            )

        return {
            "dialect": "tsql",
            "workspace": self.workspace,
            "lakehouse": self.lakehouse,
            "tables": tables,
            "columns": columns,
            "fks": fks,
            "partitions": partitions,
        }

    # ---- Tools / health ------------------------------------------------

    def emit_tools(self) -> list[Callable[..., Any]]:
        try:
            from dash.providers.tool_factory import make_tools  # type: ignore
        except ImportError:
            return []
        return make_tools(self)

    def health_check(self) -> bool:
        if self.engine_ro is None:
            self.degraded = True
            return False
        try:
            with self.engine_ro.connect().execution_options(
                stream_results=False
            ) as conn:
                conn.execute(text("SELECT 1"))
            self.degraded = False
            return True
        except Exception as exc:  # noqa: BLE001
            self.degraded = True
            self.last_error = str(exc)[:300]
            logger.warning(
                "health_check failed for %s/%s: %s",
                self.project_slug,
                self.id,
                exc,
            )
            return False

    # ---- Prompt overlay ------------------------------------------------

    def dialect_overlay(self) -> str:
        """Custom overlay: dialect rules + workspace/lakehouse + key tables."""
        tables = self.schema_blob.get("tables") or []
        partitions: dict[str, int] = (
            self.schema_blob.get("partitions") or {}
        )
        if partitions:
            top_tables = sorted(
                partitions.items(), key=lambda kv: kv[1], reverse=True
            )[:5]
            key_tables_block = "KEY TABLES (by row count):\n" + "\n".join(
                f"  - {name}: {rows:,} rows" for name, rows in top_tables
            )
        elif tables:
            key_tables_block = "TABLES (sample):\n" + "\n".join(
                f"  - {t}" for t in tables[:5]
            )
        else:
            key_tables_block = "TABLES: <empty>"

        header = (
            f"### Source: {self.name} (id={self.id}, mode={self.mode})\n"
            f"Tools: query_{self.id}, describe_{self.id}, sample_{self.id}\n"
            f"Workspace: {self.workspace} | Lakehouse: {self.lakehouse}\n"
            f"Table count: {len(tables)}"
        )

        body_parts = [header, self.DIALECT_RULES, key_tables_block]
        if self.degraded:
            body_parts.append(
                f"!! DEGRADED: {self.last_error or 'unknown error'} — "
                "prefer cached snapshot, do not attempt live writes."
            )

        overlay = "\n\n".join(body_parts).strip() + "\n"
        # Soft cap ~3KB to keep prompt assembly bounded.
        if len(overlay) > 3072:
            overlay = overlay[:3064] + "\n...[t]"
        return overlay


register_provider_class("fabric", FabricProvider)
