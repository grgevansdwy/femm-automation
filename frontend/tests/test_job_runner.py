import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from engine.repositories.coilset_repository import get_coilset
from engine.repositories.run_result_repository import RunResultRepository

from frontend.services.job_runner import JobRunner, JobStatus, coilset_signature


def _wait_for(job, timeout=60):
    deadline = time.time() + timeout
    while job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(f"job {job.id} still {job.status} after {timeout}s")
        time.sleep(0.05)


def test_submit_runs_a_real_simulation_and_persists_result():
    runner = JobRunner()
    coilset = get_coilset("base_coil")

    job = runner.submit(coilset, config_id="base_coil", config_name="base_coil", distance=5.0)
    _wait_for(job)

    assert job.status == JobStatus.DONE
    assert job.error is None
    assert job.result is not None
    assert job.result["L_loop"] > 0

    from frontend.services.job_runner import RESULTS_ROOT
    persisted = RunResultRepository.load_result(job.id, root=RESULTS_ROOT)
    assert persisted is not None
    assert persisted["status"] == "done"
    assert persisted["result"]["L_loop"] == job.result["L_loop"]


def test_submit_with_invalid_distance_type_fails_cleanly():
    runner = JobRunner()
    coilset = get_coilset("base_coil")

    job = runner.submit(coilset, config_id="base_coil", config_name="base_coil", distance="not-a-number")
    _wait_for(job)

    assert job.status == JobStatus.FAILED
    assert job.error is not None


def test_find_cached_returns_none_when_nothing_matches():
    runner = JobRunner()
    coilset = get_coilset("base_coil")
    distance = round(random.uniform(1.0, 50.0), 6)  # near-certainly never solved before

    assert runner.find_cached(coilset, distance) is None


def test_find_cached_returns_the_persisted_result_after_a_matching_run():
    runner = JobRunner()
    coilset = get_coilset("base_coil")
    distance = round(random.uniform(1.0, 50.0), 6)

    job = runner.submit(coilset, config_id="base_coil", config_name="base_coil", distance=distance)
    _wait_for(job)

    cached = runner.find_cached(coilset, distance)
    assert cached is not None
    assert cached["run_id"] == job.id
    assert cached["result"]["L_loop"] == job.result["L_loop"]

    # A different distance on the same coilset must not match.
    assert runner.find_cached(coilset, distance + 1.0) is None


def test_touch_bumps_last_used_at_without_changing_finished_at():
    runner = JobRunner()
    coilset = get_coilset("base_coil")
    distance = round(random.uniform(1.0, 50.0), 6)

    job = runner.submit(coilset, config_id="base_coil", config_name="base_coil", distance=distance)
    _wait_for(job)
    cached = runner.find_cached(coilset, distance)
    original_last_used_at = cached["last_used_at"]
    original_finished_at = cached["finished_at"]

    time.sleep(0.01)
    touched = runner.touch(cached)

    assert touched["finished_at"] == original_finished_at  # true solve time never changes
    assert touched["last_used_at"] > original_last_used_at

    from frontend.services.job_runner import RESULTS_ROOT
    persisted = RunResultRepository.load_result(job.id, root=RESULTS_ROOT)
    assert persisted["last_used_at"] == touched["last_used_at"]  # re-persisted to disk


def test_signature_ignores_config_name():
    """Renaming a config is a label change, not a geometry change - it must
    not turn its cached runs into misses or flag them as edited."""
    import dataclasses

    coilset = get_coilset("base_coil")
    renamed = dataclasses.replace(coilset, config_name="something else")
    assert coilset_signature(coilset, 5.0) == coilset_signature(renamed, 5.0)
    assert coilset_signature(coilset, 5.0) != coilset_signature(dataclasses.replace(coilset, loop_r=66), 5.0)


def test_cancel_drops_a_queued_job_but_never_a_running_one(monkeypatch):
    """Restart on an unfinished AI search drops its not-yet-started solves.
    One worker, held busy, so the submitted solve is guaranteed queued."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import frontend.services.job_runner as job_runner_module
    from agent.femm_agent.services.scorer import wait_for_jobs

    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(job_runner_module, "EXECUTOR", executor)
    release = threading.Event()
    blocker = executor.submit(release.wait)

    runner = JobRunner()
    job = runner.submit(get_coilset("base_coil"), config_id="agent", config_name="agent-candidate", distance=5.0)
    try:
        assert runner.cancel(job.id) is True
        assert job.status == JobStatus.FAILED and job.error == "cancelled"
        assert job.id not in runner.jobs, "a dropped job must not show up in Run History"
        try:
            wait_for_jobs({5.0: job})  # must not hang waiting on a job that will never run
        except RuntimeError as exc:
            assert "cancelled" in str(exc)
        else:
            raise AssertionError("waiting on a cancelled job should raise")
        assert runner.cancel(job.id) is False  # already gone
    finally:
        release.set()
        blocker.result(timeout=5)
        executor.shutdown(wait=True)
    assert RunResultRepository.list_results(root=job_runner_module.RESULTS_ROOT) == [], "nothing persisted"
