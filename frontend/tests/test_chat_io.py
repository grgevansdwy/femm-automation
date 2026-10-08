"""Pure chat export/import file logic - no imgui import. Reads and writes the
ai_runs tables through agent/services/chat_export.py, so it relies on
conftest.py's autouse isolate_db fixture like any other repository test.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

import pytest

from agent.repositories import ai_run_repository as ar
from agent.services import chat_export
from frontend.files_backup import chat_io


@pytest.fixture
def out(tmp_path):
    """An empty folder to export into (tmp_path itself holds the test database)."""
    folder = tmp_path / "exports"
    folder.mkdir()
    return folder


def _chat() -> str:
    run_id = ar.create_ai_run("base_coil")["id"]
    ar.append_message(run_id, "interview", 1, "user", content="cut tank ESR",
                      payload={"role": "user", "content": "cut tank ESR"})
    return run_id


def test_default_name_uses_the_baseline_and_the_id():
    assert chat_io.default_name({"id": "abc123", "baseline_config_id": "Gen2 coil/v3"}, "json") == "chat_Gen2_coil_v3_abc123.json"
    assert chat_io.default_name({"id": "abc123", "baseline_config_id": None}, "pdf") == "chat_abc123.pdf"


def test_export_json_then_import_gives_a_new_chat(out):
    run_id = _chat()
    path = out / "chat.json"
    chat_io.export_json(run_id, path)
    new_id = chat_io.import_json(path)
    assert new_id != run_id and ar.get_ai_run(new_id)["imported_at"] is not None
    assert [m["content"] for m in ar.list_messages(new_id)] == ["cut tank ESR"]
    assert [p.name for p in out.iterdir()] == ["chat.json"], "no temporary file is left behind"
    import os
    umask = os.umask(0)
    os.umask(umask)
    assert path.stat().st_mode & 0o777 == 0o666 & ~umask, "an export is readable like any saved file, not owner-only"


def test_export_pdf_writes_a_pdf(out):
    path = out / "chat.pdf"
    chat_io.export_pdf(_chat(), path)
    assert path.read_bytes().startswith(b"%PDF")
    assert [p.name for p in out.iterdir()] == ["chat.pdf"]


def test_a_failed_export_leaves_no_file_and_keeps_an_existing_one(out, monkeypatch):
    path = out / "chat.pdf"
    path.write_text("an earlier export")

    def fail(data, temp, **kwargs):
        Path(temp).write_text("half a file")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(chat_export, "write_pdf", fail)
    with pytest.raises(chat_io.ChatIOError, match="No space left"):
        chat_io.export_pdf(_chat(), path)
    assert path.read_text() == "an earlier export"
    assert [p.name for p in out.iterdir()] == ["chat.pdf"]


def test_export_of_a_missing_chat_is_one_sentence(out):
    with pytest.raises(chat_io.ChatIOError, match="no longer exists"):
        chat_io.export_json("nope", out / "chat.json")
    assert list(out.iterdir()) == []


def test_an_oversized_file_is_refused_before_it_is_read(tmp_path, monkeypatch):
    path = tmp_path / "big.json"
    path.write_text("x" * 500)
    monkeypatch.setattr(chat_export, "MAX_FILE_CHARS", 100)
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("the file was read"))
    with pytest.raises(chat_io.ChatIOError, match="larger than"):
        chat_io.import_json(path)


def test_import_errors_name_the_check_and_write_nothing(tmp_path):
    before = len(ar.list_ai_runs())
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(chat_io.ChatIOError, match="json check"):
        chat_io.import_json(path)
    path.write_bytes(b"\xff\xfe\x00binary")
    with pytest.raises(chat_io.ChatIOError, match="not text"):
        chat_io.import_json(path)
    with pytest.raises(chat_io.ChatIOError, match="Could not read"):
        chat_io.import_json(tmp_path / "missing.json")
    assert len(ar.list_ai_runs()) == before
