"""Bounded asynchronous jobs for mobile model requests."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_LOCK = threading.RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, min(4, int(os.getenv("SPIRITKIN_IOS_JOB_WORKERS", "2") or 2))),
    thread_name_prefix="spiritkin-ios-job",
)
_MAX_JOBS = 80
_TTL_SECONDS = 15 * 60


def _prune() -> None:
    cutoff = time.time() - _TTL_SECONDS
    for job_id, job in list(_JOBS.items()):
        if float(job.get("updated_at") or 0) < cutoff:
            _JOBS.pop(job_id, None)
    while len(_JOBS) > _MAX_JOBS:
        oldest = min(_JOBS.items(), key=lambda item: float(item[1].get("updated_at") or 0))[0]
        _JOBS.pop(oldest, None)


def submit_ios_job(work: Callable[[], dict[str, Any]], *, workspace_id: str) -> dict[str, Any]:
    job_id = f"iosjob_{uuid.uuid4().hex[:20]}"
    now = time.time()
    with _LOCK:
        _prune()
        _JOBS[job_id] = {"job_id": job_id, "workspace_id": workspace_id, "status": "queued", "created_at": now, "updated_at": now}

    def run() -> None:
        with _LOCK:
            job = _JOBS.get(job_id)
            if not job:
                return
            job.update({"status": "running", "updated_at": time.time()})
        try:
            result = work()
            with _LOCK:
                job = _JOBS.get(job_id)
                if job:
                    job.update({"status": "completed", "result": result, "updated_at": time.time()})
        except Exception as exc:
            with _LOCK:
                job = _JOBS.get(job_id)
                if job:
                    job.update({"status": "failed", "error": str(exc)[:500], "error_type": exc.__class__.__name__, "updated_at": time.time()})

    _EXECUTOR.submit(run)
    return ios_job_snapshot(job_id) or {"job_id": job_id, "workspace_id": workspace_id, "status": "queued"}


def ios_job_snapshot(job_id: str, *, workspace_id: str = "") -> dict[str, Any] | None:
    with _LOCK:
        _prune()
        job = _JOBS.get(str(job_id or ""))
        if not job or (workspace_id and job.get("workspace_id") != workspace_id):
            return None
        return dict(job)
