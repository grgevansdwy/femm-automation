# =============================================================================
# coilset_repository.py --> CRUD for the `configs` table (engine/repositories/db.py)
#
# The unified config store: one row per config, covering both the built-in
# reference designs (builtin=True, e.g. Gen3, base_coil) and user-created/
# cloned configs (builtin=False). Built-in configs can be edited in place but
# not deleted without force=True - save_config() preserves an existing
# record's builtin flag, and delete_config() refuses to remove one unless
# forced. This module is the one place that touches the `configs` table.
#
# A fresh database starts empty, but the desktop app calls
# seed_starter_configs() at launch: if there are no configs at all it loads the
# reference designs in backend/data/starter_configs/*.json, so a new checkout
# has something to simulate. Backup All / Restore All
# (frontend/src/frontend/files_backup/backup_io.py) is how configs (and runs)
# move between machines or get restored after testing something risky.
# =============================================================================
import dataclasses
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from engine.models.coilset import CoilSet
from engine.repositories import db


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row) -> dict:
    coilset = json.loads(row['coilset_json'])
    # The record's name is the one config name: the list shows it, and the
    # form's config_name field is forced to match it. Older rows (migrated
    # from the legacy templates, where config_name was a free-text label like
    # "4mm RC ferrite, FJ7" while the name was the template's file id) store
    # two different values - this read-side sync is what keeps those from
    # showing one name in the list and another in the form.
    coilset['config_name'] = row['name']
    return {
        'id': row['id'],
        'name': row['name'],
        'builtin': bool(row['builtin']),
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'cloned_from': row['cloned_from'],
        'coilset': coilset,
    }


def _upsert(conn, record) -> None:
    # Same rule on the write side (see _row_to_record) - every save, clone,
    # import and backup restore stores config_name == name.
    coilset = {**record['coilset'], 'config_name': record['name']}
    conn.execute(
        """INSERT INTO configs (id, name, builtin, created_at, updated_at, cloned_from, coilset_json)
           VALUES (:id, :name, :builtin, :created_at, :updated_at, :cloned_from, :coilset_json)
           ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, builtin=excluded.builtin, created_at=excluded.created_at,
               updated_at=excluded.updated_at, cloned_from=excluded.cloned_from,
               coilset_json=excluded.coilset_json""",
        {
            'id': record['id'],
            'name': record['name'],
            'builtin': int(record['builtin']),
            'created_at': record['created_at'],
            'updated_at': record['updated_at'],
            'cloned_from': record.get('cloned_from'),
            'coilset_json': json.dumps(coilset),
        },
    )
    conn.commit()


def list_configs():
    """Every config record (builtin + user-created), sorted by name."""
    with db.connection() as conn:
        rows = conn.execute('SELECT * FROM configs ORDER BY name COLLATE NOCASE').fetchall()
    return [_row_to_record(r) for r in rows]


def name_conflict(name: str, exclude_id: str | None = None) -> bool:
    """True if some other config already has this name (case-insensitive,
    matching how list_configs() sorts)."""
    target = name.strip().lower()
    return any(c["name"].strip().lower() == target and c["id"] != exclude_id for c in list_configs())


def dedupe_name(base_name: str) -> str:
    """The first available name starting from base_name: the bare name if
    free, otherwise "<base_name> (2)", "(3)", ... Same shape as
    frontend/src/frontend/files_backup/config_io.py's dedupe_name(), kept as
    a separate backend-side copy since backend/agent/ must not import
    frontend code."""
    if not name_conflict(base_name):
        return base_name
    n = 2
    while True:
        candidate = f"{base_name} ({n})"
        if not name_conflict(candidate):
            return candidate
        n += 1


def get_config(config_id):
    with db.connection() as conn:
        row = conn.execute('SELECT * FROM configs WHERE id = ?', (config_id,)).fetchone()
    return _row_to_record(row) if row else None


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
    with db.connection() as conn:
        _upsert(conn, record)
    return record


def restore_config(record):
    """Upserts a config record exactly as given - id, timestamps and the
    builtin flag are preserved verbatim, unlike save_config()'s edit
    semantics (always a fresh updated_at, builtin re-derived from any
    existing row). The one exception is coilset.config_name, which is always
    set to the record's name (see _upsert). Used by bulk restore (frontend/src/frontend/files_backup/backup_io.py), which
    needs an exact reproduction of a previously-exported record. Returns
    the record unchanged."""
    with db.connection() as conn:
        _upsert(conn, record)
    return record


def delete_config(config_id, force=False):
    """Raises ValueError for a built-in config unless force=True. No-op if
    it doesn't exist. force=True is what the GUI uses - it lets a user
    delete any config, built-in or not (see
    frontend/src/frontend/screen_manual/configs_panel.py's ConfigsPanelState.delete())."""
    record = get_config(config_id)
    if record is None:
        return
    if record['builtin'] and not force:
        raise ValueError(f"Cannot delete built-in config: {config_id}")
    with db.connection() as conn:
        conn.execute('DELETE FROM configs WHERE id = ?', (config_id,))
        conn.commit()


STARTER_CONFIGS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'starter_configs'


def seed_starter_configs() -> int:
    """If the database has no configs at all, load every record in
    STARTER_CONFIGS_DIR (same shape as an exported config) and return how
    many were loaded; otherwise do nothing and return 0."""
    if list_configs():
        return 0
    loaded = 0
    for path in sorted(STARTER_CONFIGS_DIR.glob('*.json')):
        restore_config(json.loads(path.read_text()))
        loaded += 1
    return loaded
