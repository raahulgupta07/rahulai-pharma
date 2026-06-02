"""
Gunicorn config — Issue #13 hardening.

Why gunicorn (not bare uvicorn --workers): uvicorn workers all share the same
process group ENV, so we can't stamp a unique WORKER_RANK per worker from
uvicorn's CLI. Gunicorn exposes a `post_fork` hook that fires inside each
forked worker before app import — perfect place to mutate os.environ so the
lifespan in app/main.py can read WORKER_RANK and gate background daemons.

End result: only the worker with WORKER_RANK=0 spawns daemons; the other N-1
workers serve traffic only. No more 8× duplicate cron/cost-guard/reembed loops.

Tunables (env):
    PORT             bind port (default 8000)
    WORKERS          worker count (default = cpu_count, min 2)
    GUNICORN_TIMEOUT request timeout seconds (default 120)
"""

import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WORKERS", max(2, multiprocessing.cpu_count())))
worker_class = "uvicorn.workers.UvicornWorker"
# tmpfs heartbeat avoids slow-disk worker timeouts on container hosts
worker_tmp_dir = "/dev/shm"
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
keepalive = 5
graceful_timeout = 30
# preload=False so each worker initializes its own lifespan (daemons,
# engines, ML thread). Required for WORKER_RANK gating to actually work.
preload_app = False
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")


def post_fork(server, worker):
    """Stamp WORKER_RANK on each forked worker.

    Gunicorn's `worker.age` is a monotonic counter starting at 1 for the first
    forked worker. We subtract 1 so rank is 0-indexed — daemon gate in
    app/main.py lifespan requires rank 0 to spawn background loops.
    """
    rank = max(0, worker.age - 1)
    os.environ["WORKER_RANK"] = str(rank)
    server.log.info(
        f"worker {worker.age} (pid {worker.pid}) stamped WORKER_RANK={rank}"
    )
