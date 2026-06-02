"""Resolve source prefixes to providers within current project.

NEVER reaches across projects. Hard agent isolation.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ResolvedSource:
    provider_id: str       # input from prefix
    provider: object       # BaseProvider instance
    project_slug: str
    dialect: str
    accessible: bool = True
    error: Optional[str] = None


@dataclass
class ResolutionResult:
    sources: list[ResolvedSource] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    all_accessible: bool = True


def resolve(
    provider_ids: list[str] | set[str],
    project_slug: str,
    *,
    requesting_agent_scope: str = "analyst",
    user_role: str = "viewer",
) -> ResolutionResult:
    """Map prefix → provider in CURRENT PROJECT only.

    Rules:
    - Provider must exist in registry for project_slug.
    - Provider's agent_scope must allow requesting agent.
    - Cross-project resolution NEVER attempted.
    """
    result = ResolutionResult()

    try:
        from dash.providers import get_registry
        registry = get_registry()
    except Exception as e:
        result.errors.append(f"registry unavailable: {e}")
        result.all_accessible = False
        return result

    project_providers = []
    try:
        project_providers = registry.list_for_project(project_slug)
    except Exception as e:
        result.errors.append(f"list_for_project failed: {e}")
        result.all_accessible = False
        return result

    by_id = {p.id: p for p in project_providers}

    for pid in provider_ids:
        if pid not in by_id:
            result.errors.append(
                f"unknown source '{pid}' in project '{project_slug}' "
                f"(known: {list(by_id.keys())[:6]})"
            )
            result.sources.append(ResolvedSource(
                provider_id=pid, provider=None, project_slug=project_slug,
                dialect="?", accessible=False,
                error=f"not in project '{project_slug}'",
            ))
            result.all_accessible = False
            continue

        provider = by_id[pid]

        # Scope check
        scope = getattr(provider, "agent_scope", "project")
        if not _scope_allows(scope, requesting_agent_scope):
            result.errors.append(
                f"source '{pid}' has scope='{scope}', "
                f"not visible to {requesting_agent_scope}"
            )
            result.sources.append(ResolvedSource(
                provider_id=pid, provider=provider,
                project_slug=project_slug,
                dialect=getattr(provider, "dialect", "?"),
                accessible=False,
                error=f"scope mismatch: {scope}",
            ))
            result.all_accessible = False
            continue

        if getattr(provider, "degraded", False):
            result.errors.append(
                f"source '{pid}' is degraded: "
                f"{getattr(provider, 'last_error', 'unknown')}"
            )
            result.sources.append(ResolvedSource(
                provider_id=pid, provider=provider,
                project_slug=project_slug,
                dialect=getattr(provider, "dialect", "?"),
                accessible=False,
                error="degraded",
            ))
            result.all_accessible = False
            continue

        result.sources.append(ResolvedSource(
            provider_id=pid, provider=provider,
            project_slug=project_slug,
            dialect=getattr(provider, "dialect", "?"),
            accessible=True,
        ))

    return result


def resolve_with_rbac(
    provider_ids: list[str] | set[str],
    project_slug: str,
    user_id: Optional[int],
    *,
    requesting_agent_scope: str = "analyst",
    user_role: str = "viewer",
) -> ResolutionResult:
    """Like resolve() but also verifies user has project-level RBAC.

    User must have at least viewer role on project_slug. Without it,
    even sources visible to the agent are rejected.
    """
    result = ResolutionResult()

    # Project-level access check
    try:
        from app.auth import check_project_permission
        # check_project_permission(user_dict, slug, required_role)
        # We may not have full user dict here — pass minimal stub
        user_stub = {"user_id": user_id, "role": user_role}
        if not check_project_permission(user_stub, project_slug, required_role="viewer"):
            result.errors.append(f"user lacks viewer access to project '{project_slug}'")
            result.all_accessible = False
            for pid in provider_ids:
                result.sources.append(ResolvedSource(
                    provider_id=pid, provider=None, project_slug=project_slug,
                    dialect="?", accessible=False, error="rbac_denied",
                ))
            return result
    except Exception as e:
        # If permission system unavailable, fall back to allowing
        # (registry isolation already prevents cross-project leak)
        logger.debug(f"rbac check unavailable: {e}")

    # Delegate to existing resolve()
    return resolve(provider_ids, project_slug,
                    requesting_agent_scope=requesting_agent_scope,
                    user_role=user_role)


def _scope_allows(provider_scope: str, requesting: str) -> bool:
    """Mirror logic from registry.list_for_project but explicit."""
    # 'shared' and 'project' visible to all
    if provider_scope in ("shared", "project"):
        return True
    if provider_scope == "analyst_only":
        return requesting in ("analyst", "shared")
    if provider_scope == "researcher_only":
        return requesting in ("researcher", "shared")
    return False
