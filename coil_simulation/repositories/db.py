# =============================================================================
# db.py
#
# Shared SQLite connection + schema for the `configs` and `runs` tables.
# Both live in one database file (DB_PATH) deliberately - queries that join
# a run to its config only work if they're in the same file, and it keeps
# the whole app's data (minus constants.json) in one thing to back up.
# runs.config_id references configs.id but isn't enforced - see connect().
# coilset_repository.py and run_result_repository.py are the only two
# callers that touch these tables.
#
# Every call opens and closes its own short-lived connection (see
# connection() below) rather than holding one open for the app's lifetime -
# at this app's scale (a desktop tool, a few hundred rows) the per-call
# connect overhead is sub-millisecond and this keeps the repositories'
# per-function-call style unchanged from their old per-function-call file
# I/O.
# =============================================================================
import contextlib
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'femm_automation.db'

_SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    builtin      INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    cloned_from  TEXT,
    coilset_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_configs_name ON configs (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    config_id     TEXT REFERENCES configs (id),  -- documents intent only, not enforced - see connect()
    config_name   TEXT NOT NULL,
    distance      REAL,
    status        TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    last_used_at  TEXT,
    error         TEXT,
    result_json   TEXT,
    coilset_hash  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_config_id    ON runs (config_id);
CREATE INDEX IF NOT EXISTS idx_runs_cache_lookup ON runs (status, coilset_hash);
CREATE INDEX IF NOT EXISTS idx_runs_last_used_at ON runs (last_used_at);

CREATE TABLE IF NOT EXISTS ai_runs (
    id                    TEXT PRIMARY KEY,
    baseline_config_id    TEXT REFERENCES configs (id),  -- documents intent only, not enforced - see connect()
    spec_json             TEXT,  -- NULL while still interviewing
    status                TEXT NOT NULL CHECK (status IN
        ('interviewing', 'running', 'goal_met', 'budget_exhausted', 'stopped_by_model', 'failed')),
    created_at            TEXT NOT NULL,
    started_at            TEXT,  -- when the optimize phase began
    finished_at           TEXT,
    error                 TEXT,
    best_round            INTEGER,
    best_coilset_json     TEXT,  -- denormalized, for fast history-list rendering
    best_tool_result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_runs_created_at ON ai_runs (created_at);

CREATE TABLE IF NOT EXISTS ai_run_messages (
    id           TEXT PRIMARY KEY,
    ai_run_id    TEXT NOT NULL REFERENCES ai_runs (id),  -- documents intent only, not enforced - see connect()
    phase        TEXT NOT NULL CHECK (phase IN ('interview', 'optimize')),
    seq          INTEGER NOT NULL,  -- monotonic order within (ai_run_id, phase)
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
    round_num    INTEGER,  -- NULL for interview rows
    content      TEXT,
    payload_json TEXT,  -- e.g. finalize_spec args, or a round's candidate list
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_run_messages_run ON ai_run_messages (ai_run_id, phase, seq);
"""


def connect(db_path=None) -> sqlite3.Connection:
    """Opens a connection to db_path (default: the *current* value of
    module-level DB_PATH, looked up fresh on every call so tests can
    monkeypatch db.DB_PATH and have every repository function pick it up),
    creating the schema if it isn't there yet.

    journal_mode=WAL matters specifically because job_runner.py persists a
    finished run from a ThreadPoolExecutor worker thread while the GUI's
    main thread reads (list_results()/find_cached()) every frame - WAL lets
    those reads proceed without waiting on a concurrent writer, instead of
    the default rollback-journal mode's whole-file write lock.

    foreign_keys enforcement is deliberately left OFF (SQLite's default):
    the old flat-file store never validated that a result's config_id
    pointed at a real config either, and runs_panel.py already tolerates a
    stale/missing config_id gracefully. Turning enforcement on would reject
    a run whose config was deleted (or never existed - e.g. a plain string
    id used in a test fixture) instead of just leaving it unresolved, which
    is a stricter behavior than anything actually relies on.
    """
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


@contextlib.contextmanager
def connection(db_path=None):
    """`with db.connection() as conn:` - connects, yields, always closes."""
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
