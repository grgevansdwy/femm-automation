"""Exercises ConfigsPanelState's methods directly - the same calls the
Config Library tab's buttons make, without needing to click a real window."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from gui.field_layout import _ALL_FIELDS
from gui.panels.configs_panel import ConfigsPanelState


def test_load_populates_every_field_from_a_real_builtin_config():
    state = ConfigsPanelState()
    state.load("base_coil")

    assert set(state.buffer) == set(_ALL_FIELDS)
    # Not asserting an exact config_name: it's plain user data and this repo's
    # base_coil has already been renamed once via the app's own Save button.
    assert state.buffer["config_name"].strip() != ""
    assert state.buffer["loop_r"] == 65


def test_new_starts_blank():
    state = ConfigsPanelState()
    state.new()

    assert state.selected_id is None
    assert state.buffer["config_name"] == "New Config"


def test_builtin_configs_can_be_deleted():
    """Unlike coilset_repository.delete_config() (which refuses built-ins,
    right for the webapp's shared reference-design library), this app lets
    the user delete anything - including a built-in - per their own request.
    Deletes a cloned+flagged-builtin copy, never a real reference design."""
    import json

    from repositories.coilset_repository import CONFIGS_DIR

    state = ConfigsPanelState()
    state.load("base_coil")
    state.clone()
    cloned_id = state.selected_id
    path = CONFIGS_DIR / f"{cloned_id}.json"
    record = json.loads(path.read_text())
    record["builtin"] = True
    path.write_text(json.dumps(record, indent=2))
    state.refresh()

    state.delete()

    assert state.error == ""
    assert not path.exists()
    assert all(c["id"] != cloned_id for c in state.configs)


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

    state.buffer["loop_r"] = 70.0
    state.save()
    assert state.error == ""
    assert state.buffer["loop_r"] == 70.0

    from repositories.coilset_repository import get_coilset
    assert get_coilset(cloned_id).loop_r == 70.0

    state.delete()
    assert state.error == ""
    assert all(c["id"] != cloned_id for c in state.configs)


def test_save_rejects_a_duplicate_name():
    state = ConfigsPanelState()
    state.load("base_coil")
    state.clone()  # a real, uniquely-named config to collide with
    clone_id = state.selected_id
    taken_name = next(c for c in state.configs if c["id"] == clone_id)["name"]

    state.new()
    state.buffer["config_name"] = taken_name
    state.save()

    assert state.error != ""
    assert taken_name in state.error

    from repositories.coilset_repository import list_configs
    assert sum(1 for c in list_configs() if c["name"] == taken_name) == 1  # no duplicate written

    state.load(clone_id)
    state.delete()


def test_save_allows_keeping_the_same_name_when_editing():
    """The duplicate-name guard must exclude the config being saved from
    the collision check against itself, or every ordinary re-save would
    falsely report its own name as taken."""
    state = ConfigsPanelState()
    state.load("base_coil")
    state.clone()

    state.buffer["loop_r"] = 42.0
    state.save()  # same config_name as before - must not self-conflict

    assert state.error == ""
    assert state.buffer["loop_r"] == 42.0

    state.delete()


def test_clone_rejects_a_duplicate_copy_name():
    state = ConfigsPanelState()
    state.load("base_coil")
    state.clone()
    assert state.error == ""
    first_clone_id = state.selected_id
    first_clone_name = next(c for c in state.configs if c["id"] == first_clone_id)["name"]

    state.load("base_coil")
    state.clone()  # same source -> same auto "<name> (copy)" -> conflict

    assert state.error != ""
    assert state.selected_id == "base_coil"  # clone() returned early, selection untouched

    from repositories.coilset_repository import list_configs
    assert sum(1 for c in list_configs() if c["name"] == first_clone_name) == 1  # only the first clone exists

    state.load(first_clone_id)
    state.delete()


def test_save_rejects_invalid_geometry():
    state = ConfigsPanelState()
    state.load("base_coil")

    # Trace width far larger than the loop's own radius - check_valid_coil
    # rejects this as "crosses the y-axis".
    state.buffer["loop_trace_width"] = 1000.0
    state.save()

    assert state.error != ""
    assert "too large" in state.error  # the geometry reason string, not a crash
