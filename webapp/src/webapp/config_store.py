"""JSON-file CRUD for user-saved coil configs.

Deliberately separate from noelle's coilset_repository.py, which stays
read-only and is used only as a source of clone-from templates (see
template_registry.py). One JSON file per config under webapp/data/configs/.
"""

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "data" / "configs"  # webapp/data/configs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _path(config_id: str) -> Path:
    return CONFIGS_DIR / f"{config_id}.json"


def list_configs() -> list[dict[str, Any]]:
    if not CONFIGS_DIR.is_dir():
        return []
    return [json.loads(path.read_text()) for path in sorted(CONFIGS_DIR.glob("*.json"))]


def load_config(config_id: str) -> dict[str, Any] | None:
    path = _path(config_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_config(
    coilset: Any,
    name: str,
    config_id: str | None = None,
    cloned_from: str | None = None,
) -> dict[str, Any]:
    """Create (config_id=None) or overwrite (config_id given) a saved config."""
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_config(config_id) if config_id else None
    record = {
        "id": config_id or new_id(),
        "name": name,
        "created_at": existing["created_at"] if existing else _now_iso(),
        "updated_at": _now_iso(),
        "cloned_from": cloned_from if cloned_from is not None else (existing.get("cloned_from") if existing else None),
        "coilset": asdict(coilset),
    }
    _path(record["id"]).write_text(json.dumps(record, indent=2))
    return record


def delete_config(config_id: str) -> None:
    path = _path(config_id)
    if path.exists():
        path.unlink()
