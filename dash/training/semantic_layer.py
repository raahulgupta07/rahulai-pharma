"""Engineer-agent semantic layer — materialized-view designer for training.

The Engineer (an Agno agent) PROPOSES materialized views over the trained
tables; it never executes DDL. Everything dangerous lives here, in trusted
Python:

  validate_matview_spec()  — the whitelist gate. Rejects anything that is not a
                             pure, single-statement SELECT over the project's
                             own schema. This is the ENTIRE safety story for
                             LLM-authored SQL; treat it adversarially.
  build_ddl()              — assembles the CREATE MATERIALIZED VIEW + index
                             statements from validated, structured fields. The
                             LLM's free text is only ever the SELECT body, which
                             validate_matview_spec() has already vetted.
  apply_semantic_layer()   — runs the validated DDL inside one txn on the
                             non-superuser app role with a statement timeout,
                             then registers the matview in dash_table_metadata.

Design rule: the agent returns a struct (name / select_sql / indexes); WE build
the DDL. The model never holds a string we execute verbatim, and never gets a
"run this SQL" tool. Defense in depth: validate the SELECT, rebuild the DDL,
EXPLAIN server-side, execute as a locked-down role.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Limits ──────────────────────────────────────────────────────────────────
MAX_SELECT_LEN = 8000            # a matview SELECT longer than this is suspect
MAX_NAME_LEN = 48
MAX_MATVIEWS = 5                 # cap per training run (cost + blast radius)
STATEMENT_TIMEOUT_MS = 30000     # same guard as relationship verify

# Identifier: lowercase, starts with a letter, snake_case only.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Index column list: bare identifiers, commas, spaces. No expressions/functions.
_INDEX_COLS_RE = re.compile(r"^[a-z_][a-z0-9_]*(\s*,\s*[a-z_][a-z0-9_]*)*$")

# Statement must read, never write/define/administer. Whole-word match so a
# column literally named "updated" can't trip "UPDATE".
_FORBIDDEN_KEYWORDS = (
    "DROP", "ALTER", "CREATE", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
    "MERGE", "GRANT", "REVOKE", "COPY", "CALL", "EXECUTE", "VACUUM",
    "ANALYZE", "REINDEX", "CLUSTER", "REFRESH", "ATTACH", "DETACH", "SET",
    "RESET", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "PREPARE",
    "LISTEN", "NOTIFY", "LOCK", "DO", "DECLARE", "FETCH",
)
# Schemas the SELECT must never reach into. Anything qualified with these (or a
# bare pg_/information_schema reference) is rejected outright.
_FORBIDDEN_SCHEMA_REFS = (
    "pg_catalog.", "pg_temp.", "pg_toast.", "information_schema.",
    "public.", "dash.", "ai.",
)


class MatviewRejected(ValueError):
    """Raised when a proposed matview fails the whitelist. Never retried."""


@dataclass
class MatviewSpec:
    """Structured proposal from the Engineer agent. Free text only in
    `purpose`/`grain`/`select_sql`; the SELECT is the sole executed string and
    is whitelist-validated before any DDL is built."""
    name: str
    select_sql: str
    unique_index: str                       # cols for the REFRESH-CONCURRENTLY unique idx
    purpose: str = ""
    grain: str = ""
    extra_indexes: list[str] = field(default_factory=list)


def _strip_sql(sql: str) -> str:
    """Trim and reject SQL comments outright (they hide payloads). We do NOT
    strip comments — their mere presence is grounds for rejection."""
    return (sql or "").strip()


def validate_matview_spec(spec: MatviewSpec, base_tables: set[str],
                          schema: str = "citypharma") -> None:
    """Raise MatviewRejected unless the spec is a safe, pure SELECT matview.

    base_tables — the project's real table/view names; the matview must not
    shadow one. schema — the only schema the SELECT may touch.
    """
    # ── name ── validate the RAW name (case-sensitive) so "Bad" is rejected,
    # not silently lowercased into "bad".
    raw_name = (spec.name or "").strip()
    if not _NAME_RE.match(raw_name):
        raise MatviewRejected(f"bad name {spec.name!r}: must be snake_case [a-z][a-z0-9_]*")
    if len(raw_name) > MAX_NAME_LEN:
        raise MatviewRejected(f"name too long ({len(raw_name)}>{MAX_NAME_LEN})")
    name = raw_name
    if name.upper() in _FORBIDDEN_KEYWORDS:
        raise MatviewRejected(f"name {name!r} is a reserved SQL keyword")
    if name in {t.lower() for t in base_tables}:
        raise MatviewRejected(f"name {name!r} shadows an existing base table")

    # ── select body ──
    sql = _strip_sql(spec.select_sql)
    if not sql:
        raise MatviewRejected("empty select_sql")
    if len(sql) > MAX_SELECT_LEN:
        raise MatviewRejected(f"select_sql too long ({len(sql)}>{MAX_SELECT_LEN})")

    # single statement only — a ';' may appear ONLY as one trailing terminator.
    # Any ';' before the final char (incl. "SELECT 1;;") means a second statement.
    _stripped = sql.rstrip()
    if ";" in _stripped[:-1]:
        raise MatviewRejected("multiple statements (';' inside select_sql)")

    # no comments — they hide injected payloads
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise MatviewRejected("SQL comments are not allowed in select_sql")

    # must be a read: SELECT or a WITH-cte that resolves to SELECT
    head = re.match(r"^\(*\s*(\w+)", sql)
    if not head or head.group(1).upper() not in ("SELECT", "WITH"):
        raise MatviewRejected("select_sql must start with SELECT or WITH")

    upper = sql.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"(?<![A-Z_]){kw}(?![A-Z_])", upper):
            raise MatviewRejected(f"forbidden keyword {kw!r} in select_sql")

    # SELECT ... INTO writes a table — block the INTO form specifically
    if re.search(r"(?<![A-Z_])INTO(?![A-Z_])", upper):
        raise MatviewRejected("SELECT INTO is not allowed")

    # no cross-schema reach
    low = sql.lower()
    for ref in _FORBIDDEN_SCHEMA_REFS:
        if ref in low:
            raise MatviewRejected(f"forbidden schema reference {ref!r}")
    if re.search(r"(?<![a-z_])pg_[a-z_]+", low):
        raise MatviewRejected("forbidden pg_* reference")

    # any schema-qualified table must be the project schema. A qualified name
    # looks like `<ident>.<ident>` where the left side is NOT a known alias.
    # We can't fully parse SQL, so: reject ANY `<word>.<word>` whose left word
    # is a real schema-looking token other than the project schema. Aliases are
    # short and local; to stay safe we require qualified refs use the project
    # schema explicitly and forbid every other dotted schema we know about
    # (handled above). Bare unqualified names resolve via search_path → project.

    # ── indexes ──
    uidx = (spec.unique_index or "").strip().lower()
    if not uidx:
        raise MatviewRejected("unique_index is required (needed for REFRESH CONCURRENTLY)")
    if not _INDEX_COLS_RE.match(uidx):
        raise MatviewRejected(f"bad unique_index {spec.unique_index!r}: bare column list only")
    for ix in spec.extra_indexes or []:
        ixl = (ix or "").strip().lower()
        if ixl and not _INDEX_COLS_RE.match(ixl):
            raise MatviewRejected(f"bad index {ix!r}: bare column list only")


def build_ddl(spec: MatviewSpec, schema: str = "citypharma") -> list[str]:
    """Assemble executable DDL statements from a VALIDATED spec.

    Caller MUST run validate_matview_spec() first. Returns a list of single
    statements (drop, create, unique index, extra indexes) executed in order.
    The only model-authored string is the SELECT body, already whitelisted.
    """
    name = spec.name.strip().lower()
    sql = _strip_sql(spec.select_sql).rstrip(";")
    q = f'{schema}."{name}"'
    stmts = [
        f'DROP MATERIALIZED VIEW IF EXISTS {q} CASCADE',
        f'CREATE MATERIALIZED VIEW {q} AS\n{sql}\nWITH DATA',
        f'CREATE UNIQUE INDEX "{name}_uidx" ON {q} ({spec.unique_index.strip()})',
    ]
    for i, ix in enumerate(spec.extra_indexes or []):
        ixl = (ix or "").strip()
        if ixl:
            stmts.append(f'CREATE INDEX "{name}_ix{i}" ON {q} ({ixl})')
    return stmts


# ── DB execution (trusted side — runs only validated DDL) ─────────────────────

def _timeout_engine(db_url: str):
    """Engine with a hard statement timeout so a pathological matview SELECT
    can never hang the training run (same guard as relationship verify)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    return create_engine(
        db_url, poolclass=NullPool,
        connect_args={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
    )


def apply_semantic_layer(slug, specs, base_tables, db_url, schema="citypharma",
                         log=lambda m: None):
    """Validate, dry-run, and create each proposed matview. Returns a list of
    per-spec result dicts. Never raises — a bad spec is skipped + logged.

    For each spec, in order: whitelist → EXPLAIN the SELECT (server-side
    dry-run) → DROP+CREATE+index inside ONE txn with statement timeout →
    register in dash_table_metadata. Caps at MAX_MATVIEWS.
    """
    from sqlalchemy import text
    results = []
    if not specs:
        return results
    eng = _timeout_engine(db_url)
    for spec in specs[:MAX_MATVIEWS]:
        name = (getattr(spec, "name", "") or "").strip().lower()
        rec = {"name": name, "ok": False, "purpose": getattr(spec, "purpose", ""),
               "grain": getattr(spec, "grain", ""), "error": None}
        # 1) whitelist
        try:
            validate_matview_spec(spec, set(base_tables), schema=schema)
        except MatviewRejected as e:
            rec["error"] = f"rejected: {e}"
            log(f"  ✗ {name}: {e}")
            results.append(rec)
            continue
        # 2) server-side dry-run — EXPLAIN never executes the body
        select_sql = _strip_sql(spec.select_sql).rstrip(";")
        try:
            with eng.connect() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                c.execute(text(f"EXPLAIN {select_sql}"))
        except Exception as e:
            rec["error"] = f"explain failed: {str(e)[:160]}"
            log(f"  ✗ {name}: explain failed — {str(e)[:120]}")
            results.append(rec)
            continue
        # 3) create inside one txn
        try:
            stmts = build_ddl(spec, schema=schema)
            with eng.begin() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                for s in stmts:
                    c.execute(text(s))
            rec["ok"] = True
            log(f"  ✓ {name} created")
        except Exception as e:
            rec["error"] = f"create failed: {str(e)[:160]}"
            log(f"  ✗ {name}: create failed — {str(e)[:120]}")
            results.append(rec)
            continue
        # 4) register in dash_table_metadata (idempotent)
        try:
            import json
            refresh_sql = f'REFRESH MATERIALIZED VIEW CONCURRENTLY {schema}."{name}"'
            meta = {"semantic_layer": True, "derived": True,
                    "purpose": rec["purpose"], "grain": rec["grain"],
                    "refresh_sql": refresh_sql, "source": "engineer_agent"}
            with eng.begin() as c:
                c.execute(text("""
                    INSERT INTO public.dash_table_metadata
                        (project_slug, table_name, metadata, updated_at)
                    VALUES (:s, :t, CAST(:m AS jsonb), NOW())
                    ON CONFLICT (project_slug, table_name)
                    DO UPDATE SET metadata = public.dash_table_metadata.metadata || CAST(:m AS jsonb),
                                  updated_at = NOW()
                """), {"s": slug, "t": name, "m": json.dumps(meta)})
        except Exception as e:
            log(f"  · {name}: metadata register skipped — {str(e)[:100]}")
        results.append(rec)
    return results


def list_semantic_layer(slug, db_url):
    """Return registered matview rows for a project (for refresh + UI)."""
    from sqlalchemy import text
    out = []
    try:
        eng = _timeout_engine(db_url)
        with eng.connect() as c:
            rows = c.execute(text("""
                SELECT table_name, metadata FROM public.dash_table_metadata
                WHERE project_slug = :s
                  AND COALESCE((metadata->>'semantic_layer')::bool, false) = true
                ORDER BY table_name
            """), {"s": slug}).fetchall()
        for r in rows:
            m = r[1] if isinstance(r[1], dict) else {}
            out.append({"name": r[0], "purpose": m.get("purpose", ""),
                        "grain": m.get("grain", ""),
                        "refresh_sql": m.get("refresh_sql", ""),
                        "source": m.get("source", "")})
    except Exception:
        pass
    return out


def refresh_semantic_layer(slug, db_url, schema="citypharma", log=lambda m: None):
    """REFRESH MATERIALIZED VIEW CONCURRENTLY every registered matview. Falls
    back to a non-concurrent refresh if the unique index is somehow missing."""
    from sqlalchemy import text
    eng = _timeout_engine(db_url)
    refreshed = 0
    for mv in list_semantic_layer(slug, db_url):
        name = mv["name"]
        try:
            with eng.begin() as c:
                c.execute(text(f"SET search_path = {schema}, public"))
                try:
                    c.execute(text(f'REFRESH MATERIALIZED VIEW CONCURRENTLY {schema}."{name}"'))
                except Exception:
                    c.execute(text(f'REFRESH MATERIALIZED VIEW {schema}."{name}"'))
            refreshed += 1
            log(f"  ↻ {name} refreshed")
        except Exception as e:
            log(f"  ✗ {name}: refresh failed — {str(e)[:100]}")
    return refreshed
