"""
dash.ingest — Staged data-ingest pipeline module.

Public API
----------
content_hash   : compute sha256 hex digest of file bytes or from a path
new_batch_id   : generate a unique batch identifier
staging_root   : return the staging root directory for a project
batch_dir      : return the directory for a specific batch
stage_file     : copy a file into a batch and build its manifest entry
write_manifest : atomically persist the manifest JSON + upsert DB rows
read_manifest  : load a manifest from disk
list_batches   : newest-first list of batch manifests for a project
quarantine_file: move a staged file into quarantine and update manifest
content_hash_seen: check if a content hash exists in a target table column
"""

from .content import content_hash
from .staging import (
    new_batch_id,
    staging_root,
    batch_dir,
    stage_file,
    write_manifest,
    read_manifest,
    list_batches,
    quarantine_file,
    content_hash_seen,
)
from .contract import (
    pg_type,
    detect_load_key,
    infer_contract,
    get_contract,
    save_contract,
    check_against_contract,
    evolve_contract,
)
from .loader import (
    compute_row_key,
    stamp_lineage,
    ensure_columns,
    table_exists,
    file_hash_seen,
    delete_where_period,
    delete_where_batch,
    promote_file,
)

__all__ = [
    "content_hash",
    "new_batch_id",
    "staging_root",
    "batch_dir",
    "stage_file",
    "write_manifest",
    "read_manifest",
    "list_batches",
    "quarantine_file",
    "content_hash_seen",
    "pg_type",
    "detect_load_key",
    "infer_contract",
    "get_contract",
    "save_contract",
    "check_against_contract",
    "evolve_contract",
    "compute_row_key",
    "stamp_lineage",
    "ensure_columns",
    "table_exists",
    "file_hash_seen",
    "delete_where_period",
    "delete_where_batch",
    "promote_file",
]
