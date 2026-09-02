# =============================================================================
# coilset_repository.py
#
# The unified config store: one JSON file per config under data/configs/,
# covering both the built-in reference designs (builtin=True, e.g. Gen3,
# base_coil) and user-created/cloned configs (builtin=False). Built-in
# configs can be edited in place but not deleted - save_config() preserves
# an existing record's builtin flag, and delete_config() refuses to remove
# one. This module is the one place that touches data/configs/.
# =============================================================================
import dataclasses
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models.coilset import CoilSet

CONFIGS_DIR = Path(__file__).resolve().parents[1] / 'data' / 'configs'


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _path(config_id):
    return CONFIGS_DIR / f'{config_id}.json'


def list_configs():
    """Every config record (builtin + user-created), sorted by name."""
    if not CONFIGS_DIR.is_dir():
        return []
    records = [json.loads(p.read_text()) for p in CONFIGS_DIR.glob('*.json')]
    return sorted(records, key=lambda r: r['name'].lower())


def get_config(config_id):
    path = _path(config_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_coilset(config_id):
    """Raises KeyError if config_id doesn't exist."""
    record = get_config(config_id)
    if record is None:
        raise KeyError(f"No such config: {config_id!r}")
    return CoilSet(**record['coilset'])


def save_config(coilset, name, config_id=None, cloned_from=None):
    """Create (config_id=None) or overwrite (config_id given) a config.

    Overwriting an existing record preserves its builtin flag - this is the
    whole mechanism behind "built-ins are editable but not deletable".
    """
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    existing = get_config(config_id) if config_id else None
    record = {
        'id': config_id or uuid.uuid4().hex[:12],
        'name': name,
        'builtin': existing['builtin'] if existing else False,
        'created_at': existing['created_at'] if existing else _now_iso(),
        'updated_at': _now_iso(),
        'cloned_from': cloned_from if cloned_from is not None else (existing.get('cloned_from') if existing else None),
        'coilset': dataclasses.asdict(coilset),
    }
    _path(record['id']).write_text(json.dumps(record, indent=2))
    return record


def delete_config(config_id):
    """Raises ValueError for a built-in config. No-op if it doesn't exist."""
    record = get_config(config_id)
    if record is None:
        return
    if record.get('builtin'):
        raise ValueError(f"Cannot delete built-in config: {config_id}")
    _path(config_id).unlink()
