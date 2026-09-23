# =============================================================================
# run_result_repository.py
#
# Owns everything a run writes to the `runs` table (engine/repositories/db.py).
# Generic - callers (main.py, frontend/src/frontend/services/job_runner.py, webapp's
# job_runner.py) build their own payload dict; this repository doesn't know
# or care what's inside the "result" section, only the columns it promotes
# for querying/sorting.
#
# root= is kept for backward compatibility with every existing call site
# (frontend/src/frontend/services/job_runner.py, frontend/src/frontend/screen_manual/runs_panel.py, webapp's
# job_runner.py/routers) - it now names the *directory* that holds
# femm_automation.db rather than a directory of loose JSON files. In
# production that directory is backend/data/ (the same directory
# engine/repositories/db.py's DB_PATH lives in, deliberately - configs and runs
# have to be in the same file for runs.config_id to be a real foreign key),
# so root=RESULTS_ROOT still resolves to the one shared database. When root
# isn't given at all, this falls back to db.DB_PATH directly.
# =============================================================================
import datetime
import json
from pathlib import Path

from engine.repositories import db

_ROW_COLUMNS = (
    'run_id', 'config_id', 'config_name', 'distance', 'status',
    'created_at', 'started_at', 'finished_at', 'last_used_at', 'error',
)


def _row_to_record(row) -> dict:
    record = {col: row[col] for col in _ROW_COLUMNS}
    record['result'] = json.loads(row['result_json']) if row['result_json'] is not None else None
    record['coilset_hash'] = row['coilset_hash']
    return record


def _db_path_for(root) -> Path | None:
    return (Path(root) / 'femm_automation.db') if root is not None else None


class RunResultRepository:
    def __init__(self, run_id=None, root=None):
        self.run_id = run_id or '{:%Y%m%dT%H%M%S}'.format(datetime.datetime.now())
        self._db_path = _db_path_for(root)

    def save_result_json(self, payload):
        """Upserts by run_id - matches the old write-the-file-again
        overwrite behavior used by both JobRunner._run()'s final persist and
        touch()'s re-persist."""
        params = {col: payload.get(col) for col in _ROW_COLUMNS}
        params['run_id'] = payload.get('run_id', self.run_id)
        params['result_json'] = json.dumps(payload.get('result'), default=str) if payload.get('result') is not None else None
        params['coilset_hash'] = payload.get('coilset_hash')

        with db.connection(self._db_path) as conn:
            conn.execute(
                """INSERT INTO runs
                   (run_id, config_id, config_name, distance, status, created_at,
                    started_at, finished_at, last_used_at, error, result_json, coilset_hash)
                   VALUES (:run_id, :config_id, :config_name, :distance, :status, :created_at,
                           :started_at, :finished_at, :last_used_at, :error, :result_json, :coilset_hash)
                   ON CONFLICT(run_id) DO UPDATE SET
                       config_id=excluded.config_id, config_name=excluded.config_name,
                       distance=excluded.distance, status=excluded.status, created_at=excluded.created_at,
                       started_at=excluded.started_at, finished_at=excluded.finished_at,
                       last_used_at=excluded.last_used_at, error=excluded.error,
                       result_json=excluded.result_json, coilset_hash=excluded.coilset_hash""",
                params,
            )
            conn.commit()

    @staticmethod
    def load_result(run_id, root=None):
        with db.connection(_db_path_for(root)) as conn:
            row = conn.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone()
        return _row_to_record(row) if row else None

    @staticmethod
    def list_results(root=None):
        with db.connection(_db_path_for(root)) as conn:
            rows = conn.execute(
                'SELECT * FROM runs ORDER BY COALESCE(finished_at, created_at) DESC'
            ).fetchall()
        return [_row_to_record(r) for r in rows]
