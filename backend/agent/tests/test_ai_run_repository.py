# =============================================================================
# test_ai_run_repository.py
#
# Checks the store backing AI-run history: ai_runs (one row per agent run)
# and ai_run_messages (one row per interview exchange or optimize round).
# Needs no FEMM, no LLM:
#     python3 -m agent.tests.test_ai_run_repository  (run from backend/)
#
# Under pytest, backend/conftest.py's autouse isolate_db fixture points this at
# a fresh, isolated database, so nothing here touches your real data. Run
# directly via `python3 -m agent.tests.test_ai_run_repository` instead (no pytest)
# and it runs against the real database - cleans up nothing itself, so
# prefer pytest for repeated runs.
# =============================================================================
import pytest

from agent.repositories import ai_run_repository as ar
from engine.repositories import db


def test_create_ai_run_starts_interviewing():
    record = ar.create_ai_run('Gen2_coil')
    assert record['status'] == 'interviewing'
    assert record['spec'] is None
    assert ar.get_ai_run(record['id']) == record
    print("  ok  create_ai_run() starts a run in 'interviewing' with no spec yet")


def test_finalize_spec_moves_to_plan_ready_not_running():
    record = ar.create_ai_run('Gen2_coil')
    spec = {'objective_text': 'match M23 within 5%', 'distances': [3.0, 5.0]}
    ar.finalize_spec(record['id'], spec)

    updated = ar.get_ai_run(record['id'])
    assert updated['status'] == 'plan_ready', "a proposed plan is not a live search"
    assert updated['spec'] == spec
    assert updated['started_at'] is None, "the search starts at Start Search, not when the plan is proposed"

    ar.finalize_spec(record['id'], {**spec, 'distances': [5.0]})
    assert ar.get_ai_run(record['id'])['spec']['distances'] == [5.0], "a revised plan replaces it"
    print("  ok  finalize_spec() stores the spec and moves the run to 'plan_ready'")


def _run_in(status):
    """A run walked to `status` through allowed changes only."""
    run_id = ar.create_ai_run('Gen2_coil')['id']
    if status == 'interviewing':
        return run_id
    if status == 'cancelled':
        ar.transition(run_id, 'cancelled')
        return run_id
    ar.finalize_spec(run_id, {'distances': [5.0]})
    if status != 'plan_ready':
        ar.transition(run_id, 'running')
        if status != 'running':
            ar.transition(run_id, status)
    return run_id


def test_transition_only_overwrites_passed_fields():
    run_id = _run_in('plan_ready')
    ar.transition(run_id, 'running', started_at='2026-09-09T00:00:00+00:00', baseline_coilset={'loop_r': 65})
    ar.transition(run_id, 'goal_met', finished_at='2026-09-10T00:00:00+00:00', best_round=3,
                  best_coilset={'loop_r': 50}, stop_reason='plateau', budget=[{'segment': 1, 'rounds_used': 3}])

    updated = ar.get_ai_run(run_id)
    assert (updated['status'], updated['stop_reason'], updated['best_round']) == ('goal_met', 'plateau', 3)
    assert updated['baseline_coilset'] == {'loop_r': 65}, "set by the earlier call, kept by this one"
    assert updated['budget'] == [{'segment': 1, 'rounds_used': 3}] and updated['fragility'] is None

    # "continue": back to running. The old ending's fields are cleared, the best design is not.
    ar.transition(run_id, 'running')
    updated = ar.get_ai_run(run_id)
    assert (updated['finished_at'], updated['stop_reason'], updated['error']) == (None, None, None)
    assert updated['best_coilset'] == {'loop_r': 50} and updated['started_at'] == '2026-09-09T00:00:00+00:00'
    print("  ok  transition() leaves unpassed fields untouched and clears the old ending on a continue")


def test_every_allowed_status_change_works_and_every_other_one_is_rejected():
    """SPEC-DETAILED.md section 5, both ways: the table is the whole truth."""
    terminal = set(ar.TERMINAL_STATUSES)
    allowed = {
        'interviewing': {'plan_ready', 'cancelled'},
        'plan_ready': {'plan_ready', 'running', 'cancelled'},
        'running': terminal,
        **{s: {'running', 'plan_ready'} for s in terminal},
    }
    assert set(allowed) == set(ar.STATUSES)
    for old in ar.STATUSES:
        for new in ar.STATUSES:
            run_id = _run_in(old)
            if new in allowed[old]:
                ar.transition(run_id, new)
                assert ar.get_ai_run(run_id)['status'] == new
            else:
                with pytest.raises(ar.IllegalTransition):
                    ar.transition(run_id, new, error='must not be written')
                after = ar.get_ai_run(run_id)
                assert after['status'] == old and after['error'] is None, f"{old} -> {new} must change nothing"

    with pytest.raises(KeyError):
        ar.transition('does-not-exist', 'running')
    with pytest.raises(TypeError):
        ar.transition(_run_in('plan_ready'), 'running', status='goal_met')
    print("  ok  transition() allows exactly the changes in the lifecycle table")


def test_reaper_marks_running_rows_interrupted_and_touches_nothing_else():
    ids = {status: _run_in(status) for status in ('interviewing', 'plan_ready', 'running', 'goal_met', 'cancelled')}
    ar.append_message(ids['running'], phase='optimize', seq=1, role='assistant', round_num=1, payload={'candidates': []})

    assert ar.reap_running() == 1
    assert ar.get_ai_run(ids['running'])['status'] == 'interrupted'
    assert ar.get_ai_run(ids['running'])['finished_at'] is not None
    assert len(ar.list_messages(ids['running'])) == 1, "finished rounds stay with the run"
    assert {s: ar.get_ai_run(i)['status'] for s, i in ids.items() if s != 'running'} == \
        {s: s for s in ids if s != 'running'}
    assert ar.reap_running() == 0 and not [r for r in ar.list_ai_runs() if r['status'] == 'running']
    ar.transition(ids['running'], 'running')  # an interrupted run can be continued
    print("  ok  reap_running(): no row is left 'running' after an app start")


# --- migration from the pre-lifecycle table (version 0) -----------------------

_V0_SCHEMA = """
CREATE TABLE ai_runs (
    id TEXT PRIMARY KEY, baseline_config_id TEXT, spec_json TEXT,
    status TEXT NOT NULL CHECK (status IN
        ('interviewing', 'running', 'goal_met', 'budget_exhausted', 'stopped_by_model', 'failed')),
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT, best_round INTEGER,
    best_coilset_json TEXT, best_tool_result_json TEXT
);
CREATE INDEX idx_ai_runs_created_at ON ai_runs (created_at);
CREATE TABLE ai_run_messages (
    id TEXT PRIMARY KEY, ai_run_id TEXT NOT NULL, phase TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
    round_num INTEGER, content TEXT, payload_json TEXT, created_at TEXT NOT NULL
);
"""
_V0_ROWS = [
    ('a', 'base_coil', None, 'interviewing', '2026-09-01T00:00:00+00:00', None, None, None, None, None, None),
    ('b', 'base_coil', '{"distances": [5.0]}', 'running', '2026-09-02T00:00:00+00:00', '2026-09-02T00:01:00+00:00',
     None, None, None, None, None),
    ('c', 'gone_config', '{"distances": [3.0]}', 'goal_met', '2026-09-03T00:00:00+00:00', '2026-09-03T00:01:00+00:00',
     '2026-09-03T01:00:00+00:00', None, 4, '{"loop_r": 50}', '{"goal_met": true}'),
    ('d', 'base_coil', '{}', 'failed', '2026-09-04T00:00:00+00:00', None, '2026-09-04T01:00:00+00:00',
     'Search failed: the AI server could not be reached.', None, None, None),
]


def _v0_database(path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript(_V0_SCHEMA)
    conn.executemany("INSERT INTO ai_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)", _V0_ROWS)
    conn.execute("INSERT INTO ai_run_messages VALUES ('m1','c','optimize',1,'assistant',1,'thinking','{}','2026-09-03')")
    conn.commit()
    conn.close()


def _raw(path, sql):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_migration_keeps_every_row_makes_a_backup_and_opens_the_new_statuses(tmp_path, monkeypatch):
    path = tmp_path / "old" / "femm_automation.db"
    path.parent.mkdir()
    _v0_database(path)
    monkeypatch.setattr(db, "DB_PATH", path)

    ar.migrate_if_needed()

    assert _raw(path, "PRAGMA user_version") == [(1,)]
    old_columns = "id, baseline_config_id, spec_json, status, created_at, started_at, finished_at, error, " \
                  "best_round, best_coilset_json, best_tool_result_json"
    assert _raw(path, f"SELECT {old_columns} FROM ai_runs ORDER BY id") == _V0_ROWS, "every value carried over unchanged"
    assert _raw(path, "SELECT stop_reason, budget_json, fragility_json, baseline_coilset_json FROM ai_runs") == \
        [(None, None, None, None)] * 4
    assert _raw(path, "SELECT COUNT(*) FROM ai_run_messages") == [(1,)]
    assert _raw(path, "SELECT name FROM sqlite_master WHERE name = 'idx_ai_runs_created_at'") == [('idx_ai_runs_created_at',)]

    (backup,) = list((path.parent / "backups").glob("femm_automation-v0-*.db"))
    assert _raw(backup, "PRAGMA user_version") == [(0,)]
    assert _raw(backup, f"SELECT {old_columns} FROM ai_runs ORDER BY id") == _V0_ROWS

    ar.transition('b', 'cancelled')  # a status the old table's CHECK would have refused
    assert ar.get_ai_run('c')['best_coilset'] == {'loop_r': 50}
    ar.migrate_if_needed()
    assert len(list((path.parent / "backups").glob("*.db"))) == 1, "an up-to-date database is not migrated again"
    print("  ok  version 0 -> 1: rows unchanged, backup written, new statuses accepted")


def test_a_migration_that_fails_half_way_changes_nothing_and_names_the_backup(tmp_path, monkeypatch):
    path = tmp_path / "old" / "femm_automation.db"
    path.parent.mkdir()
    _v0_database(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    # Fails after the new table is built and filled, before the old one is replaced.
    monkeypatch.setattr(ar, "_RUNS_INDEX", "CREATE INDEX broken ON no_such_table (x)")

    with pytest.raises(ar.MigrationError) as failure:
        ar.migrate_if_needed()

    (backup,) = list((path.parent / "backups").glob("femm_automation-v0-*.db"))
    assert str(backup) in str(failure.value)
    assert _raw(path, "PRAGMA user_version") == [(0,)]
    assert _raw(path, "SELECT COUNT(*) FROM ai_runs") == [(4,)]
    assert _raw(path, "SELECT name FROM sqlite_master WHERE name = 'ai_runs_new'") == [], "the half-built table is gone"
    assert "stop_reason" not in [c[1] for c in _raw(path, "PRAGMA table_info(ai_runs)")]

    monkeypatch.undo()  # the real index statement again
    monkeypatch.setattr(db, "DB_PATH", path)
    ar.migrate_if_needed()
    assert _raw(path, "PRAGMA user_version") == [(1,)], "the next start retries and succeeds"
    print("  ok  an interrupted migration rolls back completely")


def test_get_ai_run_missing_returns_none():
    assert ar.get_ai_run('does-not-exist') is None
    print("  ok  get_ai_run() returns None for a missing id")


def test_finalize_spec_serializes_a_real_agent_spec_with_sets():
    """Regression test: dataclasses.asdict(AgentSpec(...)) leaves
    VariableSpec's enabled_groups/excluded_fields as real Python sets,
    not lists - json.dumps() can't serialize a set directly. Found via a
    real end-to-end GUI test (frontend/tests/test_agent_panel.py), not a
    synthetic case - the exact shape finalize_spec() gets from
    agent/input_chat/services/runner.py and agent/femm_agent/services/runner.py in production."""
    record = ar.create_ai_run('Gen2_coil')
    spec = {
        'baseline_config_id': 'Gen2_coil',
        'objective_text': 'match M23 within 5%',
        'variable_spec': {
            'enabled_groups': {'tank_ferrite', 'rc_shield'},
            'excluded_fields': {'loop_r'},
        },
    }
    ar.finalize_spec(record['id'], spec)  # must not raise

    got = ar.get_ai_run(record['id'])
    assert sorted(got['spec']['variable_spec']['enabled_groups']) == ['rc_shield', 'tank_ferrite']
    assert got['spec']['variable_spec']['excluded_fields'] == ['loop_r']
    print("  ok  finalize_spec() serializes a real AgentSpec containing sets without raising")


def test_append_message_and_list_messages_replay_order():
    record = ar.create_ai_run('Gen2_coil')
    ar.append_message(record['id'], phase='interview', seq=1, role='user', content='match M23 within 5%')
    ar.append_message(record['id'], phase='interview', seq=2, role='assistant', content='what distances?')
    ar.append_message(record['id'], phase='optimize', seq=1, role='assistant',
                       round_num=1, content='proposing', payload={'tank_trace_thickness': 0.14})

    all_messages = ar.list_messages(record['id'])
    assert [m['phase'] for m in all_messages] == ['interview', 'interview', 'optimize']
    assert [m['seq'] for m in all_messages] == [1, 2, 1]

    interview_only = ar.list_messages(record['id'], phase='interview')
    assert len(interview_only) == 2
    assert interview_only[1]['content'] == 'what distances?'

    optimize_only = ar.list_messages(record['id'], phase='optimize')
    assert optimize_only[0]['round_num'] == 1
    assert optimize_only[0]['payload'] == {'tank_trace_thickness': 0.14}
    print("  ok  append_message()/list_messages() round-trip both phases in replay order")


def test_list_ai_runs_sorted_newest_first():
    r1 = ar.create_ai_run('Gen2_coil')
    r2 = ar.create_ai_run('Gen2_coil')

    runs = ar.list_ai_runs()
    ids = [r['id'] for r in runs]
    assert ids.index(r2['id']) < ids.index(r1['id']), "newer run should sort first"
    print("  ok  list_ai_runs() sorts newest first")



def test_delete_ai_run_removes_the_run_and_its_messages():
    keep = ar.create_ai_run('Gen2_coil')
    ar.append_message(keep['id'], phase='interview', seq=0, role='user', content='keep me')
    gone = ar.create_ai_run('Gen2_coil')
    ar.append_message(gone['id'], phase='interview', seq=0, role='user', content='discard me')

    ar.delete_ai_run(gone['id'])
    ar.delete_ai_run('does-not-exist')  # no-op, no error

    assert ar.get_ai_run(gone['id']) is None
    assert ar.list_messages(gone['id']) == []
    assert ar.get_ai_run(keep['id']) is not None
    assert len(ar.list_messages(keep['id'])) == 1
    print("  ok  delete_ai_run() removes one run and only its messages")


if __name__ == '__main__':
    print("checking the AI-run history store")
    test_create_ai_run_starts_interviewing()
    test_finalize_spec_moves_to_running()
    test_update_run_status_only_overwrites_passed_fields()
    test_get_ai_run_missing_returns_none()
    test_finalize_spec_serializes_a_real_agent_spec_with_sets()
    test_append_message_and_list_messages_replay_order()
    test_list_ai_runs_sorted_newest_first()
    test_delete_ai_run_removes_the_run_and_its_messages()
    print("all ai_run_repository checks passed")
