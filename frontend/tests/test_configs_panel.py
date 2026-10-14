"""Exercises ConfigsPanelState's methods directly - the same calls the
Config Library tab's buttons make, without needing to click a real window."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.services import toast
from frontend.services.field_layout import _ALL_FIELDS
from frontend.screen_manual.configs_panel import ConfigsPanelState


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
    """Unlike coilset_repository.delete_config()'s default (which refuses
    built-ins, right for the webapp's shared reference-design library),
    this app lets the user delete anything - including a built-in - per
    their own request. Deletes a cloned+flagged-builtin copy, never a real
    reference design."""
    from engine.repositories.coilset_repository import get_config, restore_config

    state = ConfigsPanelState()
    state.load("base_coil")
    state.clone()
    cloned_id = state.selected_id

    record = get_config(cloned_id)
    record["builtin"] = True
    restore_config(record)
    state.refresh()

    state.delete()

    assert state.error == ""
    assert get_config(cloned_id) is None
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

    from engine.repositories.coilset_repository import get_coilset
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

    from engine.repositories.coilset_repository import list_configs
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
    from engine.repositories.coilset_repository import list_configs

    state = ConfigsPanelState()
    state.load("base_coil")
    original_name = state.buffer["config_name"]
    export_path = tmp_path / "base_coil_export.json"

    toast.clear()
    state.export(export_path)
    assert toast.active() == [(f"Exported successfully to {export_path}", False)]

    state.import_from(export_path)

    assert state.error == ""
    # base_coil's record name is already taken, so dedupe_name must have
    # kicked in - the imported CoilSet content is still identical otherwise.
    text, is_error = toast.active()[-1]
    assert text.startswith("Imported") and "successfully" in text and not is_error
    # config_name must follow the *deduped* name, matching the left list -
    # not the original pre-dedupe name baked into the imported file, which
    # would leave the list and the form disagreeing (a real reported bug).
    imported_id = state.selected_id
    imported_record = next(c for c in state.configs if c["id"] == imported_id)
    assert imported_record["name"] != original_name  # dedupe actually kicked in
    assert state.buffer["config_name"] == imported_record["name"]
    assert imported_id != "base_coil"
    assert any(c["id"] == imported_id for c in list_configs())

    state.delete()
    assert all(c["id"] != imported_id for c in list_configs())


def test_import_forces_config_name_to_match_the_deduped_list_name(tmp_path):
    """A real reported bug: an imported file whose 'coilset.config_name'
    already differs from its top-level 'name' (e.g. because it was itself
    produced by a version of this same bug, or just hand-edited) used to
    carry that stale config_name straight through - the left list would show
    the deduped name while the form's config_name field showed something
    else entirely."""
    import json

    from engine.repositories.coilset_repository import list_configs

    state = ConfigsPanelState()
    state.load("base_coil")
    export_path = tmp_path / "mismatched_export.json"
    state.export(export_path)

    record = json.loads(export_path.read_text())
    record["coilset"]["config_name"] = "this does not match the record name"
    export_path.write_text(json.dumps(record))

    state.import_from(export_path)

    assert state.error == ""
    imported_id = state.selected_id
    imported_record = next(c for c in list_configs() if c["id"] == imported_id)
    assert state.buffer["config_name"] == imported_record["name"]
    assert state.buffer["config_name"] != "this does not match the record name"

    state.delete()


def test_import_rejects_invalid_geometry_and_writes_nothing(tmp_path):
    import json

    from engine.repositories.coilset_repository import get_config, list_configs

    record = get_config("base_coil")
    record["coilset"]["loop_trace_width"] = 1000.0  # same invalid geometry as the Save test above
    bad_path = tmp_path / "bad_config.json"
    bad_path.write_text(json.dumps(record))

    before = {c["id"] for c in list_configs()}
    state = ConfigsPanelState()

    toast.clear()
    state.import_from(bad_path)

    (text, is_error), = toast.active()
    assert is_error and text.startswith("Import failed") and "too large" in text
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
