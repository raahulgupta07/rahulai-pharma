"""Per-connection RBAC — frozen contract §4.

User dict shape: {id, username, is_super_admin, aad_groups: [guid, ...]}
Connection dict shape: {enabled, allow_all_users, users_allowed, ldap_groups_allowed, ...}
"""
from __future__ import annotations


def can_user_use(user: dict, conn: dict) -> bool:
    if not conn.get("enabled"):
        return False
    if user.get("is_super_admin"):
        return True
    if conn.get("allow_all_users"):
        return True
    if user["id"] in (conn.get("users_allowed") or []):
        return True
    if set(user.get("aad_groups") or []) & set(conn.get("ldap_groups_allowed") or []):
        return True
    return False
