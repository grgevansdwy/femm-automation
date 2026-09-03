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
    # Clone renames both the config record *and* the CoilSet.config_name
    # field inside it, so the list and the form agree.
    cloned_record = next(c for c in state.configs if c["id"] == cloned_id)
    assert cloned_record["name"].endswith("(copy)")
    assert state.buffer["config_name"] == cloned_record["name"]

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


def test_cloning_the_same_config_twice_stacks_another_copy_suffix():
    """Cloning never errors on a name collision - it appends another
    " (copy)" until the name is free, so the same config can be cloned
    repeatedly."""
    state = ConfigsPanelState()
    state.load("base_coil")

    state.clone()
    assert state.error == ""
    first_id = state.selected_id
    first_name = next(c for c in state.configs if c["id"] == first_id)["name"]
    assert first_name.endswith("(copy)")

    state.load("base_coil")
    state.clone()  # same source, same would-be name -> stacks another suffix

    assert state.error == ""
    second_id = state.selected_id
    assert second_id != first_id
    second_name = next(c for c in state.configs if c["id"] == second_id)["name"]
    assert second_name == f"{first_name} (copy)"
    assert state.buffer["config_name"] == second_name

    state.load(second_id)
    state.delete()
    state.load(first_id)
    state.delete()


def test_export_then_import_round_trips_via_tmp_path(tmp_path):
    from repositories.coilset_repository import list_configs

    state = ConfigsPanelState()
    state.load("base_coil")
    original_name = state.buffer["config_name"]
    export_path = tmp_path / "base_coil_export.json"

    state.export(export_path)
    assert state.error == ""

    state.import_from(export_path)

    assert state.error == ""
    # base_coil's record name is already taken, so dedupe_name must have
    # kicked in - the imported CoilSet content is still identical though.
    assert state.status.startswith("Imported")
    assert state.buffer["config_name"] == original_name
    imported_id = state.selected_id
    assert imported_id != "base_coil"
    assert any(c["id"] == imported_id for c in list_configs())

    state.delete()
    assert all(c["id"] != imported_id for c in list_configs())


def test_import_rejects_invalid_geometry_and_writes_nothing(tmp_path):
    import json

    from repositories.coilset_repository import get_config, list_configs

    record = get_config("base_coil")
    record["coilset"]["loop_trace_width"] = 1000.0  # same invalid geometry as the Save test above
    bad_path = tmp_path / "bad_config.json"
    bad_path.write_text(json.dumps(record))

    before = {c["id"] for c in list_configs()}
    state = ConfigsPanelState()

    state.import_from(bad_path)

    assert state.error != ""
    assert "too large" in state.error
    after = {c["id"] for c in list_configs()}
    assert after == before  # nothing written


def test_save_rejects_invalid_geometry():
    state = ConfigsPanelState()
    state.load("base_coil")

    # Trace width far larger than the loop's own radius - check_valid_coil
    # rejects this as "crosses the y-axis".
    state.buffer["loop_trace_width"] = 1000.0
    state.save()

    assert state.error != ""
    assert "too large" in state.error  # the geometry reason string, not a crash
