"""Runs FEMM solve jobs on a pool of workers pulling from a shared queue.

Each solve is now an independent `femmcli` subprocess in its own temp
directory (coil_simulation/services/xfemm_backend.py) rather than a shared
Wine/FEMM session, so concurrent solves are safe - unlike the old
single-worker setup this replaces. `WORKER_COUNT` copies of `worker_loop()`
each pull a job id off the shared `QUEUE` and run it on `EXECUTOR`
(sized to match, so WORKER_COUNT jobs can genuinely be solving at once);
bumping `EXECUTOR`'s max_workers alone wouldn't have been enough, since a
single `worker_loop()` awaits each job before pulling the next.

Completed/failed jobs are persisted to RESULTS_ROOT as a flat JSON file (see
_persist_job) - JOBS only needs to hold a job while it's queued/running;
history survives a restart by being read back off disk (see routers/home.py,
routers/runs.py), not by JOBS surviving.
"""

import asyncio
import dataclasses
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ._sim_path import SIM_DIR

RESULTS_ROOT = SIM_DIR / "data" / "results"


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
WORKER_COUNT = os.cpu_count() or 4
EXECUTOR = ThreadPoolExecutor(max_workers=WORKER_COUNT)


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
    """Runs for the lifetime of the app; main.py's lifespan handler starts
    WORKER_COUNT copies of this as separate tasks sharing QUEUE/EXECUTOR."""
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
