"""Provider abstraction layer for per-agent data source isolation.

Each Dash project-agent owns N data sources (Postgres local/remote, MySQL,
Microsoft Fabric, ...). Each source is a single :class:`BaseProvider`
instance carrying its own engine(s), schema metadata, dialect, instructions,
and emitting its own tools (``query_<id>``, ``describe_<id>``, ``sample_<id>``).

The :class:`ProviderRegistry` (singleton, accessed via :func:`get_registry`)
holds providers per ``project_slug``, deduped by ``(project_slug, provider_id)``,
with async setup/teardown and a degraded-mode flag for graceful failure.
"""

from .base import BaseProvider
from .registry import ProviderRegistry, get_registry

__all__ = ["BaseProvider", "ProviderRegistry", "get_registry"]
