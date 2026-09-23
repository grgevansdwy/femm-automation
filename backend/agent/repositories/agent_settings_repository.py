# =============================================================================
# agent_settings_repository.py --> the `agent_settings` table: one row per
# setting a person has changed from its default (key -> JSON value). Stores
# overrides only - a setting at its default has no row, so "reset to default"
# is just deleting the row, and a default changed in code reaches everyone who
# never customized it. Same shared SQLite file and same create-on-first-use
# approach as ai_run_repository.py; this module is the one place that touches
# the table. What the keys mean is agent/settings/settings.py's business.
# =============================================================================
import contextlib
import json
from datetime import datetime, timezone

from engine.repositories import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@contextlib.contextmanager
def _connection():
    with db.connection() as conn:
        conn.executescript(_SCHEMA)
        yield conn


def load_overrides() -> dict:
    """Every saved setting, key -> value."""
    with _connection() as conn:
        rows = conn.execute("SELECT key, value_json FROM agent_settings").fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


def replace_overrides(overrides: dict) -> None:
    """Makes the saved settings exactly `overrides`, in one transaction: rows
    for keys not in it are deleted (that is how a setting is reset)."""
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as conn:
        with conn:  # commit both together, or neither
            conn.execute("DELETE FROM agent_settings WHERE key NOT IN (%s)" % ",".join("?" * len(overrides)),
                         list(overrides))
            conn.executemany(
                """INSERT INTO agent_settings (key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
                [(key, json.dumps(value), now) for key, value in overrides.items()],
            )
