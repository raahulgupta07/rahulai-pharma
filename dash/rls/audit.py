"""RLS audit — write rewrites + blocks to dash_rls_audit.

Sampled 1-in-N for non-blocked rewrites (cap volume). All blocks logged.
"""
import json, logging, random, time, threading, queue
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from db import db_url

_log = logging.getLogger(__name__)
_QUEUE: queue.Queue = queue.Queue(maxsize=10000)
_FLUSH_DAEMON_STARTED = False
_LOCK = threading.Lock()
_SAMPLE_RATE = 0.05  # 5% of non-blocked rewrites; all blocks always logged


def _ensure_table():
    eng = create_engine(db_url, poolclass=NullPool)
    try:
        with eng.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS dash_rls_audit (
                    id BIGSERIAL PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    user_attrs JSONB,
                    external_user TEXT,
                    embed_id TEXT,
                    original_sql TEXT NOT NULL,
                    rewritten_sql TEXT,
                    mode TEXT,
                    blocked BOOL DEFAULT false,
                    block_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """))
            # Phase 2 — additive columns for visibility-policy audit
            conn.execute(text("ALTER TABLE dash_rls_audit ADD COLUMN IF NOT EXISTS policy_version INT"))
            conn.execute(text("ALTER TABLE dash_rls_audit ADD COLUMN IF NOT EXISTS fields_downgraded TEXT[]"))
    finally:
        eng.dispose()


def log_rls_event(project_slug, original_sql, rewritten_sql=None, mode=None,
                  blocked=False, block_reason=None,
                  user_attrs=None, external_user=None, embed_id=None,
                  policy_version=None, fields_downgraded=None):
    """Non-blocking enqueue. Daemon flushes."""
    # Always log policy downgrades; keep sampling for plain rewrites.
    if not blocked and not fields_downgraded and random.random() > _SAMPLE_RATE:
        return  # sampled out
    try:
        _QUEUE.put_nowait({
            "project_slug": project_slug,
            "user_attrs": json.dumps(user_attrs) if user_attrs else None,
            "external_user": external_user,
            "embed_id": embed_id,
            "original_sql": (original_sql or "")[:8000],
            "rewritten_sql": (rewritten_sql or "")[:8000] if rewritten_sql else None,
            "mode": mode,
            "blocked": blocked,
            "block_reason": (block_reason or "")[:500] if block_reason else None,
            "policy_version": policy_version,
            "fields_downgraded": list(fields_downgraded) if fields_downgraded else None,
        })
    except queue.Full:
        pass
    _start_flusher()


def _start_flusher():
    global _FLUSH_DAEMON_STARTED
    if _FLUSH_DAEMON_STARTED:
        return
    with _LOCK:
        if _FLUSH_DAEMON_STARTED:
            return
        _FLUSH_DAEMON_STARTED = True
        t = threading.Thread(target=_flusher_loop, daemon=True, name="rls-audit-flusher")
        t.start()


def _flusher_loop():
    _ensure_table()
    eng = create_engine(db_url, poolclass=NullPool)
    while True:
        try:
            batch = []
            try:
                batch.append(_QUEUE.get(timeout=30))
            except queue.Empty:
                continue
            # drain
            while len(batch) < 200:
                try:
                    batch.append(_QUEUE.get_nowait())
                except queue.Empty:
                    break
            with eng.begin() as conn:
                conn.execute(text("""
                    INSERT INTO dash_rls_audit
                    (project_slug, user_attrs, external_user, embed_id,
                     original_sql, rewritten_sql, mode, blocked, block_reason,
                     policy_version, fields_downgraded)
                    VALUES
                    (:project_slug, CAST(:user_attrs AS JSONB), :external_user, :embed_id,
                     :original_sql, :rewritten_sql, :mode, :blocked, :block_reason,
                     :policy_version, :fields_downgraded)
                """), batch)
        except Exception as e:
            _log.warning(f"rls audit flush failed: {e}")
            time.sleep(5)
