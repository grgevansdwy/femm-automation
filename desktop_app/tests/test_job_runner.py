import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import desktop_app._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from repositories.coilset_repository import get_coilset
from repositories.run_result_repository import RunResultRepository

from desktop_app.job_runner import JobRunner, JobStatus


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

    from desktop_app.job_runner import RESULTS_ROOT
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
