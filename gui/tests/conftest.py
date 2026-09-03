"""Keeps every test out of coil_simulation/data/results/.

Without this, a test that persists a job result (job_runner tests
especially) writes into the same folder the live app's Run History reads
from - that's how test_submit_with_invalid_distance_type_fails_cleanly ended
up showing "not-a-number" as a real Run History entry in the running app.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def isolate_results_dir(tmp_path, monkeypatch):
    import gui.job_runner as job_runner
    import gui.panels.runs_panel as runs_panel

    monkeypatch.setattr(job_runner, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(runs_panel, "RESULTS_ROOT", tmp_path)
