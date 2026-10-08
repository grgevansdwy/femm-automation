"""Chat export/import: the file side of agent/services/chat_export.py - pure
logic, no imgui import, so it's fully pytest-able without a live frame. UI
glue (file dialogs, buttons) lives in screen_agent/; this module names the
file, writes it so a failed export never leaves half a file behind, refuses
an oversized import before reading it, and turns every failure into one
sentence a person can act on (ChatIOError).
"""

import os
import re
import tempfile
from pathlib import Path


class ChatIOError(Exception):
    """An export or import that didn't happen; str() is the sentence to show."""


def default_name(run: dict, ext: str) -> str:
    """chat_<baseline>_<id>.<ext>, with only characters every file system takes."""
    baseline = re.sub(r"[^A-Za-z0-9_-]+", "_", run.get("baseline_config_id") or "").strip("_")
    return "_".join(part for part in ("chat", baseline, run.get("id") or "") if part) + f".{ext}"


def _write_atomically(path, write) -> None:
    """`write(temp_path)` produces the file next to `path`; it is moved into
    place only when complete."""
    path = Path(path)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    os.close(handle)
    try:
        write(temp)
        # mkstemp() makes the file private to its owner; an export is meant to
        # be handed on, so it gets the permissions any saved file would.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(temp, 0o666 & ~umask)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def export_json(ai_run_id: str, path) -> None:
    from agent.services import chat_export
    try:
        text = chat_export.dumps_export(chat_export.build_export(ai_run_id))
        _write_atomically(path, lambda temp: Path(temp).write_text(text, encoding="utf-8"))
    except chat_export.ChatFileError as exc:
        raise ChatIOError(f"This chat can't be exported: {exc.detail}.") from exc
    except ValueError as exc:
        raise ChatIOError(f"Not exported: {exc}.") from exc
    except OSError as exc:
        raise ChatIOError(f"Could not write {path}: {exc.strerror or exc}.") from exc


def export_pdf(ai_run_id: str, path) -> None:
    from agent.repositories.ai_run_repository import get_ai_run
    from agent.services import chat_export
    try:
        data = chat_export.build_export(ai_run_id)
        imported_at = (get_ai_run(ai_run_id) or {}).get("imported_at")
        _write_atomically(path, lambda temp: chat_export.write_pdf(data, temp, chat_id=ai_run_id, imported_at=imported_at))
    except ValueError as exc:
        raise ChatIOError(f"Not exported: {exc}.") from exc
    except OSError as exc:
        raise ChatIOError(f"Could not write {path}: {exc.strerror or exc}.") from exc


def import_json(path) -> str:
    """Imports the chat file at `path` as a new chat and returns its id."""
    from agent.services import chat_export
    try:
        if os.stat(path).st_size > chat_export.MAX_FILE_CHARS:
            raise ChatIOError(f"Not imported: the file is larger than "
                              f"{chat_export.MAX_FILE_CHARS // (1024 * 1024)} MB.")
        text = Path(path).read_text(encoding="utf-8")
        return chat_export.import_chat(text)
    except chat_export.ChatFileError as exc:
        raise ChatIOError(f"Not imported ({exc.check} check): {exc.detail}.") from exc
    except UnicodeDecodeError as exc:
        raise ChatIOError("Not imported (json check): the file is not text.") from exc
    except OSError as exc:
        raise ChatIOError(f"Could not read {path}: {exc.strerror or exc}.") from exc
