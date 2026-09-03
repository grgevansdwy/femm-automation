import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from repositories.coilset_repository import get_coilset
from repositories.run_result_repository import RunResultRepository

from gui.job_runner import JobRunner, JobStatus


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

    from gui.job_runner import RESULTS_ROOT
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

    from gui.job_runner import RESULTS_ROOT
    persisted = RunResultRepository.load_result(job.id, root=RESULTS_ROOT)
    assert persisted["last_used_at"] == touched["last_used_at"]  # re-persisted to disk
