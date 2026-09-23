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
from agent.repositories import ai_run_repository as ar


def test_create_ai_run_starts_interviewing():
    record = ar.create_ai_run('Gen2_coil')
    assert record['status'] == 'interviewing'
    assert record['spec'] is None
    assert ar.get_ai_run(record['id']) == record
    print("  ok  create_ai_run() starts a run in 'interviewing' with no spec yet")


def test_finalize_spec_moves_to_running():
    record = ar.create_ai_run('Gen2_coil')
    spec = {'objective_text': 'match M23 within 5%', 'distances': [3.0, 5.0]}
    ar.finalize_spec(record['id'], spec)

    updated = ar.get_ai_run(record['id'])
    assert updated['status'] == 'running'
    assert updated['spec'] == spec
    assert updated['started_at'] is not None
    print("  ok  finalize_spec() stores the spec and moves the run to 'running'")


def test_update_run_status_only_overwrites_passed_fields():
    record = ar.create_ai_run('Gen2_coil')
    ar.update_run_status(record['id'], 'running', best_round=3, best_coilset={'loop_r': 50})
    ar.update_run_status(record['id'], 'goal_met', finished_at='2026-09-10T00:00:00+00:00')

    updated = ar.get_ai_run(record['id'])
    assert updated['status'] == 'goal_met'
    assert updated['finished_at'] == '2026-09-10T00:00:00+00:00'
    assert updated['best_round'] == 3, "best_round from the earlier call should survive"
    assert updated['best_coilset'] == {'loop_r': 50}, "best_coilset from the earlier call should survive"
    print("  ok  update_run_status() leaves unpassed fields untouched")


def test_get_ai_run_missing_returns_none():
    assert ar.get_ai_run('does-not-exist') is None
    print("  ok  get_ai_run() returns None for a missing id")


def test_finalize_spec_serializes_a_real_agent_spec_with_sets():
    """Regression test: dataclasses.asdict(AgentSpec(...)) leaves
    VariableSpec's enabled_groups/excluded_fields as real Python sets,
    not lists - json.dumps() can't serialize a set directly. Found via a
    real end-to-end GUI test (frontend/tests/test_agent_panel.py), not a
    synthetic case - the exact shape finalize_spec() gets from
    agent/services/gui_bridge.py and agent/services/runner.py in production."""
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
