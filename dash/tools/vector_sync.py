"""Vector sync — auto re-embed on doc/data updates.

Background queue + worker that hashes text content, skips no-op writes, and
upserts ``(project_slug, namespace, source_id)`` rows into ``dash_vectors``.

Public surface:
    VECTOR_SYNC                — module-level singleton
    VECTOR_SYNC.enqueue(...)   — coroutine, hash-check + push
    VECTOR_SYNC.start()        — idempotent worker boot
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the async helper's native batch path."""
    from dash.tools.embeddings_helper import embed_batch as _embed_batch
    return await _embed_batch(texts)


class VectorSync:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._worker_task: asyncio.Task | None = None

    async def enqueue(
        self,
        slug: str,
        namespace: str,
        source_id: str,
        text: str,
        scope_attrs: dict | None = None,
        metadata: dict | None = None,
        source_table: str | None = None,
    ) -> bool:
        """Hash text, skip if matches stored hash. Else enqueue for embed.

        Returns True if a new embedding job was queued, False if skipped (no
        change) or on transient failure.
        """
        if not text or not text.strip():
            return False
        try:
            from db.session import get_sql_engine
            from sqlalchemy import text as sql

            h = hashlib.sha256(text.encode("utf-8")).hexdigest()
            try:
                with get_sql_engine().connect() as c:
                    row = c.execute(
                        sql(
                            "SELECT text_hash FROM dash_vectors "
                            "WHERE project_slug=:p AND namespace=:n AND source_id=:s"
                        ),
                        {"p": slug, "n": namespace, "s": source_id},
                    ).fetchone()
                if row and row[0] == h:
                    return False
            except Exception as e:
                # Table may not exist yet, or transient — fall through to enqueue.
                log.debug("vector_sync: hash-check skipped (%s)", e)

            try:
                self.queue.put_nowait(
                    {
                        "slug": slug,
                        "namespace": namespace,
                        "source_id": source_id,
                        "text": text,
                        "scope_attrs": scope_attrs or {},
                        "metadata": metadata or {},
                        "source_table": source_table,
                    }
                )
            except asyncio.QueueFull:
                log.warning("vector_sync: queue full, dropping %s/%s/%s",
                            slug, namespace, source_id)
                return False
            return True
        except Exception:
            log.exception("vector_sync: enqueue failed")
            return False

    async def worker(self) -> None:
        from db import get_sql_engine
        from sqlalchemy import text as sql

        while True:
            batch: list[dict[str, Any]] = []
            try:
                batch.append(await self.queue.get())
                while len(batch) < 64:
                    batch.append(await asyncio.wait_for(self.queue.get(), 1.0))
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("vector_sync: queue read failed")

            if not batch:
                continue

            try:
                vecs = await embed_batch([r["text"] for r in batch])
                eng = get_sql_engine()
                with eng.begin() as c:
                    for r, v in zip(batch, vecs):
                        h = hashlib.sha256(r["text"].encode("utf-8")).hexdigest()
                        c.execute(
                            sql(
                                """
                                INSERT INTO dash_vectors
                                  (project_slug, namespace, source_id, source_table,
                                   scope_attrs, text, text_hash, embedding, metadata)
                                VALUES (:p, :n, :s, :tbl,
                                        CAST(:scope AS jsonb), :txt, :h,
                                        CAST(:v AS vector), CAST(:m AS jsonb))
                                ON CONFLICT (project_slug, namespace, source_id) DO UPDATE SET
                                  text = EXCLUDED.text,
                                  text_hash = EXCLUDED.text_hash,
                                  embedding = EXCLUDED.embedding,
                                  scope_attrs = EXCLUDED.scope_attrs,
                                  metadata = EXCLUDED.metadata,
                                  updated_at = NOW()
                                """
                            ),
                            {
                                "p": r["slug"],
                                "n": r["namespace"],
                                "s": r["source_id"],
                                "tbl": r.get("source_table"),
                                "scope": json.dumps(r["scope_attrs"]),
                                "txt": r["text"],
                                "h": h,
                                "v": _vec_literal(v),
                                "m": json.dumps(r["metadata"]),
                            },
                        )
            except Exception as e:
                log.exception("vector_sync worker batch failed: %s", e)

    def start(self) -> None:
        """Boot the worker on the running loop. Idempotent."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._worker_task = loop.create_task(self.worker())


def _vec_literal(vec: list[float]) -> str:
    """pgvector literal: '[0.1,0.2,...]'."""
    try:
        from dash.tools.embeddings_helper import vec_to_pg
        return vec_to_pg(vec)
    except Exception:
        return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


VECTOR_SYNC = VectorSync()

__all__ = ["VECTOR_SYNC", "VectorSync", "embed_batch"]
