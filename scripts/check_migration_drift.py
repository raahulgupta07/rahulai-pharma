#!/usr/bin/env python3
"""
Migration Drift CI Gate
========================

Scans Python source (app/, dash/, ml_worker/) for references to tables/views
that don't exist in any migration under db/migrations/*.sql.

Catches the bug class where:
  - Code does `SELECT ... FROM dash.dash_foo`
  - But no migration ever ran `CREATE TABLE dash.dash_foo`
  - → Runtime error in production (e.g. mig 092 missing → workflow_runner crashes)

How it works
------------
1. Walk app/, dash/, ml_worker/ for *.py files.
2. Extract `FROM`, `JOIN`, `INSERT INTO`, `UPDATE` references to
   `<schema>.<table>` (with file:line for each occurrence).
3. Parse all db/migrations/*.sql for `CREATE TABLE [IF NOT EXISTS] [schema.]<name>`,
   `CREATE VIEW`, `CREATE MATERIALIZED VIEW`.
4. Diff: code refs NOT found in migrations → drift list.
5. Subtract scripts/drift_allowlist.txt entries (one per line, # comments OK,
   simple glob wildcards `*` supported, e.g. `pg_*`, `proj_*`).
6. Exit 0 if drift list empty after allowlist; exit 1 if any drift remains.

CLI
---
  python3 scripts/check_migration_drift.py            # check, exit code = drift status
  python3 scripts/check_migration_drift.py --verbose  # print all detected refs + migrations
  python3 scripts/check_migration_drift.py --report   # print summary even on success

Exit codes
----------
  0 = clean (no drift, or all drift is allowlisted)
  1 = drift detected (offending refs printed to stderr)
  2 = script error (paths missing, etc.)

Patterns to remember
--------------------
- Add new tables to db/migrations/ (idempotent: CREATE TABLE IF NOT EXISTS).
- Add planned-but-not-yet-built tables to scripts/drift_allowlist.txt with a
  comment block explaining WHY (dormant feature, external table, etc.).
- This gate is informational only on CI for now; promote to BLOCK once
  allowlist stabilizes and drift count stays at 0 across multiple PRs.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("app", "dash", "ml_worker")
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
ALLOWLIST_FILE = REPO_ROOT / "scripts" / "drift_allowlist.txt"

# -----------------------------------------------------------------------------
# Patterns
# -----------------------------------------------------------------------------

# Match references of the form `<KEYWORD> [schema.]table` inside Python strings.
# Case-insensitive. Captures schema (optional) + table name.
# Schemas considered: dash, public, ai. Everything else assumed external/dynamic.
CODE_REF_PATTERN = re.compile(
    r"\b(FROM|JOIN|INSERT\s+INTO|UPDATE)\s+"
    r"(?:(dash|public|ai)\.)?"  # optional schema (group 2)
    r"([a-z_][a-z0-9_]*)",  # table name (group 3)
    re.IGNORECASE,
)

# Match CREATE TABLE/VIEW in migrations.
MIGRATION_CREATE_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNLOGGED\s+|TEMP\s+|TEMPORARY\s+)?"
    r"(?:TABLE|VIEW|MATERIALIZED\s+VIEW)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:(dash|public|ai|information_schema)\.)?"  # optional schema
    r"([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CodeRef:
    """One reference to a table from Python source."""

    schema: str
    table: str
    file: Path
    line: int

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


# -----------------------------------------------------------------------------
# Allowlist
# -----------------------------------------------------------------------------


def load_allowlist(path: Path) -> set[str]:
    """Load allowlist file. One pattern per line. `#` comments + blanks ignored.

    Supports glob wildcards via fnmatch (e.g. ``pg_*``, ``proj_*.foo``).
    """
    if not path.exists():
        return set()
    entries: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line.lower())
    return entries


def is_allowlisted(qualified_ref: str, allowlist: set[str]) -> bool:
    """Check qualified ref ('schema.table') against allowlist patterns.

    Built-in wildcard matches (always allowlisted):
      - information_schema.*
      - pg_*  (any pg_ prefix in any schema, e.g. pg_class)
      - dash.proj_*, public.proj_*, ai.proj_*  (per-project schemas)
      - dash.user_proj_*, public.user_proj_*
      - dash.user_demo  (demo schema)
    """
    ref = qualified_ref.lower()
    schema, _, table = ref.partition(".")

    # Hardcoded always-allow wildcards (Postgres internals + dynamic project schemas)
    if schema == "information_schema":
        return True
    if table.startswith("pg_"):
        return True
    if table.startswith("proj_"):
        return True
    if table.startswith("user_proj_"):
        return True
    if schema == "user_demo" or table == "user_demo":
        return True

    # Exact matches + glob patterns from allowlist file
    if ref in allowlist:
        return True
    for pattern in allowlist:
        if fnmatch.fnmatchcase(ref, pattern):
            return True
    return False


# -----------------------------------------------------------------------------
# Code scanning
# -----------------------------------------------------------------------------


_PY_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+", re.MULTILINE)


def scan_code_refs(scan_root: Path, dirs: tuple[str, ...]) -> list[CodeRef]:
    """Walk scan_root/<dirs> for *.py files and extract table references.

    Skips lines that look like Python imports (`from X.Y import ...` /
    `import X.Y`) — those produce false-positives because the regex would
    otherwise match the module path `dash.tools` as schema.table.
    """
    refs: list[CodeRef] = []
    for d in dirs:
        base = scan_root / d
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line_num, line in enumerate(content.splitlines(), start=1):
                stripped = line.lstrip()
                # Skip Python import lines — `from dash.tools import ...` etc.
                if stripped.startswith(("from ", "import ")):
                    continue
                # Skip pure comments — docstring/comment refs are prose, not SQL.
                if stripped.startswith("#"):
                    continue
                # Heuristic: SQL refs live inside string literals. If the line
                # has no quote chars, the FROM/JOIN/etc. is prose (docstring
                # body, code comment without #). Skips false-positives like
                # "Reads from dash.tel_* tables" in module docstrings.
                if '"' not in line and "'" not in line:
                    continue
                for match in CODE_REF_PATTERN.finditer(line):
                    schema = (match.group(2) or "").lower()
                    table = match.group(3).lower()
                    # Skip refs without an explicit schema — those are usually
                    # CTE aliases, local subqueries, or pg internals.
                    if not schema:
                        continue
                    refs.append(
                        CodeRef(schema=schema, table=table, file=py_file, line=line_num)
                    )
    return refs


# -----------------------------------------------------------------------------
# Migration parsing
# -----------------------------------------------------------------------------


def parse_migration_tables(migrations_dir: Path) -> set[str]:
    """Parse all *.sql files for CREATE TABLE/VIEW. Returns set of 'schema.table'.

    If no schema in CREATE statement, assumes 'public' (Postgres default
    when search_path is unset; migrations vary, but `dash` schema is always
    explicit when intended).
    """
    tables: set[str] = set()
    if not migrations_dir.exists():
        return tables
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        try:
            content = sql_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in MIGRATION_CREATE_PATTERN.finditer(content):
            schema = (match.group(1) or "public").lower()
            table = match.group(2).lower()
            tables.add(f"{schema}.{table}")
    return tables


def parse_python_create_tables(scan_root: Path, dirs: tuple[str, ...]) -> set[str]:
    """Parse Python source for `CREATE TABLE ...` strings (init_auth-style bootstraps).

    Many platform tables (dash_users, dash_tokens, dash_projects, etc.) are not
    in db/migrations/*.sql — they're created programmatically at app startup
    via `init_auth()` or similar bootstrap functions. This scans Python source
    for those inline DDL strings so they count as "migrated".
    """
    tables: set[str] = set()
    for d in dirs:
        base = scan_root / d
        if not base.exists():
            continue
        for py_file in base.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in MIGRATION_CREATE_PATTERN.finditer(content):
                schema = (match.group(1) or "public").lower()
                table = match.group(2).lower()
                tables.add(f"{schema}.{table}")
    return tables


# -----------------------------------------------------------------------------
# Diff + report
# -----------------------------------------------------------------------------


def find_drift(
    code_refs: list[CodeRef], migration_tables: set[str], allowlist: set[str]
) -> tuple[dict[str, list[CodeRef]], int]:
    """Return (drift_refs_grouped_by_qualified, total_drift_before_allowlist).

    drift_refs_grouped_by_qualified maps 'schema.table' → list of CodeRef sites.
    Only includes refs NOT in migrations AND NOT allowlisted.
    """
    grouped: dict[str, list[CodeRef]] = defaultdict(list)
    total_drift_pre_allowlist = 0
    for ref in code_refs:
        if ref.qualified in migration_tables:
            continue
        total_drift_pre_allowlist += 1
        if is_allowlisted(ref.qualified, allowlist):
            continue
        grouped[ref.qualified].append(ref)
    return grouped, total_drift_pre_allowlist


def print_drift_report(grouped: dict[str, list[CodeRef]]) -> None:
    """Print offending refs grouped by table, with file:line for each site."""
    print("\n" + "=" * 70, file=sys.stderr)
    print("MIGRATION DRIFT DETECTED", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(
        f"\n{len(grouped)} table reference(s) in code do not exist in any migration:\n",
        file=sys.stderr,
    )
    for qualified in sorted(grouped.keys()):
        sites = grouped[qualified]
        print(f"  ✗ {qualified}  ({len(sites)} ref(s))", file=sys.stderr)
        # Show up to 5 sites per table to keep output bounded
        for site in sites[:5]:
            rel = site.file.relative_to(REPO_ROOT) if site.file.is_relative_to(REPO_ROOT) else site.file
            print(f"      {rel}:{site.line}", file=sys.stderr)
        if len(sites) > 5:
            print(f"      ... and {len(sites) - 5} more", file=sys.stderr)
    print(
        "\nFix options:\n"
        "  1. Add a CREATE TABLE statement to a new db/migrations/*.sql file\n"
        "     (use CREATE TABLE IF NOT EXISTS for idempotency).\n"
        "  2. If the ref is intentional (dormant feature, external table, etc.),\n"
        "     add it to scripts/drift_allowlist.txt with a comment explaining why.\n",
        file=sys.stderr,
    )


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", "-v", action="store_true", help="Print all scanned refs + parsed migrations.")
    parser.add_argument("--report", action="store_true", help="Always print summary, even on clean exit.")
    args = parser.parse_args()

    if not MIGRATIONS_DIR.exists():
        print(f"ERROR: migrations dir not found: {MIGRATIONS_DIR}", file=sys.stderr)
        return 2

    code_refs = scan_code_refs(REPO_ROOT, SCAN_DIRS)
    migration_tables = parse_migration_tables(MIGRATIONS_DIR)
    # Many platform tables created programmatically at startup, not in *.sql.
    # Union them in so the drift check matches runtime reality.
    bootstrap_tables = parse_python_create_tables(REPO_ROOT, SCAN_DIRS)
    migration_tables |= bootstrap_tables
    allowlist = load_allowlist(ALLOWLIST_FILE)

    grouped, total_pre = find_drift(code_refs, migration_tables, allowlist)

    if args.verbose:
        print(f"[verbose] Scanned {len(code_refs)} total ref(s).", file=sys.stderr)
        print(f"[verbose] Parsed {len(migration_tables)} migration table(s).", file=sys.stderr)
        print(f"[verbose] Loaded {len(allowlist)} allowlist entries.", file=sys.stderr)

    drift_count = sum(len(sites) for sites in grouped.values())

    if grouped:
        print_drift_report(grouped)
        print(
            f"\nSummary: {len(code_refs)} refs scanned · {len(migration_tables)} migrations · "
            f"{total_pre} drift before allowlist · {drift_count} drift after allowlist · "
            f"{len(allowlist)} allowlist entries",
            file=sys.stderr,
        )
        return 1

    if args.report or args.verbose:
        print(
            f"✓ No migration drift.\n"
            f"  Refs scanned:           {len(code_refs)}\n"
            f"  Migrations parsed:      {len(migration_tables)}\n"
            f"  Drift before allowlist: {total_pre}\n"
            f"  Drift after allowlist:  0\n"
            f"  Allowlist entries:      {len(allowlist)}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
