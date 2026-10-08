# =============================================================================
# ai_run_repository.py --> CRUD for the `ai_runs` and `ai_run_messages`
# tables. They live in the same SQLite file as the engine's configs/runs
# (engine/repositories/db.py owns the connection) - on purpose, so an agent
# run's baseline_config_id can be joined to its config - but they are defined
# and created HERE (_AI_SCHEMA), so engine/ never has to know the agent exists.
#
# One ai_run row per agent run (interview phase through optimize phase), one
# ai_run_messages row per interview exchange or optimize-phase round entry -
# a single ordered (ai_run_id, phase, seq) table for both phases so the full
# session replays with one query, while `phase` still cheaply filters each
# browsing view. This module is the one place that touches either table.
# =============================================================================
import contextlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from engine.repositories import db

# The schema version this module writes, kept in SQLite's `PRAGMA user_version`
# (0 = the ai_runs table as it was before run lifecycle states existed,
# 1 = before a chat could be imported from a file).
SCHEMA_VERSION = 2

# How a run moves: interviewing -> plan_ready (the model proposed a plan the code
# accepted) -> running (the user clicked Start Search) -> one terminal status.
# `status` says how it ended; `stop_reason` says which rule ended it.
TERMINAL_STATUSES = ('goal_met', 'budget_exhausted', 'stopped_by_model', 'stalled', 'failed',
                     'interrupted', 'cancelled')
STATUSES = ('interviewing', 'plan_ready', 'running') + TERMINAL_STATUSES
STOP_REASONS = ('nothing_left', 'plateau', 'stalled', 'rounds', 'time', 'simulations', 'model_stopped',
                'user_stopped', 'llm_failed', 'internal_error')

# Every allowed status change (SPEC-DETAILED.md section 5). transition() is the
# one function that changes a row's status and rejects anything not listed here.
_ALLOWED = {
    'interviewing': {'plan_ready', 'cancelled'},
    'plan_ready': {'plan_ready', 'running', 'cancelled'},
    'running': set(TERMINAL_STATUSES),
    # A finished run can be continued (running) or given a revised plan (plan_ready).
    **{status: {'running', 'plan_ready'} for status in TERMINAL_STATUSES},
}

# The agent's tables - what engine/repositories/db.py used to create for it.
# (Foreign keys are documentation only, exactly as in db.py - see db.connect().)
_RUNS_TABLE = """
CREATE TABLE {name} (
    id                    TEXT PRIMARY KEY,
    baseline_config_id    TEXT REFERENCES configs (id),  -- documents intent only, not enforced - see connect()
    spec_json             TEXT,  -- NULL while still interviewing
    status                TEXT NOT NULL CHECK (status IN (%s)),
    created_at            TEXT NOT NULL,
    started_at            TEXT,  -- when the search began (Start Search)
    finished_at           TEXT,
    error                 TEXT,  -- the outcome report, for any ending
    best_round            INTEGER,
    best_coilset_json     TEXT,  -- denormalized, for fast history-list rendering
    best_tool_result_json TEXT,
    stop_reason           TEXT,  -- which rule ended the search (STOP_REASONS); NULL while not terminal
    budget_json           TEXT,  -- list, one entry per search segment: limits in force and what was used
    fragility_json        TEXT,  -- list of fragility checks (filled from milestone M3b)
    baseline_coilset_json TEXT,  -- the baseline as it was at Start Search (a config can be edited later)
    imported_at           TEXT   -- set on a chat that came from an exported file (agent/services/chat_export.py)
)""" % ", ".join(f"'{s}'" for s in STATUSES)
_RUNS_INDEX = "CREATE INDEX IF NOT EXISTS idx_ai_runs_created_at ON ai_runs (created_at)"
_V0_COLUMNS = ("id, baseline_config_id, spec_json, status, created_at, started_at, finished_at, error, "
               "best_round, best_coilset_json, best_tool_result_json")

PHASES = ('interview', 'optimize')
ROLES = ('user', 'assistant', 'tool', 'system')

_MESSAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_run_messages (
    id           TEXT PRIMARY KEY,
    ai_run_id    TEXT NOT NULL REFERENCES ai_runs (id),  -- documents intent only, not enforced - see connect()
    phase        TEXT NOT NULL CHECK (phase IN (%s)),
    seq          INTEGER NOT NULL,  -- monotonic order within (ai_run_id, phase)
    role         TEXT NOT NULL CHECK (role IN (%s)),
    round_num    INTEGER,  -- NULL for interview rows
    content      TEXT,
    payload_json TEXT,  -- e.g. finalize_spec args, or a round's candidate list
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_run_messages_run ON ai_run_messages (ai_run_id, phase, seq);
""" % (", ".join(f"'{p}'" for p in PHASES), ", ".join(f"'{r}'" for r in ROLES))


class MigrationError(RuntimeError):
    """The ai_runs table could not be brought to SCHEMA_VERSION. Nothing was
    changed (the rebuild is one transaction); the message names the backup copy."""


class IllegalTransition(ValueError):
    """transition() was asked for a status change that isn't in _ALLOWED."""


_checked_paths: set[str] = set()  # database files whose schema this process has already checked


def _ensure_schema(conn) -> None:
    """Creates the agent's tables in a new database, or migrates an older
    ai_runs table. Checked once per database file per process - every entry
    point (the app, the benchmark, `python -m agent`, tests) opens the
    database through _connection(), so none can meet an old table."""
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if path in _checked_paths:
        return
    has_runs = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ai_runs'").fetchone()
    if not has_runs:
        conn.execute(_RUNS_TABLE.format(name="ai_runs"))
        conn.execute(_RUNS_INDEX)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    else:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            _migrate_v0(conn, Path(path))
        elif version < 2:
            _migrate_v1(conn, Path(path))
    conn.executescript(_MESSAGES_SCHEMA)
    _checked_paths.add(path)


def _migrate_v0(conn, path: Path) -> None:
    """Version 0 -> 1: SQLite can't alter a CHECK constraint, so ai_runs is
    rebuilt with the new statuses and columns. The whole database file is
    copied to backups/ first; then one transaction copies every row over
    unchanged (new columns NULL). Any failure rolls back and raises
    MigrationError naming the backup."""
    backup_path = _backup(conn, path, "v0")

    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit BEGIN/COMMIT, so the DDL below is really one transaction
    try:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0]
        conn.execute(_RUNS_TABLE.format(name="ai_runs_new"))
        conn.execute(f"INSERT INTO ai_runs_new ({_V0_COLUMNS}) SELECT {_V0_COLUMNS} FROM ai_runs")
        after = conn.execute("SELECT COUNT(*) FROM ai_runs_new").fetchone()[0]
        if after != before:
            raise RuntimeError(f"row count changed while copying ai_runs ({before} -> {after})")
        conn.execute("DROP TABLE ai_runs")  # every row is in ai_runs_new, counted above, same transaction
        conn.execute("ALTER TABLE ai_runs_new RENAME TO ai_runs")
        conn.execute(_RUNS_INDEX)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise MigrationError(
            f"Could not update the agent's run history in {path} ({exc!r}). Nothing was changed. "
            f"A copy of the database made just before is at {backup_path}.") from exc
    finally:
        conn.isolation_level = previous_isolation


def _migrate_v1(conn, path: Path) -> None:
    """Version 1 -> 2: adds ai_runs.imported_at. No row is touched, but the
    database file is still copied to backups/ first, like every migration."""
    backup_path = _backup(conn, path, "v1")
    try:
        conn.execute("ALTER TABLE ai_runs ADD COLUMN imported_at TEXT")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception as exc:
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        raise MigrationError(
            f"Could not update the agent's run history in {path} ({exc!r}). Nothing was changed. "
            f"A copy of the database made just before is at {backup_path}.") from exc


def _backup(conn, path: Path, tag: str) -> Path:
    """Copies the whole database file to backups/ before a migration; returns the copy's path."""
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}-{tag}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    copy = sqlite3.connect(backup_path)
    try:
        with copy:
            conn.backup(copy)
    finally:
        copy.close()
    return backup_path


def migrate_if_needed() -> None:
    """Opens the database once so its schema is checked now. The desktop app
    calls this at startup, where a MigrationError can be shown and stop the
    app before anything else touches the database."""
    with _connection():
        pass


@contextlib.contextmanager
def _connection():
    """db.connection() plus this module's own tables (see _ensure_schema)."""
    with db.connection() as conn:
        _ensure_schema(conn)
        yield conn


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    """json.dumps() fallback for values dataclasses.asdict() doesn't
    convert on its own - specifically agent.input_chat.models.variables.VariableSpec's
    enabled_groups/excluded_fields sets, which asdict() leaves as real
    Python sets. Sorted list, not str(obj) (this project's usual
    default=str fallback), so the value stays a real JSON array - a set
    stringified as "{'a', 'b'}" would need ast.literal_eval to read back,
    not just json.loads."""
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def _dumps(value):
    return json.dumps(value, default=_json_default) if value is not None else None


def _run_row_to_record(row) -> dict:
    return {
        'id': row['id'],
        'baseline_config_id': row['baseline_config_id'],
        'spec': json.loads(row['spec_json']) if row['spec_json'] else None,
        'status': row['status'],
        'created_at': row['created_at'],
        'started_at': row['started_at'],
        'finished_at': row['finished_at'],
        'error': row['error'],
        'best_round': row['best_round'],
        'best_coilset': json.loads(row['best_coilset_json']) if row['best_coilset_json'] else None,
        'best_tool_result': json.loads(row['best_tool_result_json']) if row['best_tool_result_json'] else None,
        # The version-1 columns. agent/report.py also reads databases that have
        # not been migrated yet (read-only, e.g. a benchmark's copy): None there.
        'stop_reason': _column(row, 'stop_reason'),
        'budget': _loads(_column(row, 'budget_json')),
        'fragility': _loads(_column(row, 'fragility_json')),
        'baseline_coilset': _loads(_column(row, 'baseline_coilset_json')),
        'imported_at': _column(row, 'imported_at'),
    }


def _column(row, name):
    return row[name] if name in row.keys() else None


def _loads(text):
    return json.loads(text) if text else None


def _message_row_to_record(row) -> dict:
    return {
        'id': row['id'],
        'ai_run_id': row['ai_run_id'],
        'phase': row['phase'],
        'seq': row['seq'],
        'role': row['role'],
        'round_num': row['round_num'],
        'content': row['content'],
        'payload': json.loads(row['payload_json']) if row['payload_json'] else None,
        'created_at': row['created_at'],
    }


_JSON_COLUMNS = {'spec': 'spec_json', 'best_coilset': 'best_coilset_json', 'best_tool_result': 'best_tool_result_json',
                 'budget': 'budget_json', 'fragility': 'fragility_json', 'baseline_coilset': 'baseline_coilset_json'}
_PLAIN_COLUMNS = ('id', 'baseline_config_id', 'status', 'created_at', 'started_at', 'finished_at', 'error',
                  'best_round', 'stop_reason', 'imported_at')


def _upsert_run(conn, record) -> None:
    values = {name: record.get(name) for name in _PLAIN_COLUMNS}
    values.update({column: _dumps(record.get(key)) for key, column in _JSON_COLUMNS.items()})
    columns = list(values)
    conn.execute(
        f"INSERT INTO ai_runs ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in columns if c != 'id')}",
        values,
    )
    conn.commit()


def create_ai_run(baseline_config_id) -> dict:
    """Starts a new ai_run in the 'interviewing' state."""
    record = {
        'id': uuid.uuid4().hex[:12],
        'baseline_config_id': baseline_config_id,
        'spec': None,
        'status': 'interviewing',
        'created_at': _now_iso(),
        'started_at': None,
        'finished_at': None,
        'error': None,
        'best_round': None,
        'best_coilset': None,
        'best_tool_result': None,
        'stop_reason': None,
        'budget': None,
        'fragility': None,
        'baseline_coilset': None,
        'imported_at': None,
    }
    with _connection() as conn:
        _upsert_run(conn, record)
    return record


def insert_imported_run(run: dict, messages: list[dict]) -> str:
    """Stores a chat that came from an exported file as a NEW run and returns
    its id. `run` holds the ai_runs fields as get_ai_run() returns them (no
    id), `messages` the ai_run_messages fields (no id, no ai_run_id); both
    were checked by agent/services/chat_export.py's validate(). The run keeps
    its status and timestamps, gets imported_at, and is a plain INSERT under a
    fresh id, so no existing row can be overwritten. One transaction: any
    failure leaves nothing behind."""
    with _connection() as conn:
        try:
            while True:
                run_id = uuid.uuid4().hex[:12]
                if conn.execute('SELECT 1 FROM ai_runs WHERE id = ?', (run_id,)).fetchone() is None:
                    break
            values = {name: run.get(name) for name in _PLAIN_COLUMNS}
            values.update({column: _dumps(run.get(key)) for key, column in _JSON_COLUMNS.items()})
            values.update(id=run_id, imported_at=_now_iso())
            columns = list(values)
            conn.execute(f"INSERT INTO ai_runs ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)})",
                         values)
            for message in messages:
                conn.execute(
                    """INSERT INTO ai_run_messages (id, ai_run_id, phase, seq, role, round_num, content, payload_json, created_at)
                       VALUES (:id, :ai_run_id, :phase, :seq, :role, :round_num, :content, :payload_json, :created_at)""",
                    {'id': uuid.uuid4().hex[:12], 'ai_run_id': run_id, 'phase': message['phase'],
                     'seq': message['seq'], 'role': message['role'], 'round_num': message.get('round_num'),
                     'content': message.get('content'), 'payload_json': _dumps(message.get('payload')),
                     'created_at': message['created_at']})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return run_id


_TRANSITION_FIELDS = ('spec', 'started_at', 'finished_at', 'error', 'stop_reason', 'budget', 'fragility',
                      'baseline_coilset', 'best_round', 'best_coilset', 'best_tool_result')


def transition(ai_run_id, new_status, **fields) -> dict:
    """The one way a run's status changes. Raises IllegalTransition (and writes
    nothing) for a move that isn't in _ALLOWED, KeyError for an unknown run.

    `fields` may set any of _TRANSITION_FIELDS along with the status; a field
    not passed (or passed as None) keeps what the row already has, so a
    terminal-status call doesn't have to re-pass a best_coilset an earlier
    call already set. Moving to 'running' clears finished_at, stop_reason and
    error first: they describe an ending the run is leaving behind (continue)."""
    unknown = set(fields) - set(_TRANSITION_FIELDS)
    if unknown:
        raise TypeError(f"transition() can't set {sorted(unknown)}")
    with _connection() as conn:
        row = conn.execute('SELECT * FROM ai_runs WHERE id = ?', (ai_run_id,)).fetchone()
        if row is None:
            raise KeyError(f"No such ai_run: {ai_run_id!r}")
        record = _run_row_to_record(row)
        if new_status not in _ALLOWED.get(record['status'], ()):
            raise IllegalTransition(f"ai_run {ai_run_id}: {record['status']} -> {new_status} is not an allowed change")
        if new_status == 'running':
            record.update(finished_at=None, stop_reason=None, error=None)
            if fields.get('spec') is not None:
                # A revised plan starts: the earlier plan's best design is not this plan's.
                record.update(best_round=None, best_coilset=None, best_tool_result=None)
        record['status'] = new_status
        record.update({name: value for name, value in fields.items() if value is not None})
        _upsert_run(conn, record)
    return record


def finalize_spec(ai_run_id, spec) -> None:
    """The model proposed a plan and the code accepted it: stores the spec and
    moves the run to 'plan_ready'. Nothing is searched until the user clicks
    Start Search (AgentRunner.submit() moves it to 'running'). Called again
    for a revised plan - also after a search has ended, which leaves the
    finished search's rounds and best design in place.

    The plan being replaced is gone once this returns, and restoring the run
    later must know which distances its rounds were simulated at (a revised
    plan that adds one starts the search over). So a finished segment whose
    budget entry doesn't say - a run from before entries carried it - gets
    the old plan's distances written in first."""
    record = get_ai_run(ai_run_id)
    budget = None
    if record and record['status'] in TERMINAL_STATUSES and record.get('spec') and record.get('budget'):
        if 'distances' not in record['budget'][-1]:
            budget = [*record['budget'][:-1],
                      {**record['budget'][-1], 'distances': list(record['spec'].get('distances') or [])}]
    transition(ai_run_id, 'plan_ready', spec=spec, budget=budget)


def reap_running() -> int:
    """Marks every 'running' row 'interrupted' - called once when the app
    starts, when no search can be running (a search lives in the app's own
    process, and one app instance per database is assumed). Finished rounds
    stay with the row. Returns how many rows it changed."""
    with _connection() as conn:
        cursor = conn.execute(
            "UPDATE ai_runs SET status = 'interrupted', finished_at = COALESCE(finished_at, ?) WHERE status = 'running'",
            (_now_iso(),))
        conn.commit()
        return cursor.rowcount


def append_message(ai_run_id, phase, seq, role, round_num=None, content=None, payload=None) -> dict:
    record = {
        'id': uuid.uuid4().hex[:12],
        'ai_run_id': ai_run_id,
        'phase': phase,
        'seq': seq,
        'role': role,
        'round_num': round_num,
        'content': content,
        'payload': payload,
        'created_at': _now_iso(),
    }
    with _connection() as conn:
        conn.execute(
            """INSERT INTO ai_run_messages (id, ai_run_id, phase, seq, role, round_num, content, payload_json, created_at)
               VALUES (:id, :ai_run_id, :phase, :seq, :role, :round_num, :content, :payload_json, :created_at)""",
            {**record, 'payload_json': _dumps(record['payload'])},
        )
        conn.commit()
    return record


def list_ai_runs() -> list[dict]:
    """Every ai_run, newest first."""
    with _connection() as conn:
        rows = conn.execute('SELECT * FROM ai_runs ORDER BY created_at DESC').fetchall()
    return [_run_row_to_record(r) for r in rows]


def get_ai_run(ai_run_id) -> dict | None:
    with _connection() as conn:
        row = conn.execute('SELECT * FROM ai_runs WHERE id = ?', (ai_run_id,)).fetchone()
    return _run_row_to_record(row) if row else None


def list_chat_rows() -> list[dict]:
    """One light row per ai_run, newest first, for a list of past chats: id,
    baseline_config_id, status, created_at, and what to call it - the plan's
    objective text and the user's first message (either may be None). Reads
    none of the large JSON columns list_ai_runs() parses."""
    with _connection() as conn:
        rows = conn.execute(
            """SELECT r.id, r.baseline_config_id, r.status, r.created_at, r.imported_at,
                      json_extract(r.spec_json, '$.objective_text') AS objective_text,
                      (SELECT m.content FROM ai_run_messages m
                        WHERE m.ai_run_id = r.id AND m.phase = 'interview' AND m.role = 'user'
                        ORDER BY m.seq LIMIT 1) AS first_message
                 FROM ai_runs r ORDER BY r.created_at DESC""").fetchall()
    return [dict(row) for row in rows]


def list_messages(ai_run_id, phase=None) -> list[dict]:
    """Every message for one ai_run, in replay order. Within one phase that is
    `seq`. phase=None returns both phases in the order they were written
    (created_at): a run can hold several search segments with chat messages
    between them, so interview rows no longer all come before optimize rows."""
    query = 'SELECT * FROM ai_run_messages WHERE ai_run_id = ?'
    params = [ai_run_id]
    if phase is not None:
        query += ' AND phase = ? ORDER BY seq'
        params.append(phase)
    else:
        query += ' ORDER BY created_at, phase, seq'
    with _connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_message_row_to_record(r) for r in rows]


def delete_ai_run(ai_run_id) -> None:
    """Removes an ai_run and all its messages. No-op if it doesn't exist.
    Only ever an explicit user action (Delete in the chat sidebar) - a stopped or
    cancelled run is kept, with its plan and every finished round."""
    with _connection() as conn:
        conn.execute('DELETE FROM ai_run_messages WHERE ai_run_id = ?', (ai_run_id,))
        conn.execute('DELETE FROM ai_runs WHERE id = ?', (ai_run_id,))
        conn.commit()
