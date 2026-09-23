"""Runs FEMM solve jobs on a pool of worker threads.

Each solve is an independent `femmcli` subprocess in its own temp directory
(backend/engine/services/xfemm_backend.py), so concurrent solves are safe -
sized the same way webapp/src/webapp/job_runner.py now is
(WORKER_COUNT = os.cpu_count()), not the single-worker model the old
Wine/FEMM backend required.

No asyncio here: unlike the webapp, there's no event loop - the GUI's own
per-frame loop calls poll() to sync each job's status from its Future.
Completed/failed jobs are persisted under RESULTS_ROOT (backend's
data/ directory - see RunResultRepository's root= for why it's the whole
data/ dir and not a results/ subdirectory) via the same RunResultRepository,
the same schema webapp/src/webapp/job_runner.py writes - so results stay
readable by/comparable with the webapp.

Every persisted result also carries a `coilset_hash` (see
coilset_signature() below): a hash of the exact geometry + distance that was
solved. find_cached() uses it so re-submitting the same coilset+distance
reuses the existing result instead of re-running femmcli - solves are
several seconds each and pure functions of (coilset, distance), so there's
nothing to gain from re-solving.

Results also carry `last_used_at`, separate from `finished_at`: `finished_at`
is when femmcli actually solved it and never changes; `last_used_at` is
bumped by touch() every time a cache hit reuses that result, so Run History
can sort/show "recently used" without pretending a cached hit was a fresh
solve.
"""

import dataclasses
import hashlib
import json
import os
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .._sim_path import SIM_DIR

RESULTS_ROOT = SIM_DIR / "data"


def coilset_signature(coilset: Any, distance: float) -> str:
    # config_name is a label, not geometry - renaming a config mustn't make
    # its cached runs look like a different coil.
    fields = dataclasses.asdict(coilset)
    fields.pop("config_name", None)
    payload = json.dumps(
        {"coilset": fields, "distance": distance}, sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()

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
    last_used_at: datetime | None = None  # bumped by JobRunner.touch() on a cache hit
    error: str | None = None
    result: dict | None = None  # dataclasses.asdict(SimResult), once done

    def as_dict(self) -> dict:
        """Same shape as a persisted result JSON - lets Run History merge
        live jobs and disk-read history through one code path."""
        finished = self.finished_at.isoformat() if self.finished_at else None
        last_used = self.last_used_at.isoformat() if self.last_used_at else (finished or self.created_at.isoformat())
        return {
            "run_id": self.id,
            "config_id": self.config_id,
            "config_name": self.config_name,
            "distance": self.distance,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": finished,
            "last_used_at": last_used,
            "error": self.error,
            "result": self.result,
            "coilset_hash": coilset_signature(self.coilset, self.distance),
        }


class JobRunner:
    """Owns the in-flight/recently-finished job dict. One instance per app run."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._futures: dict[str, Future] = {}

    @staticmethod
    def find_cached(coilset: Any, distance: float) -> dict | None:
        """A previously-persisted, successful result for this exact
        coilset+distance, if one exists - None otherwise. Results written
        before coilset_hash existed just won't match, which is fine: they
        simply aren't cache 
            sim_result = simulate(job.coilset, job.distance)
            job.result = dataclasses.asdict(sim_result)hits until re-run once under this scheme."""
        from engine.repositories.run_result_repository import RunResultRepository

        signature = coilset_signature(coilset, distance)
        for row in RunResultRepository.list_results(root=RESULTS_ROOT):
            if row.get("status") == JobStatus.DONE.value and row.get("coilset_hash") == signature:
                return row
        return None

    def touch(self, row: dict) -> dict:
        """Bumps a cached result's last_used_at to now and re-persists it -
        called when find_cached()'s result is actually reused, so a reused
        run rises back to the top of Run History instead of sitting wherever
        its original solve time put it. Leaves finished_at (the true solve
        time) untouched.

        Also updates self.jobs[run_id] if that job is still resident in
        memory - otherwise history_rows() would immediately overwrite this
        disk update with the job's own (now-stale) as_dict() on the next
        merge, since a finished job is never dropped from self.jobs."""
        from engine.repositories.run_result_repository import RunResultRepository

        touched_at = now()
        row = dict(row)
        row["last_used_at"] = touched_at.isoformat()
        RunResultRepository(run_id=row["run_id"], root=RESULTS_ROOT).save_result_json(row)

        job = self.jobs.get(row["run_id"])
        if job is not None:
            job.last_used_at = touched_at

        return row

    def submit(self, coilset: Any, config_id: str, config_name: str, distance: float) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], config_id=config_id, config_name=config_name,
                   distance=distance, coilset=coilset)
        self.jobs[job.id] = job
        self._futures[job.id] = EXECUTOR.submit(self._run, job.id)
        return job

    def cancel(self, job_id: str) -> bool:
        """Drops a job that hasn't started solving yet - True if it was
        dropped. A running or finished job is left alone (a solve can't be
        interrupted, and its result is still a valid cache entry). A dropped
        job is marked failed so anything waiting on it (scorer.wait_for_jobs)
        stops waiting, and is removed from self.jobs and never persisted, so
        it doesn't show up in Run History."""
        fut = self._futures.get(job_id)
        if fut is None or not fut.cancel():
            return False
        self._futures.pop(job_id, None)
        job = self.jobs.pop(job_id, None)
        if job is not None:
            job.error = "cancelled"
            job.finished_at = now()
            job.status = JobStatus.FAILED
        return True

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
            from engine.core import simulate
            sim_result = simulate(job.coilset, job.distance)
            job.result = dataclasses.asdict(sim_result)
            job.status = JobStatus.DONE
        except Exception as exc:
            job.error = repr(exc)
            job.status = JobStatus.FAILED
        finally:
            job.finished_at = now()
            job.last_used_at = job.finished_at
            self._persist(job)

    @staticmethod
    def _persist(job: Job) -> None:
        from engine.repositories.run_result_repository import RunResultRepository

        RunResultRepository(run_id=job.id, root=RESULTS_ROOT).save_result_json(job.as_dict())
