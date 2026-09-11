"""Bulk export-all/import-all round trip. Relies on conftest.py's autouse
isolate_db fixture, so this never touches the real database - the db it
sees already has the real builtin configs seeded into it.
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

import pytest

from gui import backup_io


def test_export_all_then_import_all_reproduces_everything_exactly():
    from models.coilset import CoilSet
    from repositories.coilset_repository import list_configs, save_config
    from repositories.run_result_repository import RunResultRepository

    # Add one extra user config and one run on top of the seeded builtins,
    # so the backup covers more than just builtins.
    template = CoilSet(**list_configs()[0]["coilset"])
    save_config(dataclasses.replace(template, config_name="backup test config"), name="backup test config")
    RunResultRepository(run_id="backup-test-run").save_result_json({
        "run_id": "backup-test-run", "config_id": "base_coil", "config_name": "base_coil",
        "distance": 5.0, "status": "done",
        "created_at": "2026-09-01T00:00:00+00:00", "started_at": None,
        "finished_at": "2026-09-01T00:00:05+00:00", "last_used_at": None,
        "error": None, "result": {"L_loop": 1e-6}, "coilset_hash": None,
    })

    before_configs = {c["id"]: c for c in list_configs()}
    before_runs = {r["run_id"]: r for r in RunResultRepository.list_results()}

    dump = backup_io.export_all()
    n_configs, n_runs = backup_io.import_all(dump)

    # Restore is an upsert onto the same data it was just exported from - a
    # no-op in content terms, which is exactly what "exact reproduction"
    # (not save_config()'s edit semantics) means to prove here.
    assert n_configs == len(before_configs)
    assert n_runs == len(before_runs)
    assert {c["id"]: c for c in list_configs()} == before_configs
    assert {r["run_id"]: r for r in RunResultRepository.list_results()} == before_runs


def test_import_all_rejects_a_malformed_backup():
    with pytest.raises(ValueError, match="configs"):
        backup_io.import_all('{"not": "a backup"}')


def test_import_all_rejects_invalid_json():
    with pytest.raises(ValueError, match="valid JSON"):
        backup_io.import_all("{not json")
