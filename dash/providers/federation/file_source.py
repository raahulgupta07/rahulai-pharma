"""File source adapter for federation.

During upload, Dash extracts tables from documents:
  - PPTX: slide table shapes
  - PDF: pdfplumber tables
  - DOCX: doc.tables
  - XLSX: every sheet
  - HTML: pd.read_html
  - CSV / Parquet / ODS / XML: native pandas

These extractions land as PostgreSQL tables in proj_{slug} schema
already (handled by upload pipeline).

This adapter builds a *separate* virtual catalog so federation can
distinguish file-derived tables from native data sources, and offer
file_<doc_id>.<table_name> addressing.

Discovers via:
  1. knowledge/{slug}/doc_meta/*.json (per-doc table list)
  2. knowledge/{slug}/source_*/profile/{table}.json (already-trained tables)
  3. dash_documents table (uploaded docs registry)
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path("knowledge")


@dataclass
class FileTable:
    project_slug: str
    doc_id: str           # uploaded document ID or filename slug
    doc_name: str
    doc_type: str         # 'pptx'|'pdf'|'docx'|'xlsx'|'html'|'csv'|'parquet'
    table_name: str       # logical name within doc (e.g. "slide_3_table_1")
    full_address: str     # "file_<doc_id>.<table_name>"
    columns: list[str] = field(default_factory=list)
    row_count: int = 0
    sql_table_name: Optional[str] = None  # actual proj_{slug}.{name} if stored
    metadata: dict = field(default_factory=dict)


@dataclass
class FileSourceCatalog:
    project_slug: str
    tables: list[FileTable] = field(default_factory=list)
    docs_processed: int = 0


def discover(project_slug: str, *, knowledge_dir: Path = KNOWLEDGE_DIR) -> FileSourceCatalog:
    """Scan project's knowledge dir for file-derived tables.

    Idempotent: doesn't modify anything, just builds catalog.
    """
    catalog = FileSourceCatalog(project_slug=project_slug)

    # 1. Scan doc_meta/ JSON files
    proj_dir = knowledge_dir / project_slug
    doc_meta_dir = proj_dir / "doc_meta"
    if doc_meta_dir.exists():
        for p in doc_meta_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                tables = _extract_tables_from_doc_meta(project_slug, p.stem, data)
                catalog.tables.extend(tables)
                catalog.docs_processed += 1
            except Exception as e:
                logger.debug(f"doc_meta parse {p}: {e}")

    # 2. Scan source_*/profile/{table}.json — for tables tied to a source
    for source_dir in proj_dir.glob("source_*"):
        profile_dir = source_dir / "profile"
        if not profile_dir.exists():
            continue
        try:
            source_id = source_dir.name.split("_")[-1]
        except Exception:
            continue
        for p in profile_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if not isinstance(data, dict):
                    continue
                cols = list(data.keys())
                # Row count: max count across cols
                row_count = 0
                for col_data in data.values():
                    if isinstance(col_data, dict):
                        row_count = max(row_count, col_data.get("count", 0) or 0)
                catalog.tables.append(FileTable(
                    project_slug=project_slug,
                    doc_id=f"src{source_id}",
                    doc_name=f"source_{source_id}",
                    doc_type="source",
                    table_name=p.stem,
                    full_address=f"src{source_id}.{p.stem}",
                    columns=cols,
                    row_count=row_count,
                    sql_table_name=p.stem,
                    metadata={"source_id": source_id, "from_profile": True},
                ))
            except Exception:
                continue

    # 3. dash_documents table (DB query)
    try:
        db_tables = _discover_from_db(project_slug)
        # Merge — avoid dupes by full_address
        seen = {t.full_address for t in catalog.tables}
        for t in db_tables:
            if t.full_address not in seen:
                catalog.tables.append(t)
                seen.add(t.full_address)
    except Exception as e:
        logger.debug(f"db discovery: {e}")

    return catalog


def _extract_tables_from_doc_meta(project_slug: str, doc_id: str,
                                     data: dict) -> list[FileTable]:
    """Parse a doc_meta/{doc}.json file. Schema varies by doc type."""
    out: list[FileTable] = []
    doc_name = data.get("filename") or data.get("name") or doc_id
    doc_type = data.get("type") or data.get("ext") or _guess_type(doc_name)

    # Look for tables_extracted block
    tables_data = data.get("tables_extracted") or data.get("tables") or []
    if isinstance(tables_data, list):
        for i, tbl in enumerate(tables_data):
            if not isinstance(tbl, dict):
                continue
            t_name = tbl.get("name") or f"table_{i+1}"
            cols = tbl.get("columns") or tbl.get("headers") or []
            if not isinstance(cols, list):
                cols = []
            out.append(FileTable(
                project_slug=project_slug,
                doc_id=doc_id,
                doc_name=doc_name,
                doc_type=doc_type,
                table_name=t_name,
                full_address=f"file_{doc_id}.{t_name}",
                columns=[str(c) for c in cols],
                row_count=int(tbl.get("rows", 0) or tbl.get("row_count", 0) or 0),
                sql_table_name=tbl.get("sql_table") or tbl.get("table_name"),
                metadata={"doc_id": doc_id, "doc_type": doc_type},
            ))

    return out


def _guess_type(filename: str) -> str:
    if not filename:
        return "unknown"
    fn = filename.lower()
    for ext in ("pptx", "pdf", "docx", "xlsx", "csv", "parquet", "html",
                  "ods", "xml", "json"):
        if fn.endswith(f".{ext}"):
            return ext
    return "unknown"


def _discover_from_db(project_slug: str) -> list[FileTable]:
    """Query dash_documents for uploaded files w/ extracted tables."""
    try:
        from sqlalchemy import text
        from db.session import get_sql_engine
        eng = get_sql_engine()
        with eng.connect() as conn:
            # dash_documents schema may vary — best-effort
            try:
                rows = conn.execute(text(
                    "SELECT id, filename, doc_type, metadata "
                    "FROM public.dash_documents "
                    "WHERE project_slug = :s LIMIT 200"
                ), {"s": project_slug}).fetchall()
            except Exception:
                return []
        out = []
        for r in rows:
            doc_id, filename, doc_type, meta = r
            md = meta or {}
            tables = md.get("tables_extracted") or []
            if not isinstance(tables, list):
                continue
            for i, tbl in enumerate(tables):
                if not isinstance(tbl, dict):
                    continue
                t_name = tbl.get("name") or f"table_{i+1}"
                out.append(FileTable(
                    project_slug=project_slug,
                    doc_id=str(doc_id),
                    doc_name=filename or f"doc_{doc_id}",
                    doc_type=doc_type or _guess_type(filename or ""),
                    table_name=t_name,
                    full_address=f"file_{doc_id}.{t_name}",
                    columns=tbl.get("columns") or [],
                    row_count=int(tbl.get("row_count", 0)),
                    sql_table_name=tbl.get("sql_table"),
                    metadata={"db_doc_id": doc_id},
                ))
        return out
    except Exception as e:
        logger.debug(f"_discover_from_db: {e}")
        return []


def list_tables(project_slug: str) -> list[dict]:
    """Public API: return list of {full_address, columns, doc_name, ...}."""
    catalog = discover(project_slug)
    return [
        {
            "full_address": t.full_address,
            "doc_id": t.doc_id,
            "doc_name": t.doc_name,
            "doc_type": t.doc_type,
            "table_name": t.table_name,
            "columns": t.columns,
            "row_count": t.row_count,
            "sql_table_name": t.sql_table_name,
        }
        for t in catalog.tables
    ]


def get_table(project_slug: str, full_address: str) -> Optional[FileTable]:
    """Lookup by 'file_<doc_id>.<table_name>' or 'src<id>.<table_name>'."""
    catalog = discover(project_slug)
    for t in catalog.tables:
        if t.full_address == full_address:
            return t
    return None
