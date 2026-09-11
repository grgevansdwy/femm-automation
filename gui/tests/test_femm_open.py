"""Exercises femm_open's launch state machine and the geometry each button
hands it - everything except the imgui button widget itself, which needs a
live frame (the same split every other panel test here uses).

Nothing in this file starts Wine or FEMM: launch() is pointed at a stub, so
these run on a machine with no Wine install.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

import pytest

from gui import femm_open
from gui.job_runner import Job, JobRunner
from gui.panels.configs_panel import ConfigsPanelState
from gui.panels.runs_panel import run_geometry


@pytest.fixture(autouse=True)
def reset_launch_state():
    """femm_open's launch state is module-level (one FEMM launch at a time,
    shared by buttons in five panels) - reset it between tests."""
    yield
    femm_open._owner_id = None
    femm_open._future = None
    femm_open._status = ""
    femm_open._error = ""


def _drain(timeout=10.0):
    """Runs poll() until the in-flight launch has been picked up - the frame
    loop's job in the real app, a bounded wait here."""
    deadline = time.time() + timeout
    while femm_open.busy():
        if time.time() > deadline:
            raise TimeoutError("launch never finished")
        femm_open.poll()
        time.sleep(0.005)


def _baseline_buffer():
    state = ConfigsPanelState()
    state.load("base_coil")
    return state.buffer


def test_a_panel_buffer_rebuilds_into_the_coilset_the_solver_takes():
    coilset = femm_open.coilset_from(_baseline_buffer())

    assert coilset.config_name == "base_iterative_coil \n"
    assert coilset.loop_r == 65


def test_geometry_that_no_longer_fits_todays_coilset_is_rejected_not_crashed():
    """A historical AI run can carry a field that has since been renamed or
    removed - button() turns this into a message instead of a traceback."""
    stale = {**_baseline_buffer(), "a_field_that_no_longer_exists": 1}

    with pytest.raises(TypeError):
        femm_open.coilset_from(stale)


def test_a_launch_is_busy_until_polled_and_then_reports_where_the_model_went(tmp_path):
    fem_path = tmp_path / "Gen3_5mm" / "model.fem"
    femm_open._owner_id, femm_open._status, femm_open._error = "btn", "", ""
    femm_open._future = femm_open._EXECUTOR.submit(lambda: fem_path)

    assert femm_open.busy()
    _drain()

    assert not femm_open.busy()
    assert femm_open._status.endswith("Gen3_5mm/model.fem")
    assert femm_open._error == ""


def test_a_failed_launch_surfaces_its_reason_instead_of_raising():
    def boom():
        raise RuntimeError("Wine isn't installed")

    femm_open._owner_id = "btn"
    femm_open._future = femm_open._EXECUTOR.submit(boom)
    _drain()

    assert femm_open._error == "Wine isn't installed"
    assert femm_open._status == ""


def test_a_runs_own_geometry_is_exact_while_its_job_is_still_in_memory():
    """A job this session submitted still holds the CoilSet object that was
    solved, so its geometry needs no rebuilding and no hash check."""
    from repositories.coilset_repository import get_coilset

    configs_panel = ConfigsPanelState()
    configs_panel.load("base_coil")
    runner = JobRunner()
    # Built directly rather than through submit() - this is about what
    # run_geometry() reads off a resident job, not about running FEMM.
    runner.jobs["live"] = Job(id="live", config_id="base_coil", config_name="base_coil",
                              distance=7.25, coilset=get_coilset("base_coil"))
    row = runner.jobs["live"].as_dict()

    geometry, exact = run_geometry(row, runner)

    assert exact
    assert geometry == configs_panel.buffer


def test_a_config_edited_since_the_run_is_flagged_rather_than_opened_silently():
    """The runs table stores a hash of the geometry, not the geometry - so a
    disk row can only be rebuilt from the config as it stands today. When
    that no longer hashes to what was solved, the caller has to say so."""
    configs_panel = ConfigsPanelState()
    configs_panel.load("base_coil")
    row = {
        "run_id": "not-in-memory", "config_id": "base_coil", "config_name": "base_coil",
        "distance": 5.0, "coilset_hash": "a hash from some older geometry",
    }

    geometry, exact = run_geometry(row, JobRunner())

    assert geometry == configs_panel.buffer  # still worth opening...
    assert not exact                          # ...but not what produced the numbers


def test_a_deleted_config_leaves_nothing_to_open():
    row = {"run_id": "orphan", "config_id": "deleted-long-ago", "config_name": "gone",
           "distance": 5.0, "coilset_hash": "x"}

    geometry, exact = run_geometry(row, JobRunner())

    assert geometry is None
    assert not exact
