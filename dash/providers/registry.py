"""Process-wide registry of :class:`BaseProvider` instances.

Holds providers per ``project_slug``, deduped by ``(project_slug, provider_id)``.
Failures during :meth:`load_for_project` are logged and the offending provider
is marked ``degraded`` — they do NOT bubble up, so a single broken source
cannot prevent an agent session from starting.

Concrete provider classes (PostgresProvider, MySQLProvider, FabricProvider, ...)
register themselves at import time via :func:`register_provider_class`. The
registry resolves the right subclass per row in ``dash_data_sources`` using
the ``provider_class`` column.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .base import BaseProvider

logger = logging.getLogger(__name__)


# Subclass factory registry. Concrete provider modules call
# register_provider_class("postgres", PostgresProvider) on import; this keeps
# the registry decoupled from any specific transport.
_PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {}


def register_provider_class(name: str, cls: type[BaseProvider]) -> None:
    """Register a concrete :class:`BaseProvider` subclass under ``name``.

    Concrete provider modules call this at import time so the registry can
    construct instances from ``dash_data_sources`` rows without circular
    imports.
    """
    if not issubclass(cls, BaseProvider):
        raise TypeError(f"{cls!r} is not a BaseProvider subclass")
    if name in _PROVIDER_CLASSES and _PROVIDER_CLASSES[name] is not cls:
        logger.warning(
            "Overriding provider class %r: %s -> %s",
            name,
            _PROVIDER_CLASSES[name].__name__,
            cls.__name__,
        )
    _PROVIDER_CLASSES[name] = cls


class ProviderRegistry:
    """Thread-safe per-project provider registry."""

    def __init__(self) -> None:
        # (project_slug, provider_id) -> provider
        self._providers: dict[tuple[str, str], BaseProvider] = {}
        self._lock = threading.Lock()

    # ---- Sync surface ---------------------------------------------------

    def register(self, provider: BaseProvider) -> None:
        """Register a provider, disposing any prior instance with the same key."""
        key = (provider.project_slug, provider.id)
        with self._lock:
            existing = self._providers.get(key)
            self._providers[key] = provider
        if existing is not None and existing is not provider:
            logger.info(
                "Replacing provider %s/%s; disposing previous instance",
                provider.project_slug,
                provider.id,
            )
            # Best-effort sync dispose — async teardown handled at project level.
            try:
                if existing.engine_ro is not None:
                    existing.engine_ro.dispose()
                if existing.engine_rw is not None:
                    existing.engine_rw.dispose()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Error disposing replaced provider %s/%s",
                    existing.project_slug,
                    existing.id,
                )

    def unregister(self, project_slug: str, provider_id: str) -> None:
        with self._lock:
            self._providers.pop((project_slug, provider_id), None)

    def get(
        self, project_slug: str, provider_id: str
    ) -> Optional[BaseProvider]:
        with self._lock:
            return self._providers.get((project_slug, provider_id))

    def list_for_project(
        self,
        project_slug: str,
        agent_scope: Optional[str] = None,
    ) -> list[BaseProvider]:
        """Return providers for a project, optionally filtered by scope.

        Scope filter matches if the provider's ``agent_scope`` equals the
        requested scope, OR is ``"shared"`` / ``"project"`` (broadly visible).
        """
        with self._lock:
            providers = [
                p
                for (slug, _pid), p in self._providers.items()
                if slug == project_slug
            ]
        if agent_scope is None:
            return providers
        return [
            p
            for p in providers
            if p.agent_scope in {agent_scope, "shared", "project"}
        ]

    # ---- Async surface --------------------------------------------------

    async def load_for_project(
        self, project_slug: str
    ) -> list[BaseProvider]:
        """Read ``dash_data_sources`` rows for slug, instantiate, set up.

        Failures are logged and the provider is marked degraded; this method
        never raises. Returns the list of providers it tried to load
        (including degraded ones), so callers can surface state in the UI.

        TODO Phase 2: read rows from ``dash_data_sources`` via
        ``app.db.session`` instead of the placeholder iteration below.
        TODO Phase 3: cache provider configs in Redis with TTL keyed on
        project_slug to avoid hot-path DB hits.
        TODO Phase 4: trigger nightly drift check after successful setup
        (compare schema_blob hash vs persisted snapshot).
        """
        rows = await self._fetch_data_source_rows(project_slug)
        loaded: list[BaseProvider] = []
        for row in rows:
            provider_class_name = row.get("provider_class")
            # 'postgres_local' = local schema (no remote provider needed) —
            # silent no-op, not an error.
            if provider_class_name == "postgres_local":
                continue
            cls = _PROVIDER_CLASSES.get(provider_class_name or "")
            if cls is None:
                logger.warning(
                    "Unknown provider_class %r for project %s; skipping",
                    provider_class_name,
                    project_slug,
                )
                continue
            try:
                provider = cls(**row.get("init_kwargs", {}))
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to instantiate %s for project %s: %s",
                    provider_class_name,
                    project_slug,
                    exc,
                )
                continue

            try:
                await provider.setup()
            except Exception as exc:  # noqa: BLE001
                provider.degraded = True
                provider.last_error = str(exc)
                logger.exception(
                    "Provider %s/%s setup failed; marking degraded",
                    provider.project_slug,
                    provider.id,
                )

            self.register(provider)
            loaded.append(provider)
        return loaded

    async def dispose_for_project(self, project_slug: str) -> None:
        with self._lock:
            keys = [k for k in self._providers if k[0] == project_slug]
            providers = [self._providers.pop(k) for k in keys]
        for p in providers:
            try:
                await p.teardown()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Error tearing down provider %s/%s",
                    p.project_slug,
                    p.id,
                )

    async def dispose_all(self) -> None:
        """Dispose every provider — call from app shutdown hook."""
        with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
        for p in providers:
            try:
                await p.teardown()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Error tearing down provider %s/%s",
                    p.project_slug,
                    p.id,
                )

    # ---- Internals ------------------------------------------------------

    async def _fetch_data_source_rows(
        self, project_slug: str
    ) -> list[dict]:
        """Read active rows from ``dash_data_sources`` for this project.

        Returns rows shaped as ``{provider_class, init_kwargs}`` ready for
        :meth:`load_for_project`. Always includes a synthetic local provider
        so legacy projects (no remote sources) still get tooling.
        """
        import base64
        import json

        out: list[dict] = []
        # Always seed the local Postgres schema as a provider — preserves the
        # legacy single-schema path for projects with no remote sources.
        out.append({
            "provider_class": "postgres_local",
            "init_kwargs": {"project_slug": project_slug, "source_id": 0, "name": "local"},
        })

        try:
            from sqlalchemy import text
            from db.session import get_sql_engine
            engine = get_sql_engine()
            sql = text(
                "SELECT id, source_type, provider_class, dialect, mode, "
                "agent_scope, site_name, config "
                "FROM public.dash_data_sources "
                "WHERE project_slug = :slug AND status = 'active' "
                "AND source_type IN ('postgresql', 'mysql', 'fabric', "
                "'gdrive', 'sharepoint', 'onedrive', 'mcp')"
            )
            with engine.connect() as conn:
                rows = conn.execute(sql, {"slug": project_slug}).mappings().all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_fetch_data_source_rows: DB read failed for %s: %s",
                project_slug, exc,
            )
            return out

        sql_source_types = {"postgresql", "mysql", "fabric"}
        for row in rows:
            cfg_raw = row["config"]
            cfg = cfg_raw if isinstance(cfg_raw, dict) else (json.loads(cfg_raw) if cfg_raw else {})
            # Only SQL sources store passwords; token-based sources (gdrive,
            # sharepoint, onedrive) keep credentials in dash_tokens etc.
            if row["source_type"] in sql_source_types:
                if "password_b64" in cfg and "password" not in cfg:
                    try:
                        cfg["password"] = base64.b64decode(cfg["password_b64"]).decode()
                    except Exception:  # noqa: BLE001
                        pass
            cfg["mode"] = row.get("mode") or cfg.get("mode") or "sync"
            cfg["agent_scope"] = row.get("agent_scope") or "project"

            pclass = row.get("provider_class") or {
                "postgresql": "postgres_remote",
                "mysql": "mysql_remote",
                "fabric": "fabric",
                "gdrive": "gdrive",
                "sharepoint": "sharepoint",
                "onedrive": "onedrive",
                "mcp": "mcp",
            }.get(row["source_type"])
            if not pclass:
                continue

            out.append({
                "provider_class": pclass,
                "init_kwargs": {
                    "project_slug": project_slug,
                    "source_id": row["id"],
                    "name": row["site_name"] or f"{row['source_type']}_{row['id']}",
                    "config": cfg,
                },
            })
        logger.info(
            "_fetch_data_source_rows: %d rows for %s (incl. local)",
            len(out), project_slug,
        )
        return out


# Module-level singleton.
_registry = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Return the process-wide :class:`ProviderRegistry` singleton."""
    return _registry
