"""Toasts: the result of an import or export shows for a few seconds, then goes by itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.main import BackupPanelState
from frontend.screen_manual.configs_panel import ConfigsPanelState
from frontend.screen_manual.runs_panel import RunsPanelState
from frontend.services import toast


def test_a_toast_goes_after_its_time_and_an_error_stays_longer(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(toast.time, "monotonic", lambda: clock[0])
    toast.clear()
    toast.success("done")
    toast.error("broke")
    assert toast.active() == [("done", False), ("broke", True)]
    clock[0] += toast.SUCCESS_SECONDS + 0.1
    assert toast.active() == [("broke", True)]
    clock[0] += toast.ERROR_SECONDS
    assert toast.active() == []
    assert toast.SUCCESS_SECONDS >= 3 and toast.ERROR_SECONDS > toast.SUCCESS_SECONDS


def test_every_import_and_export_reports_through_a_toast(tmp_path):
    toast.clear()
    backup = BackupPanelState()
    path = tmp_path / "all.json"
    backup.export_all(path)
    assert toast.active()[-1] == (f"Backup saved to {path}", False)

    backup.import_all(tmp_path / "missing.json", ConfigsPanelState())
    text, is_error = toast.active()[-1]
    assert is_error and text.startswith("Restore failed: couldn't read")

    (tmp_path / "bad.json").write_text("{not json")
    backup.import_all(tmp_path / "bad.json", ConfigsPanelState())
    assert toast.active()[-1][1] is True and toast.active()[-1][0].startswith("Restore failed")

    backup.import_all(path, ConfigsPanelState())
    text, is_error = toast.active()[-1]
    assert not is_error and text.startswith("Restored ") and text.endswith(str(path))

    configs = ConfigsPanelState()
    configs.export(tmp_path / "none.json")  # nothing selected
    assert toast.active()[-1] == ("Export failed: select a config first.", True)

    runs = RunsPanelState()
    runs.export(tmp_path / "none.json")
    assert toast.active()[-1] == ("Export failed: select a run first.", True)
    runs.import_from(tmp_path / "missing.json")
    assert toast.active()[-1][0].startswith("Import failed: couldn't read") and toast.active()[-1][1]
    (tmp_path / "notrun.json").write_text('{"nope": 1}')
    runs.import_from(tmp_path / "notrun.json")
    assert toast.active()[-1][0].startswith("Import failed:") and toast.active()[-1][1]
