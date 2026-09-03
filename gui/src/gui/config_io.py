"""Config export/import: pure logic, no imgui import, so it's fully
pytest-able without a live frame. UI glue (file dialogs, buttons) lives in
panels/configs_panel.py; this module only turns a config record into JSON
text and back, plus the name-collision logic Save/Clone/Import all share.
"""

import json

from . import field_layout


def name_conflict(name: str, exclude_id: str | None = None) -> bool:
    """True if some other config already has this name (case-insensitive,
    matching how list_configs() sorts) - re-checks against disk, not a
    possibly-stale in-memory list, since coil_simulation/data/configs/ can
    be edited by other sessions too."""
    from repositories.coilset_repository import list_configs

    target = name.strip().lower()
    return any(c["name"].strip().lower() == target and c["id"] != exclude_id for c in list_configs())


def dedupe_name(base_name: str) -> str:
    """The first available name starting from base_name: the bare name if
    free, otherwise "<base_name> (imported)", "(imported 2)", ... - no
    modal prompt, auto-suffixing is the chosen v1 behavior for imports."""
    if not name_conflict(base_name):
        return base_name
    suffix = 1
    while True:
        candidate = f"{base_name} (imported)" if suffix == 1 else f"{base_name} (imported {suffix})"
        if not name_conflict(candidate):
            return candidate
        suffix += 1


def export_record(record: dict) -> str:
    """Same shape coilset_repository.save_config() writes - export is a
    literal copy of the on-disk record, not a re-derived subset."""
    return json.dumps(record, indent=2)


def parse_import(text: str) -> tuple:
    """Returns (CoilSet, name) from previously-exported JSON text, or raises
    ValueError with a specific, human-readable reason. v1 only accepts the
    exact shape export_record() writes (round-trip only) - a bare 56-field
    coilset dict without the wrapper is rejected, not silently guessed at.
    """
    from models.coilset import CoilSet

    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(record, dict) or not isinstance(record.get("coilset"), dict):
        raise ValueError("Missing or malformed 'coilset' section.")

    name = str(record.get("name") or "").strip()
    if not name:
        raise ValueError("Missing config name.")

    field_types = field_layout.field_types()
    expected_fields = set(field_types)
    actual_fields = set(record["coilset"])
    if expected_fields != actual_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError(
            f"Config fields don't match this app's CoilSet schema (missing={missing}, extra={extra})."
        )

    # Field *values* are checked too, not just field names: CoilSet is a
    # plain dataclass and won't reject a string where a number belongs, so
    # without this a wrong-typed value sails through construction and then
    # crashes inside check_valid_coilset() with a raw TypeError.
    for fname, ftype in field_types.items():
        value = record["coilset"][fname]
        if ftype is str:
            if not isinstance(value, str):
                raise ValueError(f"Field {fname!r} should be text, got {type(value).__name__}: {value!r}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Field {fname!r} should be a number, got {type(value).__name__}: {value!r}")

    try:
        coilset = CoilSet(**record["coilset"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid config data: {exc}") from exc

    return coilset, name
