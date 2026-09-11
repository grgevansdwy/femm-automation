"""Bulk backup/restore: dumps every config + every run to one JSON file, and
restores from one. Pure logic, no imgui import - UI glue (the toolbar
buttons) lives in main.py.

Restore is an *exact* reproduction, not "create new" the way importing a
single config always does: it goes through coilset_repository.restore_config()
and RunResultRepository.save_result_json() (both upsert-by-id), so ids,
timestamps and the builtin flag come back exactly as exported instead of
being re-derived like save_config()'s edit semantics would.
"""

import json
from datetime import datetime, timezone


def export_all() -> str:
    from repositories.coilset_repository import list_configs
    from repositories.run_result_repository import RunResultRepository

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "configs": list_configs(),
        "runs": RunResultRepository.list_results(),
    }
    return json.dumps(payload, indent=2, default=str)


def import_all(text: str) -> tuple[int, int]:
    """Returns (configs_restored, runs_restored). Raises ValueError on a
    malformed backup file."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "configs" not in payload or "runs" not in payload:
        raise ValueError("Not a backup file: expected an object with 'configs' and 'runs'.")
    configs, runs = payload["configs"], payload["runs"]
    if not isinstance(configs, list) or not isinstance(runs, list):
        raise ValueError("Malformed backup: 'configs' and 'runs' must be lists.")

    from repositories.coilset_repository import restore_config
    from repositories.run_result_repository import RunResultRepository

    for record in configs:
        restore_config(record)
    for row in runs:
        RunResultRepository(run_id=row["run_id"]).save_result_json(row)

    return len(configs), len(runs)
