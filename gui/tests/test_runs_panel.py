"""Exercises RunsPanelState directly - the same calls the New Run / Run
History tabs make, without needing to click a real window."""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from gui.job_runner import JobStatus
from gui.panels.runs_panel import RunsPanelState


def _wait_done(state, run_id, timeout=60):
    deadline = time.time() + timeout
    while state.job_runner.jobs[run_id].status in (JobStatus.QUEUED, JobStatus.RUNNING):
        if time.time() > deadline:
            raise TimeoutError(f"run {run_id} still running after {timeout}s")
        time.sleep(0.05)


def test_submit_run_appears_in_history_and_completes():
    state = RunsPanelState()
    state.selected_config_id = "base_coil"
    # A distance unlikely to already have a cached result from a prior test
    # run - this test is exercising the live solve path, not the cache.
    state.distance = round(random.uniform(1.0, 50.0), 6)

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
    state.distance = round(random.uniform(1.0, 50.0), 6)

    state.submit_run()
    first_id = state.selected_run_id
    # Same distance again, deliberately: submit_run() only treats a *done*
    # persisted result as a cache hit, and femmcli takes several seconds, so
    # the first job can't have persisted yet - this still proves two
    # concurrent solves of identical params, not just two cheap calls.
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


def test_resubmitting_the_same_config_and_distance_reuses_the_result():
    state = RunsPanelState()
    state.selected_config_id = "base_coil"
    state.distance = round(random.uniform(1.0, 50.0), 6)

    state.submit_run()
    first_run_id = state.selected_run_id
    _wait_done(state, first_run_id)

    state.submit_run()  # same config_id + same distance, now that the first is done

    assert state.new_run_error == ""
    assert state.new_run_status != ""
    assert state.selected_run_id == first_run_id
    assert len(state.job_runner.jobs) == 1  # no second Job was created


def test_reusing_a_cached_result_bumps_it_back_to_the_top_of_history():
    state = RunsPanelState()
    state.selected_config_id = "base_coil"
    first_distance = round(random.uniform(1.0, 25.0), 6)
    second_distance = round(random.uniform(25.0, 50.0), 6)

    state.distance = first_distance
    state.submit_run()
    first_run_id = state.selected_run_id
    _wait_done(state, first_run_id)
    last_used_before = next(r for r in state.history_rows() if r["run_id"] == first_run_id)["last_used_at"]

    # A second, distinct run solved after the first - it should now sort above it.
    state.distance = second_distance
    state.submit_run()
    second_run_id = state.selected_run_id
    _wait_done(state, second_run_id)
    assert state.history_rows()[0]["run_id"] == second_run_id

    # Resubmit the first (config, distance) again - a cache hit, not a re-solve.
    state.distance = first_distance
    state.submit_run()

    assert state.selected_run_id == first_run_id
    assert len(state.job_runner.jobs) == 2  # no third Job was created

    rows_after = state.history_rows()
    assert rows_after[0]["run_id"] == first_run_id  # reused run is back on top
    last_used_after = next(r for r in rows_after if r["run_id"] == first_run_id)["last_used_at"]
    assert last_used_after > last_used_before


def test_refresh_configs_picks_up_a_config_created_after_init():
    """refresh_configs() used to only run once, at __init__ - so the New Run
    dropdown never noticed a config created on the Config Library tab later
    in the same session."""
    from repositories.coilset_repository import CONFIGS_DIR, get_coilset, save_config

    state = RunsPanelState()
    before_ids = {c["id"] for c in state.configs}

    coilset = get_coilset("base_coil")
    probe_name = f"probe_{random.randint(0, 10**9)}"
    record = save_config(coilset, name=probe_name)

    try:
        assert record["id"] not in before_ids
        assert record["id"] not in {c["id"] for c in state.configs}  # stale until refreshed

        state.refresh_configs()

        assert record["id"] in {c["id"] for c in state.configs}
    finally:
        (CONFIGS_DIR / f"{record['id']}.json").unlink(missing_ok=True)


def test_submit_without_selected_config_sets_error():
    state = RunsPanelState()
    state.selected_config_id = None

    state.submit_run()

    assert state.new_run_error != ""
