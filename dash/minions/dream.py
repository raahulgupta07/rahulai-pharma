"""Dash B5 — Dream nightly maintenance.

Enqueues one of each maintenance minion in sequence (lowest priority,
scheduled now). Real per-handler logic lives in worker.py stubs for now.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from . import queue as q

logger = logging.getLogger(__name__)


DREAM_KINDS = [
    "dedupe_entities",
    "recompile_stale_pages",
    "reembed_stale_chunks",
    "prune_old_evidence",
]


def enqueue_dream_cycle(project: Optional[str]) -> List[int]:
    """Enqueue the full dream maintenance cycle for a project.

    Returns the list of minion IDs created.
    """
    ids: List[int] = []
    for kind in DREAM_KINDS:
        mid = q.enqueue(
            project=project,
            kind=kind,
            payload={"project": project, "source": "dream"},
            priority=9,  # lowest (highest int = lowest priority in this convention)
        )
        ids.append(mid)
    logger.info("dream cycle enqueued project=%s ids=%s", project, ids)
    return ids
