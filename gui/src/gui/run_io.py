"""Run export/import: pure logic, no imgui import, so it's fully pytest-able
without a live frame. UI glue (file dialogs, buttons) lives in
panels/runs_panel.py; this module only turns a run record into JSON text and
back, plus the run_id-collision handling Import uses.

Looser validation than config_io.py's CoilSet round-trip: a run record has
no user-editable schema to drift against (it's this repository's own output
shape, not a dataclass someone hand-edits), so this only checks presence of
the fields every consumer (_draw_run_detail, history_rows) actually reads -
not an exact field-set match the way config_io.parse_import checks CoilSet.
"""

import json
import uuid

_REQUIRED_FIELDS = ("run_id", "config_id", "config_name", "distance", "status", "created_at")


def export_record(row: dict) -> str:
    """Same shape RunResultRepository returns - export is a literal copy of
    the row, not a re-derived subset."""
    return json.dumps(row, indent=2)


def parse_import(text: str) -> dict:
    """Returns a run row dict from previously-exported JSON text, or raises
    ValueError with a specific, human-readable reason."""
    try:
        row = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(row, dict):
        raise ValueError("Expected a JSON object.")

    missing = [f for f in _REQUIRED_FIELDS if f not in row]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    if not isinstance(row.get("run_id"), str) or not row["run_id"]:
        raise ValueError("run_id must be a non-empty string.")

    return row


def resolve_import_id(row: dict) -> dict:
    """If row['run_id'] isn't already stored, keep it as-is - this is what
    lets restoring an archived/deleted run bring back its original
    id/timestamps intact. If it already exists, generate a fresh id instead,
    so importing a run exported from another machine never silently
    overwrites an unrelated local run that happens to share an id."""
    from repositories.run_result_repository import RunResultRepository

    row = dict(row)
    if RunResultRepository.load_result(row["run_id"]) is not None:
        row["run_id"] = uuid.uuid4().hex[:12]
    return row
