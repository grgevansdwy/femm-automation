"""Serializes FEMM/Wine solve jobs onto a single worker.

FEMM-via-Wine is one external process talking over a file-based IPC protocol
(ifile.txt/ofile.txt) - not safe for concurrent access. An asyncio.Queue feeds
a single-worker ThreadPoolExecutor (belt-and-braces: the queue already
serializes submissions, the pool's max_workers=1 makes it structurally
impossible to run two solves at once even if that changes).
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ._noelle_path import NOELLE_DIR

RESULTS_ROOT = NOELLE_DIR / "results"
RUN_ID_DIR_PREFIX = "ID"  # matches noelle's `results/ID<run_id>/` directory convention


def now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    id: str
    coilset_labels: list[str]
    distances: list[float]
    coilsets: list[Any] = field(repr=False)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_dir: Path | None = None

    @property
    def log_path(self) -> Path | None:
        if self.result_dir is None:
            return None
        return self.result_dir / f"log_{self.id}.txt"


JOBS: dict[str, Job] = {}
QUEUE: "asyncio.Queue[str]" = asyncio.Queue()
EXECUTOR = ThreadPoolExecutor(max_workers=1)


def new_job_id() -> str:
    # Used as the run_id passed straight through to RunResultRepository, which
    # sidesteps its own day-hour-minute default run_id (collides across
    # months) for every web-originated run.
    return uuid.uuid4().hex[:12]


def submit_job(coilsets: list[Any], coilset_labels: list[str], distances: list[float]) -> Job:
    job = Job(id=new_job_id(), coilset_labels=coilset_labels, distances=distances, coilsets=coilsets)
    JOBS[job.id] = job
    QUEUE.put_nowait(job.id)
    return job


async def worker_loop() -> None:
    """Runs for the lifetime of the app; started from main.py's lifespan handler."""
    loop = asyncio.get_running_loop()
    while True:
        job_id = await QUEUE.get()
        job = JOBS[job_id]
        job.status = JobStatus.RUNNING
        job.started_at = now()
        # Predicted before the solve finishes so the log-tail route can follow
        # log_<id>.txt while the run is still in progress.
        job.result_dir = RESULTS_ROOT / f"{RUN_ID_DIR_PREFIX}{job.id}"
        try:
            # Deferred: noelle's run_comparison() (plan section 2) may not
            # exist yet in this checkout. Deferring the import here means the
            # app and the queue itself still come up fine; only actually
            # running a job fails (as a normal FAILED job) until it lands.
            from run_comparison import run_comparison

            run, results = await loop.run_in_executor(
                EXECUTOR,
                run_comparison,
                job.coilsets,
                job.distances,
                job.id,
                RESULTS_ROOT,
            )
            # Reconcile with whatever RunResultRepository actually used, in
            # case it differs from our predicted path above.
            job.result_dir = getattr(results, "dir", job.result_dir)
            job.status = JobStatus.DONE
        except Exception as exc:
            job.error = repr(exc)
            job.status = JobStatus.FAILED
        finally:
            job.finished_at = now()
            QUEUE.task_done()
