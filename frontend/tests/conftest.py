"""Gives every test its own isolated, empty femm_automation.db instead of
the real one under backend/data/.

Without this, a test that persists a job result (job_runner tests
especially) writes into the same database the live app's Run History reads
from - that's how test_submit_with_invalid_distance_type_fails_cleanly ended
up showing "not-a-number" as a real Run History entry in the running app.

There's no seed/bootstrap file anymore (a fresh database - real or test - is
just empty), but a lot of tests reference a config named "base_coil" by id
and actually solve it with FEMM, so this inserts one directly via
restore_config() - the exact same field values base_coil used to have as a
JSON file, just written in Python instead of read off disk. See
backend/conftest.py for the same fixture used the other side.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

_BASE_COIL_RECORD = {
    "id": "base_coil",
    "name": "base_coil",
    "builtin": True,
    "created_at": "2026-09-02T16:47:13.763472+00:00",
    "updated_at": "2026-09-02T16:47:13.763472+00:00",
    "cloned_from": None,
    "coilset": {
        "config_name": "base_iterative_coil \n",
        "tank_ferrite_present": 1,
        "tank_ferrite_y_offset": 0,
        "tank_ferrite_thickness": 1,
        "tank_mu": 184.5,
        "tank_ferrite_outer_r": 100,
        "rc_ferrite_present": 1,
        "rc_ferrite_y_offset": 0,
        "rc_ferrite_thickness": 1,
        "rc_mu": 184.5,
        "rc_ferrite_outer_r": 70,
        "backplane": 0,
        "backplane_y_distance": 100,
        "loop_offset": 10,
        "loop_r": 65,
        "num_loop_turns": 1,
        "num_parallel_loop_traces": 1,
        "num_loop_layers": 1,
        "loop_trace_spacing": 3.25,
        "loop_turn_spacing": 3.25,
        "tank_center_r": 72.688,
        "num_tank_turns": 5,
        "num_parallel_tank_traces": 1,
        "num_tank_layers": 1,
        "tank_trace_spacing": 3.25,
        "tank_turn_spacing": 3.25,
        "rc_radius": 56,
        "num_rc_turns": 3,
        "num_parallel_rc_traces": 1,
        "num_rc_layers": 1,
        "rc_trace_spacing": 3.25,
        "rc_turn_spacing": 3.25,
        "tank_trace_width": 3,
        "loop_trace_width": 3,
        "rc_trace_width": 3,
        "tank_PCB_thickness": 1.6,
        "loop_PCB_thickness": 1.6,
        "rc_PCB_thickness": 1.8,
        "rc_backplane": 0,
        "rc_backplane_y_distance": 100,
        "tc_shield": 1,
        "tc_shield_y_distance": 3,
        "tc_shield_radius": 105,
        "tc_shield_thickness": 1,
        "rc_shield": 1,
        "rc_shield_y_distance": 0,
        "rc_shield_radius": 60,
        "rc_shield_thickness": 1,
        "loop_trace_thickness": 0.07,
        "tank_trace_thickness": 0.07,
        "rc_trace_thickness": 0.07,
        "rc_loss_angle": 0.0125,
        "tc_loss_angle": 0.0125,
    },
}


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    import frontend.services.job_runner as job_runner
    import frontend.screen_manual.runs_panel as runs_panel
    from agent.services import run_log
    from engine.repositories import db
    from engine.repositories.coilset_repository import restore_config

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "femm_automation.db")
    monkeypatch.setattr(run_log, "LOG_DIR", tmp_path / "agent_logs")  # agent run logs, never data/agent_logs
    monkeypatch.setattr(job_runner, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runs_panel, "RESULTS_ROOT", tmp_path)

    restore_config(_BASE_COIL_RECORD)
