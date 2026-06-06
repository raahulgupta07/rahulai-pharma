"""
Federated authentication — LDAP + OIDC/SSO (OpenWebUI-modeled)
==============================================================

Adds LDAP (bind-search-bind) and generic OIDC (Google / Microsoft / Keycloak /
any OpenID provider) on top of the existing local username+password auth in
`app/auth.py`. Modeled on Open WebUI's federated-auth design:

  - LDAP: app-bind → search by username attr (filter chars escaped) → rebind as
    the discovered user DN with the supplied password. Auto-provision a local
    user; password stored as a random bcrypt placeholder (never the LDAP pw).
  - OIDC: authorization-code flow with **state + nonce + PKCE (S256)** and
    **JWKS id_token signature verification** (pyjwt). Generic via the provider's
    `/.well-known/openid-configuration` discovery doc. Account-merge by email.
  - Role gate: reject login unless the user holds an allowed role (if configured).
  - Pharma twist: map an LDAP group / OIDC group-claim value → `dash_users.site_code`
    so SSO/LDAP users land bound to their branch (drives Shop-Counter mode).

Config = ENV (12-factor, secrets) merged with a single JSON override row in
`public.dash_admin_settings` (key='auth_config', scope='global') edited from the
super-admin Authentication tab. **Secrets (LDAP bind pw, OIDC client secrets)
live in ENV only — never written to the DB.**

Endpoints (all under /api/auth, registered as `fed_router`):
    GET  /methods                       — public; which methods are enabled (for the login page)
    POST /ldap/login                    — public; {username, password} → token
    GET  /oidc/{provider}/login         — public; 302 to the IdP
    GET  /oidc/{provider}/callback      — public; IdP redirect target → sets cookie, 302 to /ui
    GET  /config                        — super-admin; current (secret-redacted) auth config
    POST /config                        — super-admin; persist the JSON override row
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from os import getenv
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text
from starlette.responses import RedirectResponse

# Reuse the engine + helpers from the core auth module (single source of truth).
from app.auth import (  # noqa: E402
    _engine,
    _hash_password,
    _token_cache,
    SUPER_ADMIN,
    TOKEN_EXPIRY,
    get_current_user,
)

logger = logging.getLogger(__name__)

fed_router = APIRouter(prefix="/api/auth", tags=["Auth-Federated"])

# Where the IdP sends the browser back; the frontend serves under /ui.
_PUBLIC_URL = getenv("PUBLIC_URL", getenv("WEBUI_URL", "")).rstrip("/")
_SSO_COOKIE = "dash_sso"  # short-lived, JS-readable; login page moves it to localStorage then clears it

_CFG_CACHE: dict[str, Any] = {"t": 0.0, "v": None}
_CFG_TTL_S = 30.0


# ---------------------------------------------------------------------------
# Bootstrap: transient OAuth-flow store (state/nonce/PKCE, multi-worker safe)
# ---------------------------------------------------------------------------

def init_federation() -> None:
    """Idempotent. Create the transient oauth-flow table. Called from lifespan."""
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS public.dash_oauth_flow ("
                " state TEXT PRIMARY KEY,"
                " provider TEXT NOT NULL,"
                " nonce TEXT NOT NULL,"
                " code_verifier TEXT NOT NULL,"
                " redirect_uri TEXT NOT NULL,"
                " created_at BIGINT NOT NULL)"
            ))
            # opportunistic GC of stale flows (>15 min)
            conn.execute(text(
                "DELETE FROM public.dash_oauth_flow WHERE created_at < :cutoff"
            ), {"cutoff": int(time.time()) - 900})
    except Exception:
        logger.exception("init_federation: oauth-flow table bootstrap failed")


# ---------------------------------------------------------------------------
# Config — ENV defaults merged with the dash_admin_settings JSON override
# ---------------------------------------------------------------------------

def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _csv(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if not v:
        return []
    return [x.strip() for x in str(v).split(",") if x.strip()]


def _env_config() -> dict:
    """Baseline config from environment variables (OpenWebUI-style names)."""
    return {
        "local_enabled": _bool(getenv("LOCAL_AUTH_ENABLED"), True),
        "merge_by_email": _bool(getenv("OAUTH_MERGE_ACCOUNTS_BY_EMAIL"), True),
        "trusted_email_header": getenv("WEBUI_AUTH_TRUSTED_EMAIL_HEADER", ""),
        "ldap": {
            "enabled": _bool(getenv("ENABLE_LDAP")),
            "label": getenv("LDAP_SERVER_LABEL", "LDAP / Active Directory"),
            "host": getenv("LDAP_SERVER_HOST", ""),
            "port": int(getenv("LDAP_SERVER_PORT", "389") or 389),
            "use_tls": _bool(getenv("LDAP_USE_TLS")),
            "validate_cert": _bool(getenv("LDAP_VALIDATE_CERT"), True),
            "app_dn": getenv("LDAP_APP_DN", ""),
            "search_base": getenv("LDAP_SEARCH_BASE", ""),
            "uid_attr": getenv("LDAP_ATTRIBUTE_FOR_USERNAME", "uid"),
            "mail_attr": getenv("LDAP_ATTRIBUTE_FOR_MAIL", "mail"),
            "group_attr": getenv("LDAP_ATTRIBUTE_FOR_GROUPS", "memberOf"),
            "search_filter": getenv("LDAP_SEARCH_FILTER", ""),
            "group_to_site": {},  # {groupCN: site_code} — admin-tab only
        },
        # OIDC providers as a list; secrets resolved from env per-id at use time.
        "oidc": _env_oidc_providers(),
    }


def _env_oidc_providers() -> list[dict]:
    """Built-in single-provider convenience via env (generic OIDC + Google + MS).
    More providers are added via the admin tab JSON. id is a url-safe slug."""
    out: list[dict] = []
    if getenv("OPENID_PROVIDER_URL") and getenv("OAUTH_CLIENT_ID"):
        out.append({
            "id": "oidc",
            "name": getenv("OAUTH_PROVIDER_NAME", "SSO"),
            "issuer": getenv("OPENID_PROVIDER_URL", "").rstrip("/"),
            "client_id": getenv("OAUTH_CLIENT_ID", ""),
            "scopes": getenv("OAUTH_SCOPES", "openid email profile"),
            "email_claim": getenv("OAUTH_EMAIL_CLAIM", "email"),
            "username_claim": getenv("OAUTH_USERNAME_CLAIM", "preferred_username"),
            "roles_claim": getenv("OAUTH_ROLES_CLAIM", "roles"),
            "allowed_roles": _csv(getenv("OAUTH_ALLOWED_ROLES")),
            "groups_claim": getenv("OAUTH_GROUPS_CLAIM", "groups"),
            "group_to_site": {},
        })
    # Keycloak convenience (legacy env from the old hand-rolled integration)
    if getenv("KEYCLOAK_URL") and not any(p["id"] == "keycloak" for p in out):
        realm = getenv("KEYCLOAK_REALM", "dash")
        out.append({
            "id": "keycloak",
            "name": "Keycloak",
            "issuer": f"{getenv('KEYCLOAK_URL').rstrip('/')}/realms/{realm}",
            "client_id": getenv("KEYCLOAK_CLIENT_ID", "dash-app"),
            "scopes": "openid email profile",
            "email_claim": "email",
            "username_claim": "preferred_username",
            "roles_claim": "roles",
            "allowed_roles": [],
            "groups_claim": "groups",
            "group_to_site": {},
        })
    if getenv("GOOGLE_CLIENT_ID"):
        out.append({
            "id": "google", "name": "Google",
            "issuer": "https://accounts.google.com",
            "client_id": getenv("GOOGLE_CLIENT_ID", ""),
            "scopes": "openid email profile",
            "email_claim": "email", "username_claim": "email",
            "roles_claim": "", "allowed_roles": [], "groups_claim": "",
            "group_to_site": {},
        })
    if getenv("MICROSOFT_CLIENT_ID"):
        tenant = getenv("MICROSOFT_TENANT_ID", "common")
        out.append({
            "id": "microsoft", "name": "Microsoft",
            "issuer": f"https://login.microsoftonline.com/{tenant}/v2.0",
            "client_id": getenv("MICROSOFT_CLIENT_ID", ""),
            "scopes": "openid email profile",
            "email_claim": "email", "username_claim": "preferred_username",
            "roles_claim": "roles", "allowed_roles": [], "groups_claim": "groups",
            "group_to_site": {},
        })
    return out


def _read_override() -> dict:
    """The admin-tab JSON override row (non-secret fields only)."""
    try:
        with _engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM public.dash_admin_settings "
                "WHERE key = 'auth_config' AND scope = 'global' AND project_slug IS NULL"
            )).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception:
        logger.exception("auth_federation: reading auth_config override failed")
    return {}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_auth_config(fresh: bool = False) -> dict:
    """Env baseline merged with the DB override. Cached 30s. Secrets stay in env."""
    now = time.time()
    if not fresh and _CFG_CACHE["v"] is not None and (now - _CFG_CACHE["t"]) < _CFG_TTL_S:
        return _CFG_CACHE["v"]
    cfg = _deep_merge(_env_config(), _read_override())
    # OIDC override may be a full list (replace) — keep as-is if provided.
    _CFG_CACHE["t"] = now
    _CFG_CACHE["v"] = cfg
    return cfg


def _client_secret(provider_id: str) -> str:
    """Per-provider client secret from env (never stored in DB)."""
    if provider_id == "google":
        return getenv("GOOGLE_CLIENT_SECRET", "")
    if provider_id == "microsoft":
        return getenv("MICROSOFT_CLIENT_SECRET", "")
    if provider_id == "keycloak":
        return getenv("KEYCLOAK_CLIENT_SECRET", getenv("OAUTH_CLIENT_SECRET", ""))
    # generic: OIDC_<ID>_CLIENT_SECRET, fallback OAUTH_CLIENT_SECRET
    return getenv(f"OIDC_{provider_id.upper()}_CLIENT_SECRET", getenv("OAUTH_CLIENT_SECRET", ""))


def _find_provider(cfg: dict, provider_id: str) -> Optional[dict]:
    for p in cfg.get("oidc", []) or []:
        if p.get("id") == provider_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Shared user provisioning + token issuance (mirrors app/auth.py login)
# ---------------------------------------------------------------------------

def _map_site_and_role(values: list[str], group_to_site: dict, allowed_roles: list[str]) -> tuple[Optional[str], bool]:
    """Returns (site_code or None, allowed). `values` = role/group claim values.
    allowed=False ⇒ reject login (user holds none of the allowed roles)."""
    vals = set(values or [])
    allowed = True
    if allowed_roles:
        allowed = bool(vals & set(allowed_roles))
    site = None
    for grp, sc in (group_to_site or {}).items():
        if grp in vals:
            site = sc
            break
    return site, allowed


def _provision_and_issue(*, username: str, email: str, first: str, last: str,
                         provider: str, external_id: str, site_code: Optional[str],
                         merge_by_email: bool) -> dict:
    """Create-or-update the local user, then mint a dash token. Returns login payload."""
    if not username:
        raise HTTPException(400, "no username from identity provider")
    with _engine.begin() as conn:
        row = conn.execute(text(
            "SELECT id, username FROM public.dash_users WHERE username = :u"
        ), {"u": username}).fetchone()
        if not row and merge_by_email and email:
            row = conn.execute(text(
                "SELECT id, username FROM public.dash_users WHERE lower(email) = lower(:e)"
            ), {"e": email}).fetchone()

        if row:
            user_id, uname = row[0], row[1]
            conn.execute(text(
                "UPDATE public.dash_users SET email = COALESCE(NULLIF(:e,''), email),"
                " first_name = COALESCE(NULLIF(:fn,''), first_name),"
                " last_name = COALESCE(NULLIF(:ln,''), last_name),"
                " auth_provider = :prov, external_id = :ext,"
                " site_code = COALESCE(:sc, site_code), is_active = TRUE, last_login = NOW()"
                " WHERE id = :id"
            ), {"e": email, "fn": first, "ln": last, "prov": provider,
                "ext": external_id, "sc": site_code, "id": user_id})
        else:
            placeholder = _hash_password(secrets.token_urlsafe(32))
            ins = conn.execute(text(
                "INSERT INTO public.dash_users (username, password_hash, email, first_name,"
                " last_name, auth_provider, external_id, site_code, is_active, last_login)"
                " VALUES (:u, :p, :e, :fn, :ln, :prov, :ext, :sc, TRUE, NOW())"
                " RETURNING id"
            ), {"u": username, "p": placeholder, "e": email, "fn": first, "ln": last,
                "prov": provider, "ext": external_id, "sc": site_code})
            user_id = ins.fetchone()[0]
            uname = username

        token = secrets.token_urlsafe(32)
        expiry = int(time.time()) + TOKEN_EXPIRY
        conn.execute(text(
            "INSERT INTO public.dash_tokens (token, user_id, username, expiry)"
            " VALUES (:t, :uid, :u, :e)"
        ), {"t": token, "uid": user_id, "u": uname, "e": expiry})

    is_super = uname == SUPER_ADMIN
    _token_cache[token] = {"user_id": user_id, "username": uname, "expiry": expiry, "is_super": is_super}
    logger.info("federated login: %s via %s (site=%s)", uname, provider, site_code)
    return {"status": "ok", "token": token, "username": uname, "user_id": user_id, "is_super": is_super}


# ---------------------------------------------------------------------------
# LDAP (bind-search-bind)
# ---------------------------------------------------------------------------

class LdapLoginRequest(BaseModel):
    username: str
    password: str


@fed_router.post("/ldap/login")
def ldap_login(req: LdapLoginRequest):
    cfg = get_auth_config()
    lc = cfg.get("ldap", {})
    if not lc.get("enabled"):
        raise HTTPException(400, "LDAP not enabled")
    if not req.username or not req.password:
        raise HTTPException(400, "username and password required")

    try:
        import ldap3
        from ldap3.utils.conv import escape_filter_chars
    except ImportError:
        raise HTTPException(500, "ldap3 not installed")

    app_pw = getenv("LDAP_APP_PASSWORD", "")
    tls = None
    if lc.get("use_tls"):
        import ssl
        validate = ssl.CERT_REQUIRED if lc.get("validate_cert", True) else ssl.CERT_NONE
        tls = ldap3.Tls(validate=validate)
    server = ldap3.Server(lc["host"], port=int(lc.get("port", 389)),
                          use_ssl=bool(lc.get("use_tls")), tls=tls, get_info=ldap3.NONE)

    # 1) app-bind to search
    try:
        app_conn = ldap3.Connection(server, lc.get("app_dn") or None, app_pw or None,
                                    auto_bind=True, read_only=True)
    except Exception as e:
        logger.warning("LDAP app-bind failed: %s", e)
        raise HTTPException(500, "LDAP service bind failed")

    uid_attr = lc.get("uid_attr", "uid")
    mail_attr = lc.get("mail_attr", "mail")
    group_attr = lc.get("group_attr", "memberOf")
    safe_user = escape_filter_chars(req.username)
    user_filter = f"({uid_attr}={safe_user})"
    extra = (lc.get("search_filter") or "").strip()
    flt = f"(&{extra}{user_filter})" if extra else user_filter

    try:
        app_conn.search(lc["search_base"], flt,
                        attributes=[mail_attr, group_attr, "givenName", "sn", "cn"])
        entries = app_conn.entries
    finally:
        app_conn.unbind()
    if not entries:
        raise HTTPException(401, "Invalid LDAP credentials")
    entry = entries[0]
    user_dn = entry.entry_dn

    # 2) rebind as the user with the supplied password (the actual auth check)
    try:
        user_conn = ldap3.Connection(server, user_dn, req.password)
        if not user_conn.bind():
            raise HTTPException(401, "Invalid LDAP credentials")
        user_conn.unbind()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid LDAP credentials")

    def _attr(name: str) -> str:
        try:
            v = entry[name].value
            return (v[0] if isinstance(v, (list, tuple)) and v else v) or ""
        except Exception:
            return ""

    email = _attr(mail_attr)
    first = _attr("givenName")
    last = _attr("sn")
    # groups → list of CNs (memberOf returns full DNs; take the CN= component)
    groups: list[str] = []
    try:
        raw = entry[group_attr].values if group_attr in entry else []
        for g in raw or []:
            cn = str(g).split(",")[0]
            groups.append(cn.split("=", 1)[1] if "=" in cn else cn)
    except Exception:
        pass

    site, allowed = _map_site_and_role(groups, lc.get("group_to_site", {}),
                                       lc.get("allowed_groups", []))
    if not allowed:
        raise HTTPException(403, "Not authorized (LDAP group)")

    return _provision_and_issue(username=req.username, email=email, first=first, last=last,
                                provider="ldap", external_id=user_dn, site_code=site,
                                merge_by_email=bool(cfg.get("merge_by_email")))


# ---------------------------------------------------------------------------
# OIDC (authorization-code + PKCE + nonce + JWKS id_token verify)
# ---------------------------------------------------------------------------

_DISCO_CACHE: dict[str, tuple[float, dict]] = {}


def _discover(issuer: str) -> dict:
    now = time.time()
    hit = _DISCO_CACHE.get(issuer)
    if hit and (now - hit[0]) < 3600:
        return hit[1]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    doc = httpx.get(url, timeout=10).json()
    _DISCO_CACHE[issuer] = (now, doc)
    return doc


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _callback_uri(request: Request, provider_id: str) -> str:
    base = _PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/api/auth/oidc/{provider_id}/callback"


@fed_router.get("/oidc/{provider}/login")
def oidc_login(provider: str, request: Request):
    cfg = get_auth_config()
    p = _find_provider(cfg, provider)
    if not p:
        raise HTTPException(404, "Unknown OIDC provider")
    try:
        disco = _discover(p["issuer"])
    except Exception as e:
        raise HTTPException(502, f"OIDC discovery failed: {e}")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    redirect_uri = _callback_uri(request, provider)

    with _engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO public.dash_oauth_flow (state, provider, nonce, code_verifier,"
            " redirect_uri, created_at) VALUES (:s, :p, :n, :v, :r, :c)"
        ), {"s": state, "p": provider, "n": nonce, "v": verifier,
            "r": redirect_uri, "c": int(time.time())})

    from urllib.parse import urlencode
    params = {
        "client_id": p["client_id"],
        "response_type": "code",
        "scope": p.get("scopes", "openid email profile"),
        "redirect_uri": redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return RedirectResponse(url=f"{disco['authorization_endpoint']}?{urlencode(params)}")


@fed_router.get("/oidc/{provider}/callback")
def oidc_callback(provider: str, request: Request, code: str = "", state: str = "",
                  error: str = ""):
    if error:
        return RedirectResponse(url="/ui/login?sso_error=" + error)
    if not code or not state:
        raise HTTPException(400, "missing code/state")

    # consume the flow row (one-shot; validates CSRF state)
    with _engine.begin() as conn:
        row = conn.execute(text(
            "DELETE FROM public.dash_oauth_flow WHERE state = :s AND provider = :p"
            " RETURNING nonce, code_verifier, redirect_uri, created_at"
        ), {"s": state, "p": provider}).fetchone()
    if not row:
        raise HTTPException(400, "invalid or expired state")
    nonce, code_verifier, redirect_uri, created_at = row
    if int(time.time()) - int(created_at) > 900:
        raise HTTPException(400, "login flow expired")

    cfg = get_auth_config()
    p = _find_provider(cfg, provider)
    if not p:
        raise HTTPException(404, "Unknown OIDC provider")
    disco = _discover(p["issuer"])

    # exchange code → tokens (PKCE verifier + confidential client secret)
    try:
        tok = httpx.post(disco["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": p["client_id"],
            "client_secret": _client_secret(provider),
            "code_verifier": code_verifier,
        }, timeout=15).json()
    except Exception as e:
        raise HTTPException(502, f"token exchange failed: {e}")
    id_token = tok.get("id_token")
    if not id_token:
        raise HTTPException(400, tok.get("error_description", "no id_token returned"))

    # verify id_token signature against the provider JWKS (pyjwt)
    try:
        import jwt
        from jwt import PyJWKClient
        jwks = PyJWKClient(disco["jwks_uri"])
        signing_key = jwks.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token, signing_key,
            algorithms=["RS256", "RS384", "RS512", "ES256"],
            audience=p["client_id"],
            issuer=disco.get("issuer", p["issuer"]),
            options={"verify_aud": True},
        )
    except Exception as e:
        logger.warning("OIDC id_token verify failed: %s", e)
        raise HTTPException(401, "id_token verification failed")

    if claims.get("nonce") and claims["nonce"] != nonce:
        raise HTTPException(401, "nonce mismatch")

    email = str(claims.get(p.get("email_claim", "email"), "") or "")
    username = str(claims.get(p.get("username_claim", "preferred_username"), "")
                   or email or claims.get("sub", ""))
    first = str(claims.get("given_name", "") or "")
    last = str(claims.get("family_name", "") or "")

    role_vals = _csv(claims.get(p.get("roles_claim", "roles"))) if p.get("roles_claim") else []
    group_vals = _csv(claims.get(p.get("groups_claim", "groups"))) if p.get("groups_claim") else []
    site, allowed = _map_site_and_role(role_vals, p.get("group_to_site", {}),
                                       p.get("allowed_roles", []))
    if site is None and group_vals:
        site, _ = _map_site_and_role(group_vals, p.get("group_to_site", {}), [])
    if not allowed:
        return RedirectResponse(url="/ui/login?sso_error=not_authorized")

    payload = _provision_and_issue(
        username=username, email=email, first=first, last=last,
        provider=f"oidc:{provider}", external_id=str(claims.get("sub", "")),
        site_code=site, merge_by_email=bool(cfg.get("merge_by_email")),
    )

    # Hand the token to the SPA WITHOUT putting it in the URL (no access-log /
    # referrer leak): short-lived JS-readable cookie; login page moves it to
    # localStorage on mount, then clears it.
    resp = RedirectResponse(url="/ui/login?sso=1")
    resp.set_cookie(_SSO_COOKIE, payload["token"], max_age=120, httponly=False,
                    samesite="lax", path="/")
    return resp


# ---------------------------------------------------------------------------
# Public: which methods are available (login page reads this)
# ---------------------------------------------------------------------------

@fed_router.get("/methods")
def auth_methods():
    cfg = get_auth_config()
    return {
        "local": bool(cfg.get("local_enabled", True)),
        "ldap": bool(cfg.get("ldap", {}).get("enabled")),
        "ldap_label": cfg.get("ldap", {}).get("label", "LDAP"),
        "oidc": [{"id": p["id"], "name": p.get("name", p["id"])}
                 for p in (cfg.get("oidc", []) or []) if p.get("client_id") and p.get("issuer")],
        "trusted_header": bool(cfg.get("trusted_email_header")),
    }


# ---------------------------------------------------------------------------
# Super-admin: read/write the auth_config override (secrets redacted)
# ---------------------------------------------------------------------------

def _require_super(request: Request) -> dict:
    user = get_current_user(request)
    if not user or user.get("username") != SUPER_ADMIN:
        raise HTTPException(403, "Super admin only")
    return user


@fed_router.get("/config")
def get_config(request: Request):
    _require_super(request)
    cfg = get_auth_config(fresh=True)
    # secrets already live only in env; the JSON we return is safe to show.
    return {"config": cfg, "note": "Secrets (LDAP_APP_PASSWORD, *_CLIENT_SECRET) are env-only and never stored here."}


class AuthConfigRequest(BaseModel):
    config: dict


@fed_router.post("/config")
def set_config(req: AuthConfigRequest, request: Request):
    user = _require_super(request)
    # strip any secret-ish keys before persisting (defense in depth)
    cfg = json.loads(json.dumps(req.config))  # deep copy
    cfg.get("ldap", {}).pop("app_password", None)
    for p in cfg.get("oidc", []) or []:
        p.pop("client_secret", None)
    payload = json.dumps(cfg)
    try:
        with _engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO public.dash_admin_settings (key, value, value_type, scope, project_slug, updated_by)"
                " VALUES ('auth_config', :v, 'json', 'global', NULL, :uid)"
                " ON CONFLICT (key, scope, project_slug)"
                " DO UPDATE SET value = EXCLUDED.value, value_type = 'json', updated_by = EXCLUDED.updated_by, updated_at = NOW()"
            ), {"v": payload, "uid": user.get("user_id")})
    except Exception as e:
        raise HTTPException(500, f"save failed: {e}")
    _CFG_CACHE["v"] = None  # invalidate
    return {"status": "ok"}
