"""Cross-Project Skill Marketplace.

Promotes vetted project-scoped skills (`public.dash_skill_library`) into a
global pool (`dash.dash_skill_marketplace`) tagged by template_name (vertical),
then lets new projects bootstrap from the pool — solves the cold-start
problem on freshly-created projects.

Public surface
--------------
    nominate_to_marketplace(skill_id, nominator_user_id) -> dict
    list_marketplace(template=None, search=None, limit=50) -> list[dict]
    get_marketplace_skill(marketplace_id) -> dict | None
    install_skill(marketplace_id, target_project_slug, user_id) -> dict
    auto_bootstrap_new_project(project_slug, template_name) -> int
    track_install_success(marketplace_id, install_id, succeeded) -> None

All functions are sync, never raise — return ``{ok, ...}`` / ``{ok: False, error}``.
Schema-qualifies ``dash.*``, CASTs jsonb explicitly, uses NullPool.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)


# ── Hard caps / thresholds ────────────────────────────────────────────────
_ELIG_MIN_SUCCESS_COUNT = 20
_ELIG_MIN_JUDGE_SCORE = 4.5
_ELIG_FAILURE_WINDOW_DAYS = 7
_BOOTSTRAP_TOP_N = 5
_DEFAULT_LIST_LIMIT = 50
_MAX_LIST_LIMIT = 200

# ── PII / secret blockers (regex over sql_template) ───────────────────────
_PII_BLOCKERS = [
    re.compile(r"\$\{?env(?:iron(?:ment)?)?[._:]", re.IGNORECASE),
    re.compile(r"\bsecret(?:_key)?\b", re.IGNORECASE),
    re.compile(r"\bapi[_-]?key\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bauth[_-]?token\b", re.IGNORECASE),
    re.compile(r"\b(?:ssn|social[_-]?security)\b", re.IGNORECASE),
    re.compile(r"\bcredit[_-]?card\b", re.IGNORECASE),
    # personal columns (heuristic)
    re.compile(r"\b(?:email_address|phone_number|home_address|date_of_birth|dob)\b", re.IGNORECASE),
]


def _engine():
    """Return a NullPool engine (cheap to import lazily)."""
    from db.session import get_sql_engine
    return get_sql_engine()


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


def _scan_for_pii(sql_template: str) -> Optional[str]:
    """Return a human-readable reason if sql_template contains PII/secret markers."""
    if not sql_template:
        return "empty sql_template"
    for pat in _PII_BLOCKERS:
        m = pat.search(sql_template)
        if m:
            return f"blocked token matched: {m.group(0)!r}"
    return None


def _parameterize_schema(sql_template: str, user_schema: Optional[str]) -> str:
    """Replace concrete project schema references with ``${schema}`` placeholder.

    Handles ``{user_schema}.tbl``, ``proj_foo.tbl`` (matching ``user_schema``),
    and ``"proj_foo".tbl``.
    """
    if not sql_template:
        return sql_template
    out = sql_template

    # Already-template placeholder (Python ``str.format`` style) → normalize.
    out = re.sub(r"\{user_schema\}\.", r"${schema}.", out)
    out = re.sub(r"\{schema\}\.", r"${schema}.", out)

    if user_schema:
        # bareword: user_schema.table
        out = re.sub(
            r"\b" + re.escape(user_schema) + r"\.",
            r"${schema}.",
            out,
        )
        # quoted: "user_schema".table
        out = re.sub(
            r'"' + re.escape(user_schema) + r'"\.',
            r"${schema}.",
            out,
        )
    return out


def _materialize_schema(sql_template: str, target_schema: str) -> str:
    """Replace ``${schema}`` placeholder with target schema name."""
    if not sql_template or not target_schema:
        return sql_template
    return sql_template.replace("${schema}", target_schema)


def _derive_template_for_project(slug: str, conn) -> str:
    """Look up template_name for project via dash_template_expectations.

    Falls back to ``'generic'`` when no template was applied.
    """
    try:
        row = conn.execute(
            text(
                "SELECT template_name FROM dash.dash_template_expectations "
                "WHERE project_slug = :s ORDER BY applied_at DESC LIMIT 1"
            ),
            {"s": slug},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        # table may not exist on fresh installs
        pass
    return "generic"


def _project_schema(slug: str, conn) -> Optional[str]:
    try:
        row = conn.execute(
            text("SELECT schema_name FROM public.dash_projects WHERE slug = :s"),
            {"s": slug},
        ).fetchone()
        return str(row[0]) if row and row[0] else slug
    except Exception:
        return slug


# ──────────────────────────────────────────────────────────────────────────
# 1. Nominate
# ──────────────────────────────────────────────────────────────────────────
def nominate_to_marketplace(skill_id: int, nominator_user_id: int) -> Dict[str, Any]:
    """Nominate a project-scoped skill into the global marketplace.

    Eligibility:
      * status='active'
      * success_count >= 20
      * avg_judge_score >= 4.5
      * no failures in last 7 days (success_count delta heuristic)
      * sql_template free of PII / env / secret markers
    """
    if not skill_id or not nominator_user_id:
        return {"ok": False, "error": "skill_id and nominator_user_id required"}

    try:
        eng = _engine()
        with eng.connect() as conn:
            sk = conn.execute(
                text(
                    "SELECT id, project_slug, name, description, sql_template, "
                    "       params_schema, success_count, failure_count, "
                    "       avg_judge_score, status, last_used_at "
                    "FROM public.dash_skill_library WHERE id = :sid"
                ),
                {"sid": int(skill_id)},
            ).mappings().fetchone()

            if not sk:
                return {"ok": False, "error": f"skill {skill_id} not found"}

            if (sk["status"] or "").lower() != "active":
                return {"ok": False, "error": f"skill status is {sk['status']!r}, must be 'active'"}

            success_count = int(sk["success_count"] or 0)
            if success_count < _ELIG_MIN_SUCCESS_COUNT:
                return {
                    "ok": False,
                    "error": f"success_count={success_count} < {_ELIG_MIN_SUCCESS_COUNT}",
                }

            judge_score = float(sk["avg_judge_score"] or 0.0)
            if judge_score < _ELIG_MIN_JUDGE_SCORE:
                return {
                    "ok": False,
                    "error": f"avg_judge_score={judge_score:.2f} < {_ELIG_MIN_JUDGE_SCORE}",
                }

            # No failures in last N days (heuristic: failure_count must be 0 OR
            # last_used_at older than window — we don't have per-failure
            # timestamps so we approximate via failure_count being stable).
            failure_count = int(sk["failure_count"] or 0)
            if failure_count > 0:
                # accept if no recent activity (cooled down)
                row = conn.execute(
                    text(
                        "SELECT 1 FROM public.dash_skill_library "
                        "WHERE id = :sid AND last_used_at > now() - (:days || ' days')::interval"
                    ),
                    {"sid": int(skill_id), "days": _ELIG_FAILURE_WINDOW_DAYS},
                ).fetchone()
                if row:
                    return {
                        "ok": False,
                        "error": f"recent failures detected within {_ELIG_FAILURE_WINDOW_DAYS}d window",
                    }

            sql_template = sk["sql_template"] or ""
            pii_reason = _scan_for_pii(sql_template)
            if pii_reason:
                return {"ok": False, "error": f"pii_scan: {pii_reason}"}

            source_slug = sk["project_slug"]
            template_name = _derive_template_for_project(source_slug, conn)
            user_schema = _project_schema(source_slug, conn)

            parameterized_sql = _parameterize_schema(sql_template, user_schema)

            params_schema = sk["params_schema"] or {}
            if isinstance(params_schema, str):
                try:
                    params_schema = json.loads(params_schema)
                except Exception:
                    params_schema = {}

        # Insert in a fresh txn
        with eng.begin() as conn:
            row = conn.execute(
                text(
                    "INSERT INTO dash.dash_skill_marketplace "
                    "  (name, description, sql_template, params_schema, "
                    "   template_name, source_project_slug, nominator_user_id, "
                    "   avg_judge_score, source_success_count, tags) "
                    "VALUES (:n, :d, :sql, CAST(:p AS jsonb), :t, :sp, :uid, "
                    "        :score, :sc, CAST(:tags AS text[])) "
                    "ON CONFLICT (name, template_name) DO UPDATE SET "
                    "  description = EXCLUDED.description, "
                    "  sql_template = EXCLUDED.sql_template, "
                    "  params_schema = EXCLUDED.params_schema, "
                    "  avg_judge_score = EXCLUDED.avg_judge_score, "
                    "  source_success_count = EXCLUDED.source_success_count, "
                    "  status = 'active' "
                    "RETURNING id"
                ),
                {
                    "n": sk["name"],
                    "d": sk["description"],
                    "sql": parameterized_sql,
                    "p": json.dumps(params_schema),
                    "t": template_name,
                    "sp": source_slug,
                    "uid": int(nominator_user_id),
                    "score": judge_score,
                    "sc": success_count,
                    "tags": "{" + template_name + "}",
                },
            ).fetchone()
            mid = int(row[0]) if row else None

        logger.info(
            "skill_marketplace: nominated skill_id=%s → marketplace_id=%s template=%s",
            skill_id, mid, template_name,
        )
        return {
            "ok": True,
            "marketplace_id": mid,
            "template_name": template_name,
            "source_project_slug": source_slug,
        }
    except Exception as e:
        logger.exception("nominate_to_marketplace failed for skill_id=%s", skill_id)
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# 2. List / Browse
# ──────────────────────────────────────────────────────────────────────────
def list_marketplace(
    template: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = _DEFAULT_LIST_LIMIT,
) -> List[Dict[str, Any]]:
    """List marketplace skills, optionally filtered by template/search.

    Search is a simple ILIKE over ``name`` and ``description``; cosine
    similarity over ``description_embedding`` is reserved for future use
    once embed daemon populates the column.
    """
    try:
        l = max(1, min(int(limit or _DEFAULT_LIST_LIMIT), _MAX_LIST_LIMIT))
        params: Dict[str, Any] = {"l": l}
        where = ["status = 'active'"]
        if template:
            where.append("template_name = :t")
            params["t"] = str(template).strip().lower()
        if search:
            where.append("(name ILIKE :q OR description ILIKE :q)")
            params["q"] = f"%{search.strip()}%"

        sql = (
            "SELECT id, name, description, template_name, source_project_slug, "
            "       avg_judge_score, source_success_count, install_count, "
            "       total_installs_succeeded, total_installs_failed, tags, "
            "       created_at "
            "FROM dash.dash_skill_marketplace "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY install_count DESC, avg_judge_score DESC NULLS LAST, "
            "         source_success_count DESC "
            "LIMIT :l"
        )
        with _engine().connect() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.exception("list_marketplace failed")
        return [{"error": str(e)}]


def get_marketplace_skill(marketplace_id: int) -> Optional[Dict[str, Any]]:
    """Return single marketplace skill detail (includes sql_template, params_schema)."""
    try:
        with _engine().connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, name, description, sql_template, params_schema, "
                    "       template_name, source_project_slug, nominator_user_id, "
                    "       avg_judge_score, source_success_count, install_count, "
                    "       total_installs_succeeded, total_installs_failed, "
                    "       status, tags, created_at "
                    "FROM dash.dash_skill_marketplace WHERE id = :mid"
                ),
                {"mid": int(marketplace_id)},
            ).mappings().fetchone()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.exception("get_marketplace_skill failed for id=%s", marketplace_id)
        return {"error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# 3. Install
# ──────────────────────────────────────────────────────────────────────────
def install_skill(
    marketplace_id: int,
    target_project_slug: str,
    user_id: int,
) -> Dict[str, Any]:
    """Materialize a marketplace skill into the target project's skill library."""
    if not marketplace_id or not target_project_slug:
        return {"ok": False, "error": "marketplace_id and target_project_slug required"}

    try:
        eng = _engine()
        with eng.connect() as conn:
            mk = conn.execute(
                text(
                    "SELECT id, name, description, sql_template, params_schema, "
                    "       template_name "
                    "FROM dash.dash_skill_marketplace "
                    "WHERE id = :mid AND status = 'active'"
                ),
                {"mid": int(marketplace_id)},
            ).mappings().fetchone()
            if not mk:
                return {"ok": False, "error": f"marketplace skill {marketplace_id} not found or inactive"}

            target_schema = _project_schema(target_project_slug, conn)
            materialized_sql = _materialize_schema(mk["sql_template"], target_schema)

            params_schema = mk["params_schema"] or {}
            if isinstance(params_schema, str):
                try:
                    params_schema = json.loads(params_schema)
                except Exception:
                    params_schema = {}

        # Insert into project's skill library
        metadata = {
            "installed_from_marketplace": int(marketplace_id),
            "marketplace_template": mk["template_name"],
        }

        with eng.begin() as conn:
            # dash_skill_library doesn't have a metadata column historically —
            # store marker in description suffix to keep migration-free. Also
            # add unique guard via (project_slug, name).
            existing = conn.execute(
                text(
                    "SELECT id FROM public.dash_skill_library "
                    "WHERE project_slug = :s AND name = :n"
                ),
                {"s": target_project_slug, "n": mk["name"]},
            ).fetchone()
            if existing:
                install_id = int(existing[0])
                logger.info(
                    "install_skill: skill already exists for slug=%s name=%s id=%s",
                    target_project_slug, mk["name"], install_id,
                )
            else:
                row = conn.execute(
                    text(
                        "INSERT INTO public.dash_skill_library "
                        "  (project_slug, name, description, sql_template, "
                        "   params_schema, status) "
                        "VALUES (:s, :n, :d, :sql, CAST(:p AS jsonb), 'active') "
                        "RETURNING id"
                    ),
                    {
                        "s": target_project_slug,
                        "n": mk["name"],
                        "d": f"{mk['description']}\n\n[installed_from_marketplace:{marketplace_id}]",
                        "sql": materialized_sql,
                        "p": json.dumps(params_schema),
                    },
                ).fetchone()
                install_id = int(row[0]) if row else None

            # Bump install counter (atomic)
            conn.execute(
                text(
                    "UPDATE dash.dash_skill_marketplace "
                    "SET install_count = install_count + 1 "
                    "WHERE id = :mid"
                ),
                {"mid": int(marketplace_id)},
            )

        # Optional skill_audit hook (best-effort, never blocks)
        audit_result = None
        try:
            from dash.learning.skill_audit import audit_skill_candidate  # type: ignore
            audit_result = audit_skill_candidate(
                project_slug=target_project_slug,
                name=mk["name"],
                description=mk["description"],
                sql_template=materialized_sql,
            )
        except Exception:
            logger.debug("install_skill: skill_audit unavailable / failed (non-fatal)")

        logger.info(
            "skill_marketplace: installed mid=%s into slug=%s as skill_id=%s",
            marketplace_id, target_project_slug, install_id,
        )
        return {
            "ok": True,
            "install_id": install_id,
            "marketplace_id": int(marketplace_id),
            "target_project_slug": target_project_slug,
            "metadata": metadata,
            "audit": audit_result,
        }
    except Exception as e:
        logger.exception("install_skill failed mid=%s slug=%s", marketplace_id, target_project_slug)
        # Treat exceptional install as failure
        try:
            with _engine().begin() as conn:
                conn.execute(
                    text(
                        "UPDATE dash.dash_skill_marketplace "
                        "SET total_installs_failed = total_installs_failed + 1 "
                        "WHERE id = :mid"
                    ),
                    {"mid": int(marketplace_id)},
                )
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────
# 4. Auto-bootstrap (called on project create)
# ──────────────────────────────────────────────────────────────────────────
def auto_bootstrap_new_project(project_slug: str, template_name: str) -> int:
    """Auto-install top-N marketplace skills matching this project's template.

    Returns count of skills installed. Non-fatal — logs and returns 0 on
    failure.
    """
    if not project_slug:
        return 0
    template_name = (template_name or "generic").strip().lower()
    try:
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id FROM dash.dash_skill_marketplace "
                    "WHERE status = 'active' AND template_name = :t "
                    "ORDER BY install_count DESC, avg_judge_score DESC NULLS LAST, "
                    "         source_success_count DESC "
                    "LIMIT :n"
                ),
                {"t": template_name, "n": _BOOTSTRAP_TOP_N},
            ).fetchall()
        installed = 0
        for r in rows:
            mid = int(r[0])
            result = install_skill(mid, project_slug, user_id=0)
            if result.get("ok"):
                installed += 1
        logger.info(
            "skill_marketplace: bootstrapped slug=%s template=%s installed=%d",
            project_slug, template_name, installed,
        )
        return installed
    except Exception:
        logger.exception(
            "auto_bootstrap_new_project failed slug=%s template=%s",
            project_slug, template_name,
        )
        return 0


# ──────────────────────────────────────────────────────────────────────────
# 5. Track install success (called after first usage / audit verdict)
# ──────────────────────────────────────────────────────────────────────────
def track_install_success(
    marketplace_id: int,
    install_id: Optional[int] = None,
    succeeded: bool = True,
) -> None:
    """Bump succeeded/failed counters on a marketplace row."""
    if not marketplace_id:
        return
    col = "total_installs_succeeded" if succeeded else "total_installs_failed"
    try:
        with _engine().begin() as conn:
            conn.execute(
                text(
                    f"UPDATE dash.dash_skill_marketplace "
                    f"SET {col} = {col} + 1 WHERE id = :mid"
                ),
                {"mid": int(marketplace_id)},
            )
    except Exception:
        logger.debug("track_install_success failed mid=%s", marketplace_id, exc_info=True)
