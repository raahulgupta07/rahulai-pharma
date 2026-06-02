"""
Single-agent product lock (CityPharma).

When SINGLE_AGENT_MODE=1 the platform collapses to ONE hardcoded data agent
(LOCKED_PROJECT_SLUG). Project create / delete / library / multi-project
switching are disabled — every slug resolves to the locked project.

Multi-tenant code stays intact behind the flag (reversible): set
SINGLE_AGENT_MODE=0 to restore the full Dash platform.
"""

import os
from fastapi import HTTPException


def is_single_agent() -> bool:
    return os.getenv("SINGLE_AGENT_MODE", "0").strip().lower() in ("1", "true", "yes", "on")


def locked_slug() -> str:
    return os.getenv("LOCKED_PROJECT_SLUG", "citypharma").strip()


def product_name() -> str:
    return os.getenv("PRODUCT_NAME", "CityPharma").strip()


def resolve_slug(slug: str | None = None) -> str:
    """In single-agent mode, ignore the requested slug → always the locked one."""
    if is_single_agent():
        return locked_slug()
    return slug or ""


def guard_no_project_management(action: str = "manage projects") -> None:
    """Raise 403 for project-CRUD endpoints when the product is locked to one agent."""
    if is_single_agent():
        raise HTTPException(
            status_code=403,
            detail=f"{product_name()} is a single-agent product — cannot {action}.",
        )
