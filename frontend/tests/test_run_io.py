"""Pure run export/import logic - no imgui import. resolve_import_id() does
check the repository for an existing run_id, so it (and by extension this
whole file) relies on conftest.py's autouse isolate_db fixture, same as any
other test that touches the repository layer.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

import pytest

from frontend.files_backup import run_io


def _make_row(run_id="abc123def456"):
    return {
        "run_id": run_id, "config_id": "base_coil", "config_name": "base_coil",
        "distance": 5.0, "status": "done",
        "created_at": "2026-09-01T00:00:00+00:00", "started_at": "2026-09-01T00:00:01+00:00",
        "finished_at": "2026-09-01T00:00:05+00:00", "last_used_at": None,
        "error": None, "result": {"L_loop": 1e-6}, "coilset_hash": None,
    }


def test_export_then_import_round_trips_the_row():
    row = _make_row()
    exported = run_io.export_record(row)
    assert run_io.parse_import(exported) == row


def test_parse_import_rejects_invalid_json():
    with pytest.raises(ValueError, match="valid JSON"):
        run_io.parse_import("{not json")


def test_parse_import_rejects_a_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        run_io.parse_import("[1, 2, 3]")


def test_parse_import_rejects_a_row_missing_a_required_field():
    row = _make_row()
    del row["status"]
    with pytest.raises(ValueError, match="status"):
        run_io.parse_import(json.dumps(row))


def test_parse_import_rejects_an_empty_run_id():
    row = _make_row(run_id="")
    with pytest.raises(ValueError, match="run_id"):
        run_io.parse_import(json.dumps(row))


def test_resolve_import_id_keeps_the_id_when_free():
    row = _make_row(run_id="totally-unused-run-id")
    resolved = run_io.resolve_import_id(row)
    assert resolved["run_id"] == "totally-unused-run-id"


def test_resolve_import_id_generates_a_new_id_on_collision():
    from engine.repositories.run_result_repository import RunResultRepository

    row = _make_row(run_id="colliding-run-id")
    RunResultRepository(run_id=row["run_id"]).save_result_json(row)

    resolved = run_io.resolve_import_id(row)
    assert resolved["run_id"] != "colliding-run-id"
