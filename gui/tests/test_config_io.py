"""Pure export/import logic - no imgui import, no repository writes, so no
self-cleanup is needed for any of these (except the name_conflict/dedupe_name
tests, which read the real config store but never write to it)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

import pytest

from gui import config_io


def test_export_then_import_round_trips_the_same_fields_and_name():
    from repositories.coilset_repository import get_config

    record = get_config("base_coil")

    exported = config_io.export_record(record)
    coilset, name = config_io.parse_import(exported)

    assert name == record["name"]
    from models.coilset import CoilSet

    assert coilset == CoilSet(**record["coilset"])


def test_parse_import_rejects_invalid_json():
    with pytest.raises(ValueError, match="valid JSON"):
        config_io.parse_import("{not json")


def test_parse_import_rejects_a_record_missing_the_coilset_key():
    with pytest.raises(ValueError, match="coilset"):
        config_io.parse_import('{"name": "foo"}')


def test_parse_import_rejects_a_coilset_missing_a_field():
    from repositories.coilset_repository import get_config

    record = get_config("base_coil")
    del record["coilset"]["loop_r"]
    import json

    with pytest.raises(ValueError, match="missing=\\['loop_r'\\]"):
        config_io.parse_import(json.dumps(record))


def test_parse_import_rejects_a_coilset_with_an_extra_field():
    from repositories.coilset_repository import get_config

    record = get_config("base_coil")
    record["coilset"]["not_a_real_field"] = 1
    import json

    with pytest.raises(ValueError, match="extra=\\['not_a_real_field'\\]"):
        config_io.parse_import(json.dumps(record))


def test_parse_import_rejects_a_wrong_typed_value():
    """CoilSet is a plain dataclass and accepts a string where a number
    belongs; without a value-type check that sailed through construction
    and then crashed inside check_valid_coilset() with a raw TypeError."""
    import json

    from repositories.coilset_repository import get_config

    record = get_config("base_coil")
    record["coilset"]["loop_r"] = "sixty-five"

    with pytest.raises(ValueError, match="'loop_r' should be a number"):
        config_io.parse_import(json.dumps(record))


def test_dedupe_name_returns_the_bare_name_when_free():
    import random

    free_name = f"definitely_free_{random.randint(0, 10**9)}"
    assert config_io.dedupe_name(free_name) == free_name


def test_dedupe_name_picks_the_first_free_imported_suffix():
    # base_coil's own name is already taken - dedupe_name must skip it.
    from repositories.coilset_repository import get_config

    taken_name = get_config("base_coil")["name"]
    deduped = config_io.dedupe_name(taken_name)

    assert deduped != taken_name
    assert deduped.startswith(taken_name)
    assert not config_io.name_conflict(deduped)
