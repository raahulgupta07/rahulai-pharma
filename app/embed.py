"""Agent Embed CRUD API — Phase 1.

Endpoints under /api/projects/{slug}/embeds for managing embeddable agent
widget configs. Mirrors the auth/access pattern in app.learning.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from dash.embed import manager as embed_mgr
from dash.embed import rls_explainer as _rls_explainer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["Embed"])


@router.get("/{slug}/embeds/{embed_id}/sessions")
def embed_sessions(slug: str, embed_id: str, request: Request, limit: int = 50):
    """Recent sessions for this embed. Limit 50 by default."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)
    eng = _get_engine()
    with eng.connect() as conn:
        # Cross-project guard
        own = conn.execute(text(
            "SELECT 1 FROM public.dash_agent_embeds WHERE embed_id = :e AND project_slug = :s"
        ), {"e": embed_id, "s": slug}).first()
        if not own:
            raise HTTPException(404, "embed not found")
        rows = conn.execute(text(
            "SELECT session_token, external_user, origin, ip, request_count, "
            " created_at, expires_at, expires_at < NOW() AS expired, revoked "
            "FROM public.dash_embed_sessions "
            "WHERE embed_id = :e ORDER BY created_at DESC LIMIT :n"
        ), {"e": embed_id, "n": min(int(limit), 200)}).mappings().all()
    return {"sessions": [dict(r) for r in rows]}


@router.get("/{slug}/embeds/{embed_id}/usage")
def embed_usage(slug: str, embed_id: str, request: Request, days: int = 14):
    """Aggregated usage metrics (daily counts, latency p50/p95, top users, origin mix)."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)

    eng = _get_engine()
    with eng.connect() as conn:
        own = conn.execute(text(
            "SELECT 1 FROM public.dash_agent_embeds WHERE embed_id = :e AND project_slug = :s"
        ), {"e": embed_id, "s": slug}).first()
        if not own:
            raise HTTPException(404, "embed not found")

        d = max(1, min(int(days), 365))
        window = f"{d} days"

        daily = conn.execute(text(f"""
            SELECT DATE_TRUNC('day', ts) AS day,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS errors,
                   PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
                   PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                   AVG(message_chars)::int  AS avg_msg,
                   AVG(response_chars)::int AS avg_resp
            FROM public.dash_embed_calls
            WHERE embed_id = :e AND ts >= NOW() - INTERVAL '{window}'
            GROUP BY DATE_TRUNC('day', ts) ORDER BY day
        """), {"e": embed_id}).mappings().all()

        totals = conn.execute(text(f"""
            SELECT COUNT(*) AS calls,
                   SUM(CASE WHEN NOT success THEN 1 ELSE 0 END) AS errors,
                   PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
                   PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
                   COUNT(DISTINCT external_user) AS uniq_users,
                   COUNT(DISTINCT session_token) AS uniq_sessions
            FROM public.dash_embed_calls
            WHERE embed_id = :e AND ts >= NOW() - INTERVAL '{window}'
        """), {"e": embed_id}).mappings().first()

        top_users = conn.execute(text(f"""
            SELECT external_user AS user, COUNT(*) AS calls
            FROM public.dash_embed_calls
            WHERE embed_id = :e AND ts >= NOW() - INTERVAL '{window}'
              AND external_user IS NOT NULL
            GROUP BY external_user ORDER BY calls DESC LIMIT 10
        """), {"e": embed_id}).mappings().all()

        origins = conn.execute(text(f"""
            SELECT COALESCE(NULLIF(origin,''), '(unknown)') AS origin, COUNT(*) AS calls
            FROM public.dash_embed_calls
            WHERE embed_id = :e AND ts >= NOW() - INTERVAL '{window}'
            GROUP BY origin ORDER BY calls DESC LIMIT 10
        """), {"e": embed_id}).mappings().all()

    return {
        "days": d,
        "totals": dict(totals) if totals else {},
        "daily": [dict(r) for r in daily],
        "top_users": [dict(r) for r in top_users],
        "origins": [dict(r) for r in origins],
    }


@router.get("/{slug}/embeds/{embed_id}/sandbox")
def embed_sandbox(slug: str, embed_id: str, request: Request, token: str | None = None):
    """Serve a self-contained sandbox HTML page for testing this embed live.

    Authenticated admin endpoint — server-side computes HMAC sample using the
    actual secret_key so admin can test HMAC-mode embeds without leaking the secret.
    Accepts auth via Authorization header OR ?token= query param (for new-tab open).
    """
    from fastapi.responses import HTMLResponse
    import json as _json
    from sqlalchemy import text
    from dash.embed import _get_engine, hmac_user
    from app.auth import validate_token

    # Try header first, then query param fallback (so window.open with ?token= works).
    user = _get_user(request) if request.headers.get("Authorization") else None
    if not user and token:
        user = validate_token(token)
    if not user:
        return HTMLResponse("<h1>401 — auth required (pass ?token= or Authorization header)</h1>", status_code=401)

    _check_access(user, slug)

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT embed_id, project_slug, public_key, secret_key, name, "
            " allowed_origins, user_id_required, auth_mode, rate_limit_per_min, enabled "
            "FROM public.dash_agent_embeds WHERE embed_id = :e AND project_slug = :s"
        ), {"e": embed_id, "s": slug}).mappings().first()
    if not row:
        return HTMLResponse("<h1>404 — embed not found</h1>", status_code=404)
    if not row["enabled"]:
        return HTMLResponse("<h1>Embed disabled</h1>", status_code=403)

    # Pre-compute a sample HMAC for default user payload so admin can test
    # without exposing secret_key in the page.
    sample_user = {"id": "test-user-1", "store_id": "DEMO01"}
    sample_sig = ""
    if row["auth_mode"] == "hmac" and row["secret_key"]:
        sample_sig = hmac_user(row["secret_key"], sample_user)

    origin_hint = (row["allowed_origins"] or [""])[0] if row["allowed_origins"] else ""
    base_url = str(request.base_url).rstrip("/")  # e.g. https://localhost

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<title>Sandbox — {row['name'] or row['embed_id']}</title>
<style>
  body {{ font-family: ui-monospace, Menlo, monospace; background: #0a0a0a; color: #e5e5e0; margin: 0; padding: 20px; }}
  h1 {{ color: #00fc40; font-size: 16px; }}
  h2 {{ color: #66aaff; font-size: 13px; margin-top: 18px; }}
  pre {{ background: #1a1a1a; padding: 10px; overflow-x: auto; font-size: 11px; line-height: 1.5; border-left: 2px solid #333; }}
  .ok {{ color: #00fc40; }}
  .warn {{ color: #ff9d00; }}
  .err {{ color: #ff4040; }}
  .row {{ display: flex; gap: 16px; margin: 12px 0; }}
  .row > div {{ flex: 1; }}
  label {{ font-size: 10px; color: #888; display: block; margin-bottom: 4px; text-transform: uppercase; }}
  input, select {{ width: 100%; padding: 6px 10px; background: #1a1a1a; border: 1px solid #333; color: #00fc40; font-family: inherit; font-size: 11px; outline: none; }}
  input:focus {{ border-color: #00fc40; }}
  button {{ padding: 6px 14px; background: #00fc40; border: none; color: #000; cursor: pointer; font-family: inherit; font-size: 11px; font-weight: 700; }}
  .console {{ background: #000; padding: 10px; max-height: 320px; overflow-y: auto; font-size: 11px; line-height: 1.5; }}
  .console-line {{ padding: 2px 0; border-bottom: 1px dotted #1a1a1a; }}
  .badge {{ display: inline-block; padding: 1px 6px; font-size: 9px; font-weight: 700; }}
</style>
</head><body>

<h1>◉ Embed Sandbox — {row['name'] or row['embed_id']}</h1>
<div style="font-size:11px; color:#888; margin-bottom:14px;">
  embed_id: <code style="color:#00fc40;">{row['embed_id']}</code> ·
  auth: <span class="badge" style="background:#1a1a1a; color:{'#00fc40' if row['auth_mode']=='public' else '#ff9d00' if row['auth_mode']=='hmac' else '#cc99ff'};">{row['auth_mode']}</span> ·
  rate: {row['rate_limit_per_min']}/min ·
  origins: <code style="color:#aaa;">{', '.join(row['allowed_origins'] or []) or '(none)'}</code>
</div>

<h2>SAMPLE USER (HMAC mode pre-signed by server)</h2>
<div class="row">
  <div>
    <label>USER PAYLOAD (JSON)</label>
    <input id="userJson" value='{_json.dumps(sample_user)}' />
  </div>
  <div style="max-width:120px;">
    <label>HMAC SIG</label>
    <input id="userSig" value="{sample_sig}" {'disabled' if not sample_sig else ''} />
  </div>
  <div style="max-width:140px;">
    <label>&nbsp;</label>
    <button onclick="reloadWidget()">RELOAD WIDGET</button>
  </div>
</div>

<h2>NETWORK CONSOLE</h2>
<div class="console" id="console"></div>

<h2>WIDGET PREVIEW</h2>
<div style="position:relative; min-height:120px; padding:14px; background:#0d0d0d; border:1px dashed #333;">
  <div style="font-size:10px; color:#888;">The widget bubble appears in the lower-right of THIS page (since this sandbox is the host site).</div>
</div>

<script>
(function() {{
  const cons = document.getElementById('console');
  function log(msg, cls) {{
    const t = new Date().toLocaleTimeString();
    cons.innerHTML += '<div class="console-line"><span style="color:#666;">' + t + '</span> <span class="' + (cls||'') + '">' + msg + '</span></div>';
    cons.scrollTop = cons.scrollHeight;
  }}

  // Intercept fetch so we can log every embed call.
  const _origFetch = window.fetch;
  window.fetch = function(input, init) {{
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/api/embed/')) {{
      log('→ ' + (init?.method || 'GET') + ' ' + url, 'warn');
      if (init?.body) log('  body: ' + init.body, '');
    }}
    return _origFetch.apply(this, arguments).then(function(r) {{
      if (url.includes('/api/embed/')) {{
        log('← HTTP ' + r.status + ' ' + url, r.ok ? 'ok' : 'err');
      }}
      return r;
    }});
  }};

  function injectWidget() {{
    log('injecting widget…', 'ok');
    // Remove any existing
    const old = document.getElementById('dash-agent-embed');
    if (old) old.remove();
    const oldScr = document.getElementById('dash-embed-script');
    if (oldScr) oldScr.remove();

    const userJson = document.getElementById('userJson').value;
    const userSig  = document.getElementById('userSig').value;

    const s = document.createElement('script');
    s.id = 'dash-embed-script';
    s.src = '{base_url}/api/embed/widget.js';
    s.async = true;
    s.setAttribute('data-embed-id', '{row["embed_id"]}');
    s.setAttribute('data-key', '{row["public_key"]}');
    if (userJson) s.setAttribute('data-user', userJson);
    if (userSig) s.setAttribute('data-user-sig', userSig);
    s.setAttribute('data-position', 'bottom-right');
    s.setAttribute('data-theme', 'dark');
    s.setAttribute('data-greeting', 'Hi! This is sandbox mode. Try a question…');
    s.setAttribute('data-title', 'Sandbox');
    document.body.appendChild(s);
  }}

  window.reloadWidget = injectWidget;
  injectWidget();
}})();
</script>
</body></html>"""

    return HTMLResponse(html, headers={"X-Frame-Options": "SAMEORIGIN"})


def _get_user(request: Request) -> dict:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _check_access(user: dict, slug: str) -> None:
    """Mirror app.learning._check_access — owner / shared / super admin only."""
    from app.auth import check_project_permission
    perm = check_project_permission(user, slug)
    if not perm:
        raise HTTPException(403, "Access denied")


def _load_embed_or_404(embed_id: str, slug: str) -> dict:
    emb = embed_mgr.get_embed(embed_id)
    if not emb:
        raise HTTPException(404, "Embed not found")
    if emb.get("project_slug") != slug:
        # Don't leak existence of embeds in other projects.
        raise HTTPException(404, "Embed not found")
    return emb


@router.post("/{slug}/embeds")
async def create_embed_endpoint(slug: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    body = await request.json()

    name = body.get("name") or None
    allowed_origins = body.get("allowed_origins") or []
    if not isinstance(allowed_origins, list):
        raise HTTPException(400, "allowed_origins must be a list")
    auth_mode = body.get("auth_mode", "hmac")
    if auth_mode not in ("public", "hmac", "jwt"):
        raise HTTPException(400, "auth_mode must be one of: public, hmac, jwt")
    rate_limit = int(body.get("rate_limit_per_min", 30) or 30)
    feature_config = body.get("feature_config")
    if feature_config is not None and not isinstance(feature_config, dict):
        raise HTTPException(400, "feature_config must be an object")

    bound_scope_id = body.get("bound_scope_id") or None
    bound_intent = body.get("bound_intent") or "public"
    if bound_intent not in ("private", "network", "public"):
        raise HTTPException(400, "bound_intent must be one of: private, network, public")
    bound_role = body.get("bound_role") or None

    try:
        embed = embed_mgr.create_embed(
            project_slug=slug,
            name=name,
            allowed_origins=allowed_origins,
            user_id_required=bool(body.get("user_id_required", False)),
            user_id_signed=bool(body.get("user_id_signed", True)),
            auth_mode=auth_mode,
            jwt_jwks_url=body.get("jwt_jwks_url"),
            rate_limit_per_min=rate_limit,
            feature_config=feature_config,
            created_by=user.get("user_id"),
            bound_scope_id=bound_scope_id,
            bound_intent=bound_intent,
            bound_role=bound_role,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("create_embed failed for %s", slug)
        raise HTTPException(500, "Failed to create embed")

    return {
        "status": "ok",
        "warning": "secret_key is shown ONCE — store it securely now",
        "embed": embed,
    }


@router.get("/{slug}/embeds")
def list_embeds_endpoint(slug: str, request: Request, include_agent_scoped: bool = False):
    user = _get_user(request)
    _check_access(user, slug)
    embeds = embed_mgr.list_embeds(slug)
    if not include_agent_scoped:
        # Hide legacy per-agent auto-provisioned rows. Keep project-level default
        # (auto_provisioned=true AND agent_id IS NULL) and manual customs.
        embeds = [e for e in embeds if not e.get("agent_id")]
    return {"status": "ok", "embeds": embeds}


@router.post("/{slug}/embeds/prune-legacy")
def prune_legacy_agent_embeds_endpoint(slug: str, request: Request):
    """One-shot cleanup: delete legacy per-agent auto-provisioned embeds.

    Removes rows where agent_id IS NOT NULL AND auto_provisioned = TRUE for
    this project. Not auto-invoked anywhere — call manually if desired.
    """
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)
    eng = _get_engine()
    with eng.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM public.dash_agent_embeds "
                "WHERE project_slug = :s AND agent_id IS NOT NULL "
                "AND auto_provisioned = TRUE"
            ),
            {"s": slug},
        )
        deleted = res.rowcount or 0
    return {"status": "ok", "deleted": deleted}


@router.get("/{slug}/embeds/{embed_id}")
def get_embed_endpoint(slug: str, embed_id: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    emb = _load_embed_or_404(embed_id, slug)
    return {"status": "ok", "embed": emb}


@router.patch("/{slug}/embeds/{embed_id}")
async def update_embed_endpoint(slug: str, embed_id: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)
    body = await request.json()

    allowed_keys = {
        "name",
        "allowed_origins",
        "user_id_required",
        "user_id_signed",
        "auth_mode",
        "jwt_jwks_url",
        "rate_limit_per_min",
        "feature_config",
        "enabled",
        "bound_scope_id",
        "bound_intent",
        "bound_role",
        # per-agent theme (migration 062)
        "primary_color",
        "logo_url",
        "welcome_msg",
        "position",
        "theme",
        "faq_mode",
        "status",
        # consumer mode + sandbox access (migration 063)
        "response_style",
        "access_mode",
        "test_ip_allowlist",
        "max_reply_chars",
        # RLS (migration 064) — names match DB columns, no aliasing needed
        "rls_enabled",
        "rls_claims",
        "rls_policies",
        "rls_claim_source",
    }
    # Alias frontend field names → canonical
    _ALIASES = {"answer_style": "response_style", "test_access_mode": "access_mode"}
    body = {(_ALIASES.get(k, k)): v for k, v in body.items()}

    fields = {k: v for k, v in body.items() if k in allowed_keys}

    if "allowed_origins" in fields and not isinstance(fields["allowed_origins"], list):
        raise HTTPException(400, "allowed_origins must be a list")
    if "feature_config" in fields and fields["feature_config"] is not None and not isinstance(fields["feature_config"], dict):
        raise HTTPException(400, "feature_config must be an object")
    if "bound_intent" in fields and fields["bound_intent"] not in (None, "private", "network", "public"):
        raise HTTPException(400, "bound_intent must be one of: private, network, public")

    try:
        emb = embed_mgr.update_embed(embed_id, **fields)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    except Exception:
        logger.exception("update_embed failed for %s/%s", slug, embed_id)
        raise HTTPException(500, "Failed to update embed")

    return {"status": "ok", "embed": emb}


# Embed logo storage — persisted volume (knowledge_data), served publicly by
# app/embed_public.py at GET /api/embed/logo/{file} so the widget can load it
# from any external origin.
_EMBED_LOGO_DIR = "/app/knowledge/embed_logos"
_LOGO_EXT = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
    "image/svg+xml": "svg", "image/gif": "gif",
}
_LOGO_MAX_BYTES = 1024 * 1024  # 1MB


@router.post("/{slug}/embeds/{embed_id}/logo")
async def upload_embed_logo(slug: str, embed_id: str, request: Request):
    """Upload a logo image for an embed. Saves to the persisted volume, sets the
    embed's logo_url to the public served URL, returns it. Super-admin only."""
    import os
    import re
    from pathlib import Path
    from fastapi import UploadFile

    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)

    form = await request.form()
    file = form.get("file")
    if not isinstance(file, UploadFile):
        raise HTTPException(400, "no file uploaded (multipart field 'file')")
    ext = _LOGO_EXT.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "unsupported image type — use png, jpg, webp, svg, or gif")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > _LOGO_MAX_BYTES:
        raise HTTPException(400, "logo too large (max 1MB)")

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", embed_id)[:64] or "embed"
    fname = f"{safe_id}.{ext}"
    path = Path(_EMBED_LOGO_DIR) / fname
    path.parent.mkdir(parents=True, exist_ok=True)
    # remove any stale variant with a different extension for this embed
    try:
        for old in path.parent.glob(f"{safe_id}.*"):
            if old.name != fname:
                old.unlink()
    except Exception:
        pass
    path.write_bytes(data)

    base = (os.getenv("PUBLIC_URL") or os.getenv("WEBUI_URL") or str(request.base_url)).rstrip("/")
    # cache-bust so a re-upload of the same filename refreshes in browsers
    logo_url = f"{base}/api/embed/logo/{fname}?v={len(data)}"
    try:
        embed_mgr.update_embed(embed_id, logo_url=logo_url)
    except Exception:
        logger.exception("upload_embed_logo: persist logo_url failed for %s/%s", slug, embed_id)
        raise HTTPException(500, "saved file but failed to update embed")
    return {"status": "ok", "logo_url": logo_url}


@router.delete("/{slug}/embeds/{embed_id}")
def delete_embed_endpoint(slug: str, embed_id: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)
    ok = embed_mgr.delete_embed(embed_id)
    if not ok:
        raise HTTPException(404, "Embed not found")
    return {"status": "ok"}


@router.post("/{slug}/embeds/{embed_id}/rotate")
def rotate_embed_secret_endpoint(slug: str, embed_id: str, request: Request):
    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)
    try:
        secret_key = embed_mgr.rotate_secret(embed_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "status": "ok",
        "warning": "secret_key is shown ONCE — store it securely now",
        "secret_key": secret_key,
    }


@router.post("/{slug}/embeds/{embed_id}/reprovision-secret")
def reprovision_embed_secret_endpoint(slug: str, embed_id: str, request: Request):
    """Super-admin only. Rotates secret for HMAC embeds that are missing the
    encrypted column (i.e. broken because plaintext was never stored either).

    Returns plaintext secret ONCE — caller must persist it.
    """
    from app.auth import _require_super
    from sqlalchemy import text
    from dash.embed import _get_engine

    _require_super(request)
    _load_embed_or_404(embed_id, slug)

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT auth_mode, secret_key_encrypted "
            "FROM public.dash_agent_embeds "
            "WHERE embed_id = :e AND project_slug = :s"
        ), {"e": embed_id, "s": slug}).first()
    if not row:
        raise HTTPException(404, "embed not found")
    auth_mode = (row[0] or "").lower()
    secret_enc = row[1]
    if auth_mode != "hmac":
        raise HTTPException(400, "reprovision only applies to HMAC embeds")
    if secret_enc:
        raise HTTPException(
            409, "embed already has an encrypted secret; use /rotate to replace it"
        )

    try:
        secret_key = embed_mgr.rotate_secret(embed_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {
        "status": "ok",
        "warning": "secret_key is shown ONCE — store it securely now",
        "secret_key": secret_key,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-agent default embed — auto-provisioned, surfaced inline on agent rows
# ─────────────────────────────────────────────────────────────────────────────

def auto_provision_agent_embed(
    project_slug: str,
    agent_id: str,
    agent_name: str | None = None,
    created_by: int | None = None,
) -> dict | None:
    """Create an auto-provisioned embed for an agent if one doesn't exist.

    Idempotent. Returns the existing or newly-created embed (without secret).
    Safe to call from agent registration paths — failures are logged + None.
    """
    from sqlalchemy import text
    from dash.embed import _get_engine
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            existing = conn.execute(text(
                "SELECT embed_id FROM public.dash_agent_embeds "
                "WHERE project_slug = :s AND agent_id = :a AND auto_provisioned = TRUE"
            ), {"s": project_slug, "a": agent_id}).first()
            if existing:
                return {"embed_id": existing[0], "agent_id": agent_id, "created": False}

        emb = embed_mgr.create_embed(
            project_slug=project_slug,
            name=(agent_name or agent_id) + " (default)",
            allowed_origins=[],
            auth_mode="public",
            rate_limit_per_min=30,
            feature_config={"agents": {agent_id: True}},
            created_by=created_by,
        )

        # Mark as auto-provisioned + agent-scoped + draft (no origins yet).
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_agent_embeds SET "
                " agent_id = :a, auto_provisioned = TRUE, status = 'draft' "
                "WHERE embed_id = :e"
            ), {"a": agent_id, "e": emb["embed_id"]})
            conn.commit()
        emb["agent_id"] = agent_id
        emb["auto_provisioned"] = True
        emb["status"] = "draft"
        emb["created"] = True
        return emb
    except Exception:
        logger.exception("auto_provision_agent_embed failed slug=%s agent=%s", project_slug, agent_id)
        return None


@router.get("/{slug}/embeds/by-agent/{agent_id}")
def get_embed_by_agent(slug: str, agent_id: str, request: Request):
    """Fetch (or auto-create) the default embed for an agent. Used by the
    inline `</> Embed` panel on each agent row."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM public.dash_agent_embeds "
            "WHERE project_slug = :s AND agent_id = :a AND auto_provisioned = TRUE "
            "LIMIT 1"
        ), {"s": slug, "a": agent_id}).first()

    if not row:
        emb = auto_provision_agent_embed(slug, agent_id, agent_id, user.get("user_id"))
        if not emb:
            raise HTTPException(500, "Failed to auto-provision embed")
        emb_id = emb["embed_id"]
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": emb_id}).first()

    out = dict(row._mapping)
    out.pop("secret_key_hash", None)
    out.pop("secret_key", None)  # never echo on read
    for k in ("created_at", "last_used_at"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return {"status": "ok", "embed": out}


@router.post("/{slug}/embeds/backfill")
def backfill_agent_embeds(slug: str, request: Request):
    """Ensure one project-level auto embed exists (primary consumer surface),
    then create per-agent auto embeds as a legacy fallback. Idempotent."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)

    # 1) Project-level embed (primary). This is what marketing/consumer users embed.
    project_name = slug
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT name FROM public.dash_projects WHERE slug = :s"
            ), {"s": slug}).first()
            if row and row[0]:
                project_name = row[0]
    except Exception:
        pass
    project_embed = auto_provision_project_embed(slug, project_name, user.get("user_id"))
    project_created = bool(project_embed and project_embed.get("created"))

    # 2) Per-agent embeds (legacy).
    try:
        from app.learning import _list_project_agents  # type: ignore
        agents = _list_project_agents(slug) or []
    except Exception:
        agents = []
    if not agents:
        # Fallback: hardcoded core team if introspection fails.
        agents = [
            {"id": "analyst", "name": "Analyst"},
            {"id": "engineer", "name": "Engineer"},
            {"id": "researcher", "name": "Researcher"},
            {"id": "customer_strategist", "name": "Customer Strategist"},
        ]

    created = 0
    for a in agents:
        aid = a.get("id") or a.get("agent_id") or a.get("name", "").lower().replace(" ", "_")
        if not aid:
            continue
        res = auto_provision_agent_embed(slug, aid, a.get("name"), user.get("user_id"))
        if res and res.get("created"):
            created += 1

    return {
        "status": "ok",
        "project_embed_id": (project_embed or {}).get("embed_id"),
        "project_embed_created": project_created,
        "agents_total": len(agents),
        "created": created,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-PROJECT default embed — one consumer-facing widget per project
# ─────────────────────────────────────────────────────────────────────────────

def auto_provision_project_embed(
    project_slug: str,
    project_name: str | None = None,
    created_by: int | None = None,
) -> dict | None:
    """Create the auto-provisioned project-level embed if one doesn't exist.

    Same SELECT-first + INSERT-then-UPDATE pattern as `auto_provision_agent_embed`
    but with `agent_id=NULL` and consumer-friendly defaults. Idempotent.
    Returns dict (without secret) or None on hard failure.
    """
    from sqlalchemy import text
    from dash.embed import _get_engine
    try:
        eng = _get_engine()
        with eng.connect() as conn:
            existing = conn.execute(text(
                "SELECT embed_id FROM public.dash_agent_embeds "
                "WHERE project_slug = :s AND agent_id IS NULL AND auto_provisioned = TRUE "
                "LIMIT 1"
            ), {"s": project_slug}).first()
            if existing:
                return {"embed_id": existing[0], "created": False}

        name = f"{project_name or project_slug} (Assistant)"
        emb = embed_mgr.create_embed(
            project_slug=project_slug,
            name=name,
            allowed_origins=[],
            auth_mode="public",
            rate_limit_per_min=30,
            feature_config=None,
            created_by=created_by,
        )

        # Mark as project-level auto embed with consumer defaults.
        with eng.connect() as conn:
            conn.execute(text(
                "UPDATE public.dash_agent_embeds SET "
                " agent_id = NULL, "
                " auto_provisioned = TRUE, "
                " status = 'draft', "
                " response_style = 'consumer', "
                " access_mode = 'public', "
                " welcome_msg = COALESCE(welcome_msg, 'Hi! Ask me anything.'), "
                " max_reply_chars = COALESCE(max_reply_chars, 600) "
                "WHERE embed_id = :e"
            ), {"e": emb["embed_id"]})
            conn.commit()
        emb["auto_provisioned"] = True
        emb["status"] = "draft"
        emb["response_style"] = "consumer"
        emb["access_mode"] = "public"
        emb["created"] = True
        return emb
    except Exception:
        logger.exception("auto_provision_project_embed failed slug=%s", project_slug)
        return None


@router.get("/{slug}/embeds/project-default")
def get_project_default_embed(slug: str, request: Request):
    """Fetch (or auto-create) the project-level default consumer embed.

    This is the primary surface — ONE embed per project, agent_id NULL,
    `response_style='consumer'`."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)

    eng = _get_engine()
    project_name = slug
    with eng.connect() as conn:
        prow = conn.execute(text(
            "SELECT name FROM public.dash_projects WHERE slug = :s"
        ), {"s": slug}).first()
        if prow and prow[0]:
            project_name = prow[0]
        row = conn.execute(text(
            "SELECT * FROM public.dash_agent_embeds "
            "WHERE project_slug = :s AND agent_id IS NULL AND auto_provisioned = TRUE "
            "LIMIT 1"
        ), {"s": slug}).first()

    if not row:
        emb = auto_provision_project_embed(slug, project_name, user.get("user_id"))
        if not emb:
            raise HTTPException(500, "Failed to auto-provision project embed")
        emb_id = emb["embed_id"]
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM public.dash_agent_embeds WHERE embed_id = :e"
            ), {"e": emb_id}).first()

    out = dict(row._mapping)
    out.pop("secret_key_hash", None)
    out.pop("secret_key", None)
    for k in ("created_at", "last_used_at"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return {"status": "ok", "embed": out}


@router.post("/{slug}/embeds/{embed_id}/test-token")
async def gen_test_token(slug: str, embed_id: str, request: Request):
    """Generate a short-lived signed token to share a live sandbox URL with
    stakeholders. Token verifies via HMAC of (embed_id|nonce|exp) against the
    embed's secret_key_hash."""
    import base64
    import hashlib
    import hmac as _hmac
    import json as _json
    import secrets
    import time as _time

    from sqlalchemy import text
    from dash.embed import _get_engine

    user = _get_user(request)
    _check_access(user, slug)

    ttl_seconds = 900
    claims_payload: dict | None = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            if body.get("ttl_seconds"):
                ttl_seconds = max(60, min(int(body["ttl_seconds"]), 86400))
            if isinstance(body.get("claims"), dict):
                claims_payload = body["claims"]
    except Exception:
        pass

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT secret_key_hash FROM public.dash_agent_embeds "
            "WHERE embed_id = :e AND project_slug = :s"
        ), {"e": embed_id, "s": slug}).first()
    if not row:
        raise HTTPException(404, "embed not found")
    secret_hash = row[0] or ""
    if not secret_hash:
        raise HTTPException(409, "embed has no secret_key_hash; rotate first")

    exp = int(_time.time()) + ttl_seconds
    nonce = secrets.token_urlsafe(24)
    # Sign claims into msg too so they can't be tampered with in transit.
    claims_canon = _json.dumps(claims_payload, sort_keys=True, separators=(",", ":")) if claims_payload else ""
    msg = f"{embed_id}|{nonce}|{exp}|{claims_canon}".encode("utf-8")
    sig = _hmac.new(secret_hash.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    payload: dict = {"embed_id": embed_id, "exp": exp, "nonce": nonce, "sig": sig}
    if claims_payload:
        payload["claims"] = claims_payload
    token = base64.urlsafe_b64encode(_json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    base_url = str(request.base_url).rstrip("/")
    url = f"{base_url}/api/embed/try/{embed_id}?token={token}"
    # Append claim_* query params so sandbox can bake them into iframe widget.
    if claims_payload:
        from urllib.parse import urlencode
        url += "&" + urlencode({f"claim_{k}": v for k, v in claims_payload.items()})
    return {
        "token": token,
        "expires_at": exp,
        "ttl_seconds": ttl_seconds,
        "claims": claims_payload,
        "url": url,
    }


@router.get("/{slug}/embeds/{embed_id}/rls")
def get_embed_rls(slug: str, embed_id: str, request: Request):
    """Return the RLS config (claim defs + policies + source) for the
    settings UI to display. Falls back to raw columns if sibling
    dash/embed/rls.py loader unavailable."""
    from sqlalchemy import text
    from dash.embed import _get_engine
    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)

    # Prefer the sibling loader so claim defs are normalized consistently.
    try:
        from dash.embed.rls import load_rls_for_embed  # type: ignore
        cfg = load_rls_for_embed(embed_id) or {}
        if cfg:
            return {"status": "ok", "rls": cfg}
    except ImportError:
        pass
    except Exception:
        logger.exception("load_rls_for_embed failed; falling back to raw row")

    eng = _get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SELECT rls_enabled, rls_claims, rls_policies, rls_claim_source "
            "FROM public.dash_agent_embeds WHERE embed_id = :e"
        ), {"e": embed_id}).first()
    if not row:
        raise HTTPException(404, "embed not found")
    return {
        "status": "ok",
        "rls": {
            "rls_enabled": bool(row[0]),
            "rls_claims": row[1] or [],
            "rls_policies": row[2] or [],
            "rls_claim_source": row[3] or "token",
        },
    }


# ---------------------------------------------------------------------------
# Schema catalog (for RLS policy UI dropdowns)
# ---------------------------------------------------------------------------
import re as _re
import time as _time

# 5-minute in-memory cache: project_slug -> (timestamp, payload)
_SCHEMA_CATALOG_CACHE: dict[str, tuple[float, dict]] = {}
_SCHEMA_CATALOG_TTL = 300  # seconds
_MAX_TABLES = 200
_MAX_COLS_PER_TABLE = 100

# Patterns for smart suggestions
_CLAIM_NAMED = {
    "store_id", "tenant_id", "customer_id", "user_id",
    "account_id", "org_id", "region", "branch_id", "location_id",
}
_INTEGER_TYPES = {"integer", "bigint", "smallint", "int", "int2", "int4", "int8", "serial", "bigserial"}

_RE_PRIVATE_QTY = _re.compile(r"(qty|stock|inventory|quantity|count|on_hand|balance|available)", _re.I)
# Likely-claim column names that indicate a per-row scoping identifier exists in the table
_CLAIM_COL_HINTS = ("store_id", "site_id", "site_code", "tenant_id", "branch_id", "location_id", "shop_id", "outlet_id")
_RE_PRIVATE_MONEY = _re.compile(
    r"(price|cost|revenue|amount|salary|wage|margin|profit|discount|commission)", _re.I
)
_RE_HIDDEN = _re.compile(
    r"(password|secret|token|api_key|ssn|tax_id|credit_card|cvv|pin|hash)", _re.I
)
_RE_REDACTED = _re.compile(r"(email|phone|address|dob|date_of_birth)", _re.I)
_RE_SHARED = _re.compile(
    r"(name|description|category|brand|sku|product_code|location_name|store_name)", _re.I
)


def _safe_schema_name(slug: str) -> str:
    return _re.sub(r"[^a-z0-9_]", "_", slug.lower())[:63]


def _build_schema_catalog(slug: str) -> dict:
    """Query information_schema for tables + columns in project's schema."""
    from sqlalchemy import text
    schema = _safe_schema_name(slug)

    # Prefer per-project readonly engine if available
    try:
        from db.session import get_project_readonly_engine
        eng = get_project_readonly_engine(slug)
    except Exception:
        eng = _get_engine()

    tables: list[dict] = []
    notes: list[str] = []

    with eng.connect() as conn:
        table_rows = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = :s AND table_type = 'BASE TABLE' "
            "ORDER BY table_name LIMIT :n"
        ), {"s": schema, "n": _MAX_TABLES + 1}).fetchall()

        if len(table_rows) > _MAX_TABLES:
            notes.append(f"Truncated to {_MAX_TABLES} tables (more exist in schema)")
            table_rows = table_rows[:_MAX_TABLES]

        for (tname,) in table_rows:
            col_rows = conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = :t "
                "ORDER BY ordinal_position LIMIT :n"
            ), {"s": schema, "t": tname, "n": _MAX_COLS_PER_TABLE + 1}).fetchall()

            if len(col_rows) > _MAX_COLS_PER_TABLE:
                notes.append(f"Table {tname}: truncated to {_MAX_COLS_PER_TABLE} columns")
                col_rows = col_rows[:_MAX_COLS_PER_TABLE]

            tables.append({
                "name": tname,
                "columns": [{"name": c, "type": t} for (c, t) in col_rows],
            })

    suggested_claims = _suggest_claims(tables)
    suggested_policies = _suggest_policies(tables, suggested_claims)

    payload: dict = {
        "tables": tables,
        "suggested_claims": suggested_claims,
        "suggested_policies": suggested_policies,
    }
    if notes:
        payload["note"] = "; ".join(notes)
    return payload


def _suggest_claims(tables: list[dict]) -> list[dict]:
    """Find columns ending _id (>=2 tables) or in known claim names."""
    col_table_count: dict[str, int] = {}
    col_types: dict[str, str] = {}

    for t in tables:
        seen_in_table: set[str] = set()
        for c in t["columns"]:
            name = c["name"].lower()
            if name in seen_in_table:
                continue
            seen_in_table.add(name)
            col_table_count[name] = col_table_count.get(name, 0) + 1
            # Keep first-seen type (good enough for type guess)
            if name not in col_types:
                col_types[name] = (c["type"] or "").lower()

    out: list[dict] = []
    seen: set[str] = set()

    for name, count in sorted(col_table_count.items(), key=lambda kv: (-kv[1], kv[0])):
        is_id_col = name.endswith("_id") and count >= 2
        is_named = name in _CLAIM_NAMED
        if not (is_id_col or is_named):
            continue
        if name in seen:
            continue
        seen.add(name)

        dtype = col_types.get(name, "")
        claim_type = "number" if any(it in dtype for it in _INTEGER_TYPES) else "string"

        if is_id_col:
            reason = f"Appears in {count} tables as foreign key candidate"
        else:
            reason = f"Common multi-tenant scope claim (found in {count} table{'s' if count != 1 else ''})"

        out.append({
            "key": name,
            "label": name.replace("_", " ").title(),
            "type": claim_type,
            "reason": reason,
        })

    return out


def _suggest_policies(tables: list[dict], suggested_claims: list[dict]) -> list[dict]:
    """Per-column regex match → policy mode + filter recommendation."""
    first_claim = suggested_claims[0]["key"] if suggested_claims else None
    out: list[dict] = []

    for t in tables:
        col_names_lower = {c["name"].lower() for c in t["columns"]}
        store_filter = "store_id" if "store_id" in col_names_lower else first_claim
        # Detect a per-row scoping column in THIS table for own_value suggestions
        own_value_filter = next((h for h in _CLAIM_COL_HINTS if h in col_names_lower), None)

        for c in t["columns"]:
            name = c["name"]
            lname = name.lower()

            # Order matters: hidden > redacted > own_value > private > shared.
            if _RE_HIDDEN.search(lname):
                out.append({
                    "table": t["name"],
                    "column": name,
                    "mode": "hidden",
                    "reason": "matches sensitive credential/secret pattern",
                })
                continue

            if _RE_REDACTED.search(lname):
                out.append({
                    "table": t["name"],
                    "column": name,
                    "mode": "redacted",
                    "reason": "matches PII pattern (email/phone/address/dob)",
                })
                continue

            if _RE_PRIVATE_QTY.search(lname):
                # Prefer own_value when a per-row scoping column exists in the SAME table:
                # row stays visible (catalog discovery) but value is masked to NULL for
                # rows belonging to other callers.
                if own_value_filter and name.lower() != own_value_filter:
                    out.append({
                        "table": t["name"],
                        "column": name,
                        "mode": "own_value",
                        "filter": own_value_filter,
                        "reason": "per-row mask — caller sees own value, NULL for others",
                    })
                    continue
                policy = {
                    "table": t["name"],
                    "column": name,
                    "mode": "private",
                    "reason": "inventory/quantity measure, scope to caller",
                }
                if store_filter:
                    policy["filter"] = store_filter
                out.append(policy)
                continue

            if _RE_PRIVATE_MONEY.search(lname):
                policy = {
                    "table": t["name"],
                    "column": name,
                    "mode": "private",
                    "reason": "matches sensitive cost/price pattern",
                }
                if store_filter:
                    policy["filter"] = store_filter
                out.append(policy)
                continue

            if _RE_SHARED.search(lname):
                out.append({
                    "table": t["name"],
                    "column": name,
                    "mode": "shared",
                    "reason": "descriptive/catalog field, safe to share across callers",
                })
                continue

            # No match → skip (don't over-suggest)

    return out


@router.get("/{slug}/schema-catalog")
def get_schema_catalog(slug: str, request: Request):
    """List all tables + columns in project schema, with claim/policy suggestions.

    Used by RLS policy UI to populate table/column dropdowns instead of
    free-text. 5-min in-memory cache. Same auth as GET /{slug}/embeds.
    """
    user = _get_user(request)
    _check_access(user, slug)

    now = _time.time()
    cached = _SCHEMA_CATALOG_CACHE.get(slug)
    if cached and (now - cached[0]) < _SCHEMA_CATALOG_TTL:
        return cached[1]

    try:
        payload = _build_schema_catalog(slug)
    except Exception:
        logger.exception("schema-catalog build failed for %s", slug)
        raise HTTPException(500, "Failed to build schema catalog")

    _SCHEMA_CATALOG_CACHE[slug] = (now, payload)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# RLS Wizard — 3-question hybrid setup, deterministic policy generator
# ─────────────────────────────────────────────────────────────────────────────

def _check_editor(user: dict, slug: str) -> None:
    """Editor-or-above gate for wizard endpoints."""
    from app.auth import check_project_permission
    if not check_project_permission(user, slug, required_role="editor"):
        raise HTTPException(403, "Editor permission required")


@router.get("/{slug}/embeds/{embed_id}/wizard-options")
def rls_wizard_options(slug: str, embed_id: str, request: Request):
    """Schema-aware option discovery for the 3 wizard questions."""
    user = _get_user(request)
    _check_editor(user, slug)
    _load_embed_or_404(embed_id, slug)
    from dash.embed.rls_wizard import build_wizard_options
    try:
        return build_wizard_options(slug)
    except Exception:
        logger.exception("wizard-options build failed for %s", slug)
        raise HTTPException(500, "Failed to build wizard options")


@router.post("/{slug}/embeds/{embed_id}/wizard-generate")
async def rls_wizard_generate(slug: str, embed_id: str, request: Request):
    """Generate policies from answers (preview only, NOT applied)."""
    user = _get_user(request)
    _check_editor(user, slug)
    _load_embed_or_404(embed_id, slug)
    body = await request.json()
    answers = body.get("answers") or {}
    if not isinstance(answers, dict):
        raise HTTPException(400, "answers must be an object")
    from dash.embed.rls_wizard import generate_policies, log_wizard_run
    try:
        out = generate_policies(slug, answers)
    except Exception:
        logger.exception("wizard-generate failed for %s/%s", slug, embed_id)
        raise HTTPException(500, "Failed to generate policies")
    log_wizard_run(
        embed_id=embed_id,
        project_slug=slug,
        user_id=user.get("user_id"),
        answers=answers,
        generated=out,
        applied=False,
    )
    out["policies_explained"] = _rls_explainer.explain_policies(out.get("policies") or [])
    out["claims_explained"]   = _rls_explainer.explain_claims(out.get("claims") or [])
    out["apply_modes"]        = _rls_explainer.apply_modes_legend()
    out["mode_legend"]        = _rls_explainer.mode_legend()
    return out


@router.post("/{slug}/embeds/{embed_id}/wizard-apply")
async def rls_wizard_apply(slug: str, embed_id: str, request: Request):
    """Generate + apply policies to the embed. mode='replace'|'merge'."""
    user = _get_user(request)
    _check_editor(user, slug)
    emb = _load_embed_or_404(embed_id, slug)
    body = await request.json()
    answers = body.get("answers") or {}
    mode = (body.get("mode") or "replace").lower()
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode must be 'replace' or 'merge'")
    if not isinstance(answers, dict):
        raise HTTPException(400, "answers must be an object")

    from dash.embed.rls_wizard import generate_policies, log_wizard_run
    try:
        out = generate_policies(slug, answers)
    except Exception:
        logger.exception("wizard-apply generate failed for %s/%s", slug, embed_id)
        raise HTTPException(500, "Failed to generate policies")

    new_claims = out.get("claims") or []
    new_policies = out.get("policies") or []

    if mode == "merge":
        existing_claims = emb.get("rls_claims") or []
        existing_policies = emb.get("rls_policies") or []
        # Dedup claims by key
        seen_keys = {(c or {}).get("key") for c in new_claims if isinstance(c, dict)}
        merged_claims = list(new_claims) + [
            c for c in existing_claims
            if isinstance(c, dict) and c.get("key") not in seen_keys
        ]
        # Dedup policies by (table, column)
        seen_pol = {((p or {}).get("table"), (p or {}).get("column"))
                    for p in new_policies if isinstance(p, dict)}
        merged_policies = list(new_policies) + [
            p for p in existing_policies
            if isinstance(p, dict) and (p.get("table"), p.get("column")) not in seen_pol
        ]
        new_claims = merged_claims
        new_policies = merged_policies

    # Persist via update_embed (supports rls_* fields per migration 064)
    try:
        embed_mgr.update_embed(
            embed_id,
            rls_enabled=True,
            rls_claims=new_claims,
            rls_policies=new_policies,
        )
    except Exception:
        logger.exception("wizard-apply update_embed failed for %s/%s", slug, embed_id)
        raise HTTPException(500, "Failed to persist policies")

    # Audit-log into embed_mgr audit trail (best-effort) + wizard runs table
    try:
        import json as _j
        from sqlalchemy import text as _t
        from dash.embed import _get_engine
        eng = _get_engine()
        with eng.begin() as conn:
            conn.execute(_t(
                "INSERT INTO public.dash_embed_rls_audit "
                "  (embed_id, session_token, claims, denied_table, denied_column, action, sql_snippet) "
                "VALUES (:e, NULL, CAST(:c AS jsonb), NULL, NULL, :a, :sql)"
            ), {
                "e":   embed_id,
                "c":   _j.dumps({"user_id": user.get("user_id"), "mode": mode}),
                "a":   "wizard_apply",
                "sql": _j.dumps(answers)[:4000],
            })
    except Exception:
        pass  # audit table may not exist on older deployments

    log_wizard_run(
        embed_id=embed_id,
        project_slug=slug,
        user_id=user.get("user_id"),
        answers=answers,
        generated={"claims": new_claims, "policies": new_policies,
                   "warnings": out.get("warnings") or [],
                   "summary":  out.get("summary") or {}},
        applied=True,
    )

    return {
        "status":   "ok",
        "mode":     mode,
        "claims":   new_claims,
        "policies": new_policies,
        "warnings": out.get("warnings") or [],
        "summary":  out.get("summary") or {},
        "applied_explained": _rls_explainer.explain_policies(new_policies),
        "claims_explained":  _rls_explainer.explain_claims(new_claims),
        "apply_modes":       _rls_explainer.apply_modes_legend(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# RLS BLUEPRINTS LIBRARY
# ═══════════════════════════════════════════════════════════════════════════
# A blueprint = preset bundle: claims + policies + role overrides + description.
# Two sources: shipped (system JSON in dash/embed/rls_blueprints.py) and user-saved.
#
# Library endpoints live on a separate router mounted at /api/embed-rls-blueprints
# (no /api/projects prefix). Project-scoped apply/compatibility endpoints stay
# on the main `router` (/api/projects/{slug}/embeds/{embed_id}/...).

blueprints_router = APIRouter(prefix="/api/embed-rls-blueprints",
                              tags=["Embed", "RLS Blueprints"])


def _bp_engine():
    from dash.embed import _get_engine
    return _get_engine()


def _bp_summary_row(row) -> dict:
    """Convert DB row → list-card payload (no full claims/policies)."""
    claims = row["claims"] or []
    policies = row["policies"] or []
    industry = row["industry"]
    name = row["name"]
    return {
        "slug": row["slug"],
        "name": name,
        "industry": industry,
        "icon": row["icon"],
        "description": row["description"],
        "claim_count": len(claims) if isinstance(claims, list) else 0,
        "policy_count": len(policies) if isinstance(policies, list) else 0,
        "required_tables": list(row["required_tables"] or []),
        "popularity": int(row["popularity"] or 0),
        "is_system": bool(row["is_system"]),
        "created_by": row["created_by"],
        "created_at": (row["created_at"].isoformat()
                       if row["created_at"] else None),
        "display": {
            "tagline": _rls_explainer.explain_blueprint_summary(
                {"industry": industry, "name": name, "slug": row["slug"]}
            )["tagline"],
        },
    }


@blueprints_router.get("")
def list_blueprints(request: Request):
    """List ALL blueprints visible to caller: system + own user-saved.

    Returns summary cards (no full claims/policies). Sorted by is_system DESC,
    popularity DESC, name ASC.
    """
    from sqlalchemy import text as _t
    user = _get_user(request)
    uid = user.get("user_id")
    eng = _bp_engine()
    with eng.connect() as conn:
        rows = conn.execute(_t(
            "SELECT slug, name, industry, icon, description, claims, policies, "
            "       required_tables, popularity, is_system, created_by, created_at "
            "FROM public.dash_embed_rls_blueprints "
            "WHERE is_system = TRUE OR created_by = :uid "
            "ORDER BY is_system DESC, popularity DESC, name ASC"
        ), {"uid": uid}).mappings().all()
    return {"status": "ok",
            "blueprints": [_bp_summary_row(r) for r in rows]}


@blueprints_router.get("/{slug}")
def get_blueprint(slug: str, request: Request):
    """Full detail for one blueprint (claims + policies + everything)."""
    from sqlalchemy import text as _t
    user = _get_user(request)
    uid = user.get("user_id")
    eng = _bp_engine()
    with eng.connect() as conn:
        row = conn.execute(_t(
            "SELECT slug, name, industry, icon, description, claims, policies, "
            "       required_tables, popularity, is_system, created_by, created_at "
            "FROM public.dash_embed_rls_blueprints "
            "WHERE slug = :slug AND (is_system = TRUE OR created_by = :uid)"
        ), {"slug": slug, "uid": uid}).mappings().first()
    if not row:
        raise HTTPException(404, "blueprint not found")
    bp_payload = {
        "slug": row["slug"],
        "name": row["name"],
        "industry": row["industry"],
        "icon": row["icon"],
        "description": row["description"],
        "claims": row["claims"] or [],
        "policies": row["policies"] or [],
        "required_tables": list(row["required_tables"] or []),
        "popularity": int(row["popularity"] or 0),
        "is_system": bool(row["is_system"]),
        "created_by": row["created_by"],
        "created_at": (row["created_at"].isoformat()
                       if row["created_at"] else None),
    }
    # Enrich with system metadata (tagline, who_is_this_for, faq, etc.)
    # which lives in dash/embed/rls_blueprints.py SYSTEM_BLUEPRINTS but not in DB.
    try:
        from dash.embed.rls_blueprints import SYSTEM_BLUEPRINTS as _SYS_BPS
        _sys = next((b for b in _SYS_BPS if b.get("slug") == row["slug"]), None)
        if _sys:
            for k in ("tagline", "who_is_this_for", "common_pitfalls",
                      "next_steps", "faq"):
                if k in _sys and k not in bp_payload:
                    bp_payload[k] = _sys[k]
    except Exception:  # pragma: no cover
        pass
    bp_payload["display"] = _rls_explainer.cached_blueprint_display(
        row["slug"], bp_payload
    )
    return {"status": "ok", "blueprint": bp_payload}


@blueprints_router.post("")
async def create_user_blueprint(request: Request):
    """Save current embed config as a user blueprint (is_system=FALSE, created_by=uid)."""
    from sqlalchemy import text as _t
    import json as _j
    user = _get_user(request)
    uid = user.get("user_id")
    body = await request.json()

    slug = (body.get("slug") or "").strip().lower()
    name = (body.get("name") or "").strip()
    if not slug or not name:
        raise HTTPException(400, "slug and name are required")
    if not _re.match(r"^[a-z0-9_-]{2,80}$", slug):
        raise HTTPException(400, "slug must be 2-80 chars [a-z0-9_-]")

    claims = body.get("claims") or []
    policies = body.get("policies") or []
    if not isinstance(claims, list) or not isinstance(policies, list):
        raise HTTPException(400, "claims and policies must be lists")

    industry = body.get("industry") or None
    icon = body.get("icon") or None
    description = body.get("description") or None
    required_tables = body.get("required_tables") or []
    if not isinstance(required_tables, list):
        raise HTTPException(400, "required_tables must be a list")

    eng = _bp_engine()
    try:
        with eng.begin() as conn:
            conn.execute(_t(
                "INSERT INTO public.dash_embed_rls_blueprints "
                "  (slug, name, industry, icon, description, claims, policies, "
                "   required_tables, is_system, created_by) "
                "VALUES (:slug, :name, :industry, :icon, :description, "
                "        CAST(:claims AS jsonb), CAST(:policies AS jsonb), "
                "        :required_tables, FALSE, :uid)"
            ), {
                "slug": slug, "name": name, "industry": industry,
                "icon": icon, "description": description,
                "claims": _j.dumps(claims), "policies": _j.dumps(policies),
                "required_tables": list(required_tables),
                "uid": uid,
            })
    except Exception as e:
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(409, f"slug '{slug}' already exists")
        logger.exception("create_user_blueprint failed")
        raise HTTPException(500, "failed to save blueprint")

    return {"status": "ok", "slug": slug}


@blueprints_router.patch("/{slug}")
async def update_user_blueprint(slug: str, request: Request):
    """Edit existing blueprint. User can only edit own non-system blueprints; super-admin can edit system blueprints."""
    from sqlalchemy import text as _t
    import json as _j
    user = _get_user(request)
    uid = user.get("user_id")
    is_super = bool(user.get("is_super"))
    body = await request.json()

    eng = _bp_engine()
    with eng.connect() as conn:
        row = conn.execute(_t(
            "SELECT created_by, is_system FROM public.dash_embed_rls_blueprints WHERE slug=:s"
        ), {"s": slug}).mappings().first()
    if not row:
        raise HTTPException(404, "blueprint not found")
    if row["is_system"] and not is_super:
        raise HTTPException(403, "system blueprints require super-admin")
    if not row["is_system"] and row["created_by"] != uid and not is_super:
        raise HTTPException(403, "you can only edit your own blueprints")

    sets = []
    params: dict = {"slug": slug}
    for k in ("name", "industry", "icon", "description"):
        if k in body:
            sets.append(f"{k} = :{k}")
            params[k] = body[k] or None
    if "claims" in body:
        if not isinstance(body["claims"], list):
            raise HTTPException(400, "claims must be a list")
        sets.append("claims = CAST(:claims AS jsonb)")
        params["claims"] = _j.dumps(body["claims"])
    if "policies" in body:
        if not isinstance(body["policies"], list):
            raise HTTPException(400, "policies must be a list")
        sets.append("policies = CAST(:policies AS jsonb)")
        params["policies"] = _j.dumps(body["policies"])
    if "required_tables" in body:
        if not isinstance(body["required_tables"], list):
            raise HTTPException(400, "required_tables must be a list")
        sets.append("required_tables = :required_tables")
        params["required_tables"] = list(body["required_tables"])
    if not sets:
        return {"status": "ok", "slug": slug, "note": "no changes"}
    try:
        with eng.begin() as conn:
            conn.execute(_t(
                f"UPDATE public.dash_embed_rls_blueprints SET {', '.join(sets)} WHERE slug = :slug"
            ), params)
    except Exception:
        logger.exception("update_user_blueprint failed")
        raise HTTPException(500, "failed to update blueprint")
    return {"status": "ok", "slug": slug}


@blueprints_router.delete("/{slug}")
def delete_user_blueprint(slug: str, request: Request):
    """Delete a user-saved blueprint (creator only; system blueprints can't be deleted)."""
    from sqlalchemy import text as _t
    user = _get_user(request)
    uid = user.get("user_id")
    eng = _bp_engine()
    with eng.begin() as conn:
        row = conn.execute(_t(
            "SELECT is_system, created_by FROM public.dash_embed_rls_blueprints "
            "WHERE slug = :slug"
        ), {"slug": slug}).first()
        if not row:
            raise HTTPException(404, "blueprint not found")
        if bool(row[0]):
            raise HTTPException(403, "system blueprints cannot be deleted")
        if row[1] != uid:
            raise HTTPException(403, "only the creator can delete this blueprint")
        conn.execute(_t(
            "DELETE FROM public.dash_embed_rls_blueprints WHERE slug = :slug"
        ), {"slug": slug})
    return {"status": "ok", "deleted": slug}


# ── Apply blueprint to embed ────────────────────────────────────────────────
def _claim_key(c: dict) -> str:
    return str(c.get("key") or "").lower()


def _policy_key(p: dict) -> tuple:
    return (str(p.get("table") or "").lower(),
            str(p.get("column") or "").lower())


def _validate_policies_against_schema(slug: str, policies: list[dict]
                                       ) -> tuple[list[dict], list[dict]]:
    """Drop policies referencing tables/columns that don't exist in project schema.

    Wildcard ``column == "*"`` only requires the table to exist.
    Returns (kept_policies, skipped_policies). Fail-open on catalog error
    (returns policies unchanged, empty skipped).
    """
    try:
        catalog = _build_schema_catalog(slug)
    except Exception:
        logger.warning("schema catalog unavailable for %s; skipping validation", slug)
        return policies, []

    tables_map: dict[str, set[str]] = {}
    for t in catalog.get("tables") or []:
        tname = (t.get("name") or "").lower()
        cols = {(c.get("name") or "").lower() for c in (t.get("columns") or [])}
        tables_map[tname] = cols

    kept: list[dict] = []
    skipped: list[dict] = []
    for p in policies:
        tname = (p.get("table") or "").lower()
        cname = (p.get("column") or "").lower()
        if tname not in tables_map:
            skipped.append({**p, "_skip_reason": f"table '{tname}' not found"})
            continue
        if cname != "*" and cname not in tables_map[tname]:
            skipped.append({**p, "_skip_reason":
                            f"column '{tname}.{cname}' not found"})
            continue
        kept.append(p)
    return kept, skipped


@router.post("/{slug}/embeds/{embed_id}/apply-blueprint")
async def apply_blueprint_to_embed(slug: str, embed_id: str, request: Request):
    """Apply a blueprint preset to this embed's rls_claims + rls_policies.

    Body: ``{"blueprint_slug": str, "mode": "replace"|"merge"}``
    - replace: clobber existing claims+policies with blueprint's
    - merge:   dedupe by claim.key / (policy.table, policy.column);
               blueprint values WIN on conflict; conflicts reported

    Enables rls_enabled=TRUE. Validates each policy against project schema
    (drops missing into ``skipped``). Increments blueprint.popularity.
    Logs to dash_embed_rls_audit (action='apply_blueprint').
    Requires embed editor permission (project access).
    """
    from sqlalchemy import text as _t
    import json as _j

    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)

    body = await request.json()
    bp_slug = (body.get("blueprint_slug") or "").strip().lower()
    mode = (body.get("mode") or "replace").strip().lower()
    if not bp_slug:
        raise HTTPException(400, "blueprint_slug is required")
    if mode not in ("replace", "merge"):
        raise HTTPException(400, "mode must be 'replace' or 'merge'")

    eng = _bp_engine()
    uid = user.get("user_id")

    # 1. Load blueprint (system OR own user-saved)
    with eng.connect() as conn:
        bp = conn.execute(_t(
            "SELECT slug, claims, policies FROM public.dash_embed_rls_blueprints "
            "WHERE slug = :slug AND (is_system = TRUE OR created_by = :uid)"
        ), {"slug": bp_slug, "uid": uid}).mappings().first()
    if not bp:
        raise HTTPException(404, "blueprint not found")
    bp_claims = list(bp["claims"] or [])
    bp_policies = list(bp["policies"] or [])

    # 1b. Optional override — caller can supply edited claims/policies arrays
    # that REPLACE the blueprint's arrays for THIS apply only (blueprint itself
    # is never modified). Useful for the inline-edit preview flow.
    override = body.get("override") or {}
    override_used = False
    if isinstance(override, dict):
        ov_claims = override.get("claims")
        ov_policies = override.get("policies")
        if isinstance(ov_claims, list):
            # Light shape validation — each item must be a dict with a 'key'
            ov_claims = [c for c in ov_claims
                         if isinstance(c, dict) and str(c.get("key") or "").strip()]
            bp_claims = ov_claims
            override_used = True
        if isinstance(ov_policies, list):
            ov_policies = [p for p in ov_policies
                           if isinstance(p, dict)
                           and str(p.get("table") or "").strip()
                           and str(p.get("mode") or "").strip()]
            bp_policies = ov_policies
            override_used = True

    # 2. Validate policies against project schema (drop missing tables/cols).
    #    For override mode the same checks apply — invalid policies are dropped
    #    into `skipped` and reported back to the caller.
    bp_policies_kept, skipped = _validate_policies_against_schema(slug, bp_policies)
    # Additional override-only validation: filter must reference a declared claim key.
    if override_used and bp_claims:
        declared_keys = {str(c.get("key") or "").lower() for c in bp_claims}
        kept2: list[dict] = []
        for p in bp_policies_kept:
            f = (p.get("filter") or p.get("claim") or "")
            if f and str(f).lower() not in declared_keys:
                skipped.append({**p, "_skip_reason":
                                f"filter '{f}' is not a declared claim"})
                continue
            kept2.append(p)
        bp_policies_kept = kept2

    # 3. Load current embed RLS state
    with eng.connect() as conn:
        cur = conn.execute(_t(
            "SELECT rls_claims, rls_policies "
            "FROM public.dash_agent_embeds WHERE embed_id = :e"
        ), {"e": embed_id}).mappings().first()
    cur_claims = list((cur and cur["rls_claims"]) or []) if cur else []
    cur_policies = list((cur and cur["rls_policies"]) or []) if cur else []

    conflicts: list[dict] = []

    if mode == "replace":
        new_claims = bp_claims
        new_policies = bp_policies_kept
    else:
        # merge: dedupe by key, blueprint wins; record conflicts
        claims_idx = {_claim_key(c): c for c in cur_claims if _claim_key(c)}
        for c in bp_claims:
            k = _claim_key(c)
            if not k:
                continue
            if k in claims_idx and claims_idx[k] != c:
                conflicts.append({"type": "claim", "key": k,
                                  "previous": claims_idx[k], "new": c})
            claims_idx[k] = c
        new_claims = list(claims_idx.values())

        pol_idx = {_policy_key(p): p for p in cur_policies if any(_policy_key(p))}
        for p in bp_policies_kept:
            pk = _policy_key(p)
            if not any(pk):
                continue
            if pk in pol_idx and pol_idx[pk] != p:
                conflicts.append({"type": "policy", "key": list(pk),
                                  "previous": pol_idx[pk], "new": p})
            pol_idx[pk] = p
        new_policies = list(pol_idx.values())

    # 4. Persist to embed + bump popularity
    with eng.begin() as conn:
        conn.execute(_t(
            "UPDATE public.dash_agent_embeds "
            "SET rls_enabled = TRUE, "
            "    rls_claims = CAST(:c AS jsonb), "
            "    rls_policies = CAST(:p AS jsonb) "
            "WHERE embed_id = :e"
        ), {"c": _j.dumps(new_claims),
            "p": _j.dumps(new_policies),
            "e": embed_id})
        conn.execute(_t(
            "UPDATE public.dash_embed_rls_blueprints "
            "SET popularity = popularity + 1 WHERE slug = :slug"
        ), {"slug": bp_slug})

    # 5. Audit (best-effort)
    try:
        from dash.embed.rls import audit_denial
        audit_denial(
            embed_id=embed_id,
            session_token=None,
            claims={"blueprint": bp_slug, "mode": mode, "user_id": uid},
            table=None,
            column=None,
            action="apply_blueprint",
            sql_snippet=bp_slug,
        )
    except Exception as _ae:
        logger.warning("apply_blueprint audit failed: %s", _ae)

    return {
        "status": "ok",
        "override_applied": override_used,
        "claims_count": len(new_claims),
        "policies_count": len(new_policies),
        "skipped": skipped,
        "conflicts": conflicts,
        "applied_explained": _rls_explainer.explain_policies(new_policies),
        "claims_explained":  _rls_explainer.explain_claims(new_claims),
        "skipped_explained": _rls_explainer.explain_skipped(skipped),
        "apply_modes":       _rls_explainer.apply_modes_legend(),
    }


@router.get("/{slug}/embeds/{embed_id}/blueprint-compatibility")
def blueprint_compatibility(slug: str, embed_id: str, request: Request):
    """Report which of a blueprint's required_tables exist in project schema.

    Query param ``?slug=<blueprint_slug>`` (path uses ``slug`` for project).
    Returns ``{required_tables, present, missing, compatible}``.
    """
    bp_slug = (request.query_params.get("slug") or "").strip().lower()
    if not bp_slug:
        raise HTTPException(400, "query param 'slug' (blueprint slug) is required")

    from sqlalchemy import text as _t
    user = _get_user(request)
    _check_access(user, slug)
    _load_embed_or_404(embed_id, slug)

    eng = _bp_engine()
    uid = user.get("user_id")
    with eng.connect() as conn:
        bp = conn.execute(_t(
            "SELECT required_tables FROM public.dash_embed_rls_blueprints "
            "WHERE slug = :slug AND (is_system = TRUE OR created_by = :uid)"
        ), {"slug": bp_slug, "uid": uid}).first()
    if not bp:
        raise HTTPException(404, "blueprint not found")
    required = list(bp[0] or [])

    try:
        catalog = _build_schema_catalog(slug)
        existing_names = [(t.get("name") or "") for t in (catalog.get("tables") or [])]
        existing = {n.lower() for n in existing_names if n}
    except Exception:
        logger.exception("blueprint_compatibility: catalog failed")
        existing_names = []
        existing = set()

    present = [t for t in required if t.lower() in existing]
    missing = [t for t in required if t.lower() not in existing]
    return {
        "status": "ok",
        "blueprint_slug": bp_slug,
        "required_tables": required,
        "present": present,
        "missing": missing,
        "compatible": len(missing) == 0,
        "display": _rls_explainer.explain_required_tables(
            required, present, missing, present_all=existing_names,
        ),
    }


# ── Embed cache admin endpoints ─────────────────────────────────────────────
# Mounted under different prefix because router prefix is /api/projects.
# Use a second router so paths can be /api/admin/embed-cache/* and
# /api/admin/embeds/{embed_id}/cache/invalidate.
from fastapi import APIRouter as _APIRouter  # noqa: E402

cache_admin_router = _APIRouter(prefix="/api/admin", tags=["EmbedCacheAdmin"])


@cache_admin_router.get("/embed-cache/stats")
def embed_cache_stats(request: Request):
    """Super-admin: return cache hit/miss/ratio + redis connection state."""
    user = _get_user(request)
    if not user.get("is_super"):
        raise HTTPException(403, "super-admin only")
    try:
        from dash.cache import embed_cache as _ec
        return _ec.stats()
    except Exception as e:
        logger.warning("embed_cache stats failed: %s", e)
        return {"hits": 0, "misses": 0, "hit_ratio": 0.0, "redis_connected": False}


@cache_admin_router.post("/embeds/{embed_id}/cache/invalidate")
def embed_cache_invalidate(embed_id: str, request: Request):
    """Admin: invalidate all cached answers for one embed. Returns count deleted."""
    user = _get_user(request)
    # Allow super-admin OR owner of the embed's project.
    if not user.get("is_super"):
        try:
            from sqlalchemy import text as _sa_text
            from dash.embed import _get_engine
            eng = _get_engine()
            with eng.connect() as conn:
                row = conn.execute(_sa_text(
                    "SELECT project_slug FROM public.dash_agent_embeds WHERE embed_id = :e"
                ), {"e": embed_id}).first()
            if not row:
                raise HTTPException(404, "embed not found")
            _check_access(user, row[0])
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(403, "access denied")
    try:
        from dash.cache import embed_cache as _ec
        n = _ec.invalidate(embed_id)
        return {"embed_id": embed_id, "invalidated": int(n)}
    except Exception as e:
        logger.warning("embed_cache invalidate failed: %s", e)
        return {"embed_id": embed_id, "invalidated": 0, "error": str(e)[:200]}


