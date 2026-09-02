"""Serializes FEMM/Wine solve jobs onto a single worker.

FEMM-via-Wine is one external process talking over a file-based IPC protocol
(ifile.txt/ofile.txt) - not safe for concurrent access. An asyncio.Queue feeds
a single-worker ThreadPoolExecutor (belt-and-braces: the queue already
serializes submissions, the pool's max_workers=1 makes it structurally
impossible to run two solves at once even if that changes).

Completed/failed jobs are persisted to RESULTS_ROOT as a flat JSON file (see
_persist_job) - JOBS only needs to hold a job while it's queued/running;
history survives a restart by being read back off disk (see routers/home.py,
routers/runs.py), not by JOBS surviving.
"""

import asyncio
import dataclasses
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ._sim_path import SIM_DIR

RESULTS_ROOT = SIM_DIR / "results"


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
    config_id: str
    config_name: str
    distance: float
    coilset: Any = field(repr=False)
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result: dict | None = None  # dataclasses.asdict(SimResult), once done


JOBS: dict[str, Job] = {}
QUEUE: "asyncio.Queue[str]" = asyncio.Queue()
EXECUTOR = ThreadPoolExecutor(max_workers=1)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def submit_job(coilset: Any, config_id: str, config_name: str, distance: float) -> Job:
    job = Job(id=new_job_id(), config_id=config_id, config_name=config_name,
              distance=distance, coilset=coilset)
    JOBS[job.id] = job
    QUEUE.put_nowait(job.id)
    return job


def _persist_job(job: Job) -> None:
    from repositories.run_result_repository import RunResultRepository

    RunResultRepository(run_id=job.id, root=RESULTS_ROOT).save_result_json({
        "run_id": job.id,
        "config_id": job.config_id,
        "config_name": job.config_name,
        "distance": job.distance,
        "status": job.status.value,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
        "result": job.result,
    })


async def worker_loop() -> None:
    """Runs for the lifetime of the app; started from main.py's lifespan handler."""
    loop = asyncio.get_running_loop()
    while True:
        job_id = await QUEUE.get()
        job = JOBS[job_id]
        job.status = JobStatus.RUNNING
        job.started_at = now()
        try:
            # Deferred: coil_simulation's engine.simulate() may not exist yet
            # in this checkout. Deferring the import here means the app and
            # the queue itself still come up fine; only actually running a
            # job fails (as a normal FAILED job) until it lands.
            from engine import simulate

            sim_result = await loop.run_in_executor(EXECUTOR, simulate, job.coilset, job.distance)
            job.result = dataclasses.asdict(sim_result)
            job.status = JobStatus.DONE
        except Exception as exc:
            job.error = repr(exc)
            job.status = JobStatus.FAILED
        finally:
            job.finished_at = now()
            _persist_job(job)
            QUEUE.task_done()
