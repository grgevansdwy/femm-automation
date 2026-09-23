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
import uuid
from datetime import datetime, timezone

from engine.repositories import db

# The agent's tables - what engine/repositories/db.py used to create for it.
# (Foreign keys are documentation only, exactly as in db.py - see db.connect().)
_AI_SCHEMA = """
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


@contextlib.contextmanager
def _connection():
    """db.connection() plus this module's own tables. db.connect() only creates
    the engine's tables; the CREATE ... IF NOT EXISTS here is idempotent, so
    it costs next to nothing after the first call - the same per-call approach
    db.connect() already takes for its own schema."""
    with db.connection() as conn:
        conn.executescript(_AI_SCHEMA)
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
    }


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


def _upsert_run(conn, record) -> None:
    conn.execute(
        """INSERT INTO ai_runs (id, baseline_config_id, spec_json, status, created_at, started_at,
                                 finished_at, error, best_round, best_coilset_json, best_tool_result_json)
           VALUES (:id, :baseline_config_id, :spec_json, :status, :created_at, :started_at,
                   :finished_at, :error, :best_round, :best_coilset_json, :best_tool_result_json)
           ON CONFLICT(id) DO UPDATE SET
               baseline_config_id=excluded.baseline_config_id, spec_json=excluded.spec_json,
               status=excluded.status, created_at=excluded.created_at, started_at=excluded.started_at,
               finished_at=excluded.finished_at, error=excluded.error, best_round=excluded.best_round,
               best_coilset_json=excluded.best_coilset_json, best_tool_result_json=excluded.best_tool_result_json""",
        {
            'id': record['id'],
            'baseline_config_id': record['baseline_config_id'],
            'spec_json': _dumps(record['spec']),
            'status': record['status'],
            'created_at': record['created_at'],
            'started_at': record['started_at'],
            'finished_at': record['finished_at'],
            'error': record['error'],
            'best_round': record['best_round'],
            'best_coilset_json': _dumps(record['best_coilset']),
            'best_tool_result_json': _dumps(record['best_tool_result']),
        },
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
    }
    with _connection() as conn:
        _upsert_run(conn, record)
    return record


def finalize_spec(ai_run_id, spec) -> None:
    """Interview phase concluded - stores the finalized spec and moves the
    run into 'running' (the optimize phase begins here)."""
    record = get_ai_run(ai_run_id)
    if record is None:
        raise KeyError(f"No such ai_run: {ai_run_id!r}")
    record['spec'] = spec
    record['status'] = 'running'
    record['started_at'] = _now_iso()
    with _connection() as conn:
        _upsert_run(conn, record)


def update_run_status(ai_run_id, status, error=None, finished_at=None,
                       best_round=None, best_coilset=None, best_tool_result=None) -> None:
    """Only overwrites fields explicitly passed - error/finished_at/best_*
    default to whatever the row already has, not None, so a terminal-status
    call doesn't have to re-pass a best_coilset an earlier call already set."""
    record = get_ai_run(ai_run_id)
    if record is None:
        raise KeyError(f"No such ai_run: {ai_run_id!r}")
    record['status'] = status
    if error is not None:
        record['error'] = error
    if finished_at is not None:
        record['finished_at'] = finished_at
    if best_round is not None:
        record['best_round'] = best_round
    if best_coilset is not None:
        record['best_coilset'] = best_coilset
    if best_tool_result is not None:
        record['best_tool_result'] = best_tool_result
    with _connection() as conn:
        _upsert_run(conn, record)


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


def list_messages(ai_run_id, phase=None) -> list[dict]:
    """Every message for one ai_run, in replay order. phase=None returns
    both phases interleaved in (phase, seq) order - interview rows (all
    phase='interview') sort before optimize rows, matching session order
    since the interview always finishes before optimize begins."""
    query = 'SELECT * FROM ai_run_messages WHERE ai_run_id = ?'
    params = [ai_run_id]
    if phase is not None:
        query += ' AND phase = ?'
        params.append(phase)
    query += ' ORDER BY phase, seq'
    with _connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_message_row_to_record(r) for r in rows]


def delete_ai_run(ai_run_id) -> None:
    """Removes an ai_run and all its messages. No-op if it doesn't exist.
    Used when the GUI discards an unfinished run (Restart before the search
    finished) - a finished run is never deleted this way."""
    with _connection() as conn:
        conn.execute('DELETE FROM ai_run_messages WHERE ai_run_id = ?', (ai_run_id,))
        conn.execute('DELETE FROM ai_runs WHERE id = ?', (ai_run_id,))
        conn.commit()
