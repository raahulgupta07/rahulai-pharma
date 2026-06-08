"""
Single-agent product lock (CityPharma).

This product is PERMANENTLY single-tenant. The platform collapses to ONE
hardcoded data agent (LOCKED_PROJECT_SLUG). Project create / delete / library /
multi-project switching are disabled — every slug resolves to the locked project.

`is_single_agent()` is hardcoded TRUE — it is NOT env-controlled, so no stray
env var can flip the product back into multi-tenant mode. The underlying
multi-tenant code still exists (reversible), but to restore the full Dash
platform you must deliberately edit THIS function — not just set an env var.
"""

import os
from fastapi import HTTPException


def is_single_agent() -> bool:
    # PERMANENTLY single-tenant. Not env-controlled by design.
    # To re-enable multi-tenant Dash you must edit this return value here.
    return True


def locked_slug() -> str:
    return os.getenv("LOCKED_PROJECT_SLUG", "citypharma").strip()


def product_name() -> str:
    name = os.getenv("PRODUCT_NAME", "CityAgent Pharma").strip()
    return name if name != "CityPharma" else "CityAgent Pharma"


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
