#!/usr/bin/env python3
"""Audit `dash.*` vs `public.*` schema usage across SQL migrations.

Issue #28: documents intent, surfaces accidental drift.

Walks every file in ``db/migrations/*.sql`` and reports:

- Tables referenced in BOTH ``public.*`` and ``dash.*`` (split-brain risk).
- Tables referenced unqualified (``CREATE TABLE foo``) — resolves whichever
  schema is first on the search path, usually a footgun.
- Cross-migration conflicts: same table CREATEd in two different schemas.

Run from repo root::

    python scripts/audit_schema_split.py

Exits 0 on the documented baseline, 1 if a new conflict is introduced.
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# Repo-root resolution: this file lives at <root>/scripts/audit_schema_split.py
ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "db" / "migrations"

# Documented baseline — these split-brain entries are intentional per
# docs/SCHEMA_LAYOUT.md. Adding a new one requires updating that doc.
KNOWN_INTENTIONAL_SPLITS = {
    "dash_knowledge_triples",  # public = chat-time SPO writer; dash = bi-temporal copy
    "dash_company_brain",      # lives in public; bi-temporal ALTERs DO-block target either schema
}

# Strip SQL comments before regex matches.
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

# Table-reference patterns. We only look at CREATE TABLE / ALTER TABLE / DROP
# TABLE / REFERENCES to keep noise low.
_CREATE_RE = re.compile(
    r"\bCREATE\s+(?:UNLOGGED\s+|TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:(?P<schema>[a-zA-Z_][\w]*)\.)?(?P<table>[a-zA-Z_][\w]*)",
    re.IGNORECASE,
)
_ALTER_RE = re.compile(
    r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
    r"(?:(?P<schema>[a-zA-Z_][\w]*)\.)?(?P<table>[a-zA-Z_][\w]*)",
    re.IGNORECASE,
)
_REF_RE = re.compile(
    r"\bREFERENCES\s+(?:(?P<schema>[a-zA-Z_][\w]*)\.)?(?P<table>[a-zA-Z_][\w]*)",
    re.IGNORECASE,
)


def scan_file(path: Path):
    """Yield (kind, schema, table) tuples found in one SQL file."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  ! could not read {path.name}: {e}", file=sys.stderr)
        return
    sql = _COMMENT_RE.sub(" ", raw)
    for kind, pat in (("create", _CREATE_RE), ("alter", _ALTER_RE), ("ref", _REF_RE)):
        for m in pat.finditer(sql):
            schema = (m.group("schema") or "").lower() or None
            table = m.group("table").lower()
            yield kind, schema, table


def main() -> int:
    if not MIG_DIR.is_dir():
        print(f"!! migration dir not found: {MIG_DIR}")
        return 2

    # table -> {schema (or "<unqualified>") -> set(files)}
    by_table: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    # Track CREATE locations separately for conflict detection.
    created_in: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    files = sorted(MIG_DIR.glob("*.sql"))
    if not files:
        print(f"!! no .sql files under {MIG_DIR}")
        return 2

    for f in files:
        for kind, schema, table in scan_file(f):
            key = schema or "<unqualified>"
            by_table[table][key].add(f.name)
            if kind == "create" and schema is not None:
                created_in[table][schema].add(f.name)

    # ---------------- Report --------------------
    n_total = len(by_table)
    splits: list[tuple[str, dict[str, set[str]]]] = []
    unqualified: list[tuple[str, set[str]]] = []
    create_conflicts: list[tuple[str, dict[str, set[str]]]] = []

    for table, schemas in by_table.items():
        qual = {s: fs for s, fs in schemas.items() if s != "<unqualified>"}
        if len(qual) >= 2:
            splits.append((table, qual))
        if "<unqualified>" in schemas:
            unqualified.append((table, schemas["<unqualified>"]))

    for table, by_schema in created_in.items():
        if len(by_schema) >= 2:
            create_conflicts.append((table, by_schema))

    print(f"\n== Schema-split audit ({n_total} tables, {len(files)} migrations) ==\n")

    if splits:
        print(f"-- {len(splits)} table(s) appear in MULTIPLE schemas --")
        new_splits = []
        for table, qual in sorted(splits):
            tag = "INTENTIONAL" if table in KNOWN_INTENTIONAL_SPLITS else "NEW (review!)"
            print(f"  [{tag}] {table}")
            for schema, fnames in sorted(qual.items()):
                print(f"      {schema}.{table}  in  {', '.join(sorted(fnames))}")
            if table not in KNOWN_INTENTIONAL_SPLITS:
                new_splits.append(table)
        print()
    else:
        new_splits = []
        print("-- no split-brain tables (good) --\n")

    if unqualified:
        print(f"-- {len(unqualified)} unqualified table reference(s) --")
        print("   (resolve via search_path — usually a footgun)\n")
        for table, fnames in sorted(unqualified)[:25]:
            print(f"   {table}  in  {', '.join(sorted(fnames))}")
        if len(unqualified) > 25:
            print(f"   ... and {len(unqualified) - 25} more")
        print()

    if create_conflicts:
        print(f"-- {len(create_conflicts)} CREATE TABLE conflict(s) --")
        for table, by_schema in sorted(create_conflicts):
            tag = "INTENTIONAL" if table in KNOWN_INTENTIONAL_SPLITS else "NEW (review!)"
            print(f"  [{tag}] {table} CREATEd in:")
            for schema, fnames in sorted(by_schema.items()):
                print(f"      {schema}: {', '.join(sorted(fnames))}")
        print()

    # Exit non-zero only on NEW conflicts (anything outside the documented set).
    new_create_conflicts = [
        t for t, _ in create_conflicts if t not in KNOWN_INTENTIONAL_SPLITS
    ]
    if new_splits or new_create_conflicts:
        print(
            f"\n!! {len(new_splits)} new split(s), "
            f"{len(new_create_conflicts)} new CREATE conflict(s). "
            "Update docs/SCHEMA_LAYOUT.md + KNOWN_INTENTIONAL_SPLITS in this "
            "script before merging."
        )
        return 1
    print("OK — no new schema conflicts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
