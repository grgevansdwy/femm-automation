"""Exercises ConfigsPanelState's methods directly - the same calls the
Config Library tab's buttons make, without needing to click a real window."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import desktop_app._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from desktop_app.field_layout import _ALL_FIELDS
from desktop_app.panels.configs_panel import ConfigsPanelState


def test_load_populates_every_field_from_a_real_builtin_config():
    state = ConfigsPanelState()
    state.load("base_coil")

    assert set(state.buffer) == set(_ALL_FIELDS)
    assert state.buffer["config_name"].strip() == "base_iterative_coil"
    assert state.buffer["loop_r"] == 65
    assert state.is_selected_builtin() is True


def test_new_starts_blank_and_is_not_builtin():
    state = ConfigsPanelState()
    state.new()

    assert state.selected_id is None
    assert state.buffer["config_name"] == "New Config"
    assert state.is_selected_builtin() is False


def test_builtin_cannot_be_deleted():
    state = ConfigsPanelState()
    state.load("base_coil")
    before = len(state.configs)

    state.delete()

    assert "Cannot delete built-in" in state.error
    assert len(state.configs) == before  # refresh() wasn't even reached


def test_clone_edit_save_delete_round_trip():
    state = ConfigsPanelState()
    state.load("base_coil")

    state.clone()
    assert state.error == ""
    cloned_id = state.selected_id
    assert cloned_id != "base_coil"
    # Clone renames the config *record*, not the CoilSet.config_name field
    # inside it - matches webapp's routers/configs.py::clone_config.
    cloned_record = next(c for c in state.configs if c["id"] == cloned_id)
    assert cloned_record["name"].endswith("(copy)")
    assert state.is_selected_builtin() is False

    state.buffer["loop_r"] = 70.0
    state.save()
    assert state.error == ""
    assert state.buffer["loop_r"] == 70.0

    from repositories.coilset_repository import get_coilset
    assert get_coilset(cloned_id).loop_r == 70.0

    state.delete()
    assert state.error == ""
    assert all(c["id"] != cloned_id for c in state.configs)


def test_save_rejects_invalid_geometry():
    state = ConfigsPanelState()
    state.load("base_coil")

    # Trace width far larger than the loop's own radius - check_valid_coil
    # rejects this as "crosses the y-axis".
    state.buffer["loop_trace_width"] = 1000.0
    state.save()

    assert state.error != ""
    assert "too large" in state.error  # the geometry reason string, not a crash
