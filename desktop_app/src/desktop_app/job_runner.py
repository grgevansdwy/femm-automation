"""Runs FEMM solve jobs on a pool of worker threads.

Each solve is an independent `femmcli` subprocess in its own temp directory
(coil_simulation/services/xfemm_backend.py), so concurrent solves are safe -
sized the same way webapp/src/webapp/job_runner.py now is
(WORKER_COUNT = os.cpu_count()), not the single-worker model the old
Wine/FEMM backend required.

No asyncio here: unlike the webapp, there's no event loop - the GUI's own
per-frame loop calls poll() to sync each job's status from its Future.
Completed/failed jobs are persisted to RESULTS_ROOT as a flat JSON file, the
same schema webapp/src/webapp/job_runner.py writes, via the same
RunResultRepository - so results stay readable by/comparable with the webapp.
"""

import dataclasses
import os
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from ._sim_path import SIM_DIR

RESULTS_ROOT = SIM_DIR / "data" / "results"

WORKER_COUNT = os.cpu_count() or 4
EXECUTOR = ThreadPoolExecutor(max_workers=WORKER_COUNT)


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

    def as_dict(self) -> dict:
        """Same shape as a persisted result JSON - lets Run History merge
        live jobs and disk-read history through one code path."""
        return {
            "run_id": self.id,
            "config_id": self.config_id,
            "config_name": self.config_name,
            "distance": self.distance,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "result": self.result,
        }


class JobRunner:
    """Owns the in-flight/recently-finished job dict. One instance per app run."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._futures: dict[str, Future] = {}

    def submit(self, coilset: Any, config_id: str, config_name: str, distance: float) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], config_id=config_id, config_name=config_name,
                   distance=distance, coilset=coilset)
        self.jobs[job.id] = job
        self._futures[job.id] = EXECUTOR.submit(self._run, job.id)
        return job

    def poll(self) -> None:
        """Call once per frame: syncs any just-finished job's status/result."""
        for job_id, fut in list(self._futures.items()):
            if fut.done():
                del self._futures[job_id]  # already applied by _run/_finish

    def _run(self, job_id: str) -> None:
        job = self.jobs[job_id]
        job.status = JobStatus.RUNNING
        job.started_at = now()
        try:
            from engine import simulate

            sim_result = simulate(job.coilset, job.distance)
            job.result = dataclasses.asdict(sim_result)
            job.status = JobStatus.DONE
        except Exception as exc:
            job.error = repr(exc)
            job.status = JobStatus.FAILED
        finally:
            job.finished_at = now()
            self._persist(job)

    @staticmethod
    def _persist(job: Job) -> None:
        from repositories.run_result_repository import RunResultRepository

        RunResultRepository(run_id=job.id, root=RESULTS_ROOT).save_result_json(job.as_dict())
