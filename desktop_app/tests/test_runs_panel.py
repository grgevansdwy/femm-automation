"""Exercises RunsPanelState directly - the same calls the New Run / Run
History tabs make, without needing to click a real window."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import desktop_app._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from desktop_app.job_runner import JobStatus
from desktop_app.panels.runs_panel import RunsPanelState


def _wait_done(state, run_id, timeout=60):
    deadline = time.time() + timeout
    while state.job_runner.jobs[run_id].status in (JobStatus.QUEUED, JobStatus.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(f"run {run_id} still running after {timeout}s")
        time.sleep(0.05)


def test_submit_run_appears_in_history_and_completes():
    state = RunsPanelState()
    state.selected_config_id = "base_coil"
    state.distance = 5.0

    state.submit_run()
    assert state.new_run_error == ""
    run_id = state.selected_run_id
    assert run_id is not None

    rows_while_running = state.history_rows()
    assert any(r["run_id"] == run_id for r in rows_while_running)

    _wait_done(state, run_id)

    rows_after = state.history_rows()
    row = next(r for r in rows_after if r["run_id"] == run_id)
    assert row["status"] == "done"
    assert row["result"]["L_loop"] > 0


def test_two_runs_can_be_in_flight_at_once():
    state = RunsPanelState()
    state.selected_config_id = "base_coil"
    state.distance = 5.0

    state.submit_run()
    first_id = state.selected_run_id
    state.submit_run()
    second_id = state.selected_run_id

    assert first_id != second_id
    # Both jobs exist in the runner immediately after submission - proves
    # submit() doesn't block waiting for the first job to finish.
    assert first_id in state.job_runner.jobs
    assert second_id in state.job_runner.jobs

    _wait_done(state, first_id)
    _wait_done(state, second_id)
    assert state.job_runner.jobs[first_id].status == JobStatus.DONE
    assert state.job_runner.jobs[second_id].status == JobStatus.DONE


def test_submit_without_selected_config_sets_error():
    state = RunsPanelState()
    state.selected_config_id = None

    state.submit_run()

    assert state.new_run_error != ""
