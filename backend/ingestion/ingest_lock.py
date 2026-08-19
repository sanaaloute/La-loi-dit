"""Single-runner guard for document ingestion.

Both the API startup auto-ingest (``LEGAL_AI_INGEST_ON_STARTUP``) and the CLI
(``python -m backend.ingestion.pipeline ...``) take this lock so the two can
never ingest concurrently — parallel runs double the memory pressure on small
hosts (OOM-killed reindex containers) and race on the shared Milvus index.

The lock is a file in the data dir holding ``<hostname>:<pid>``. A lock is
stale when its owner's PID is gone (same container/host) or — for locks from a
*different* container, whose PID namespace cannot be probed from here — when it
is older than ``FOREIGN_LOCK_TTL_SECONDS``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import time
from pathlib import Path
from typing import Iterator, Union

LOCK_FILENAME = ".ingest-on-startup.lock"

logger = logging.getLogger(__name__)

#: How long a lock from another container/host is trusted as live. Generous on
#: purpose: it must outlast the slowest realistic full reindex (OCR-heavy
#: corpora can take over an hour). A crashed run's lock can always be removed
#: by hand once the TTL is the only thing standing in the way.
FOREIGN_LOCK_TTL_SECONDS = 2 * 3600

#: Same-host age rule (historical startup behavior): a same-host lock whose
#: PID is alive is only treated as stale past this age on the first attempt.
SAME_HOST_STALE_SECONDS = 900


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True


@contextlib.contextmanager
def ingestion_lock(data_dir: Union[str, Path]) -> Iterator[bool]:
    """Yield True when this process holds the ingestion lock.

    Yields False when a live sibling run (another uvicorn worker, the api
    container, or a CLI reindex container) already holds it. The lock file is
    removed on exit, but only when this context acquired it.
    """
    lock = Path(data_dir) / LOCK_FILENAME
    owner = f"{socket.gethostname()}:{os.getpid()}"
    acquired = False
    for attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, owner.encode())
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                raw = lock.read_text(encoding="utf-8", errors="ignore").strip()
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue  # vanished between checks: retry the create
            lock_host, _, pid_text = raw.partition(":")
            if lock_host != socket.gethostname():
                # Foreign container/host: its PID is not probeable from this
                # namespace, so freshness is the only liveness signal.
                held = age < FOREIGN_LOCK_TTL_SECONDS
            elif pid_text.isdigit():
                held = _pid_alive(int(pid_text)) and (
                    age < SAME_HOST_STALE_SECONDS or attempt == 1
                )
            else:
                held = False  # corrupt lock: reclaim
            if held:
                break
            logger.warning("ingestion lock: reclaiming lock from dead run (%s)", raw or "?")
            lock.unlink(missing_ok=True)
    try:
        yield acquired
    finally:
        if acquired:
            lock.unlink(missing_ok=True)
