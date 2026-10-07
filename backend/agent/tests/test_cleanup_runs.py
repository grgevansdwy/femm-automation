# =============================================================================
# test_cleanup_runs.py
#
# The one-time cleanup of old ai_runs rows (agent/cleanup_runs.py): a dry run
# changes nothing; --apply follows its three rules and removes nothing that
# has content.
#     python3 -m pytest agent/tests/test_cleanup_runs.py  (run from backend/)
# =============================================================================
from agent import cleanup_runs
from agent.repositories import ai_run_repository as ar
from engine.repositories import db


def _row(status, user_message=False, rounds=0, plan=True):
    run_id = ar.create_ai_run("base_coil")["id"]
    if user_message:
        ar.append_message(run_id, phase="interview", seq=0, role="user", content="cut tank ESR")
    if status != "interviewing":
        ar.finalize_spec(run_id, {"distances": [5.0]} if plan else None)
        if status != "plan_ready":
            ar.transition(run_id, "running")
            if status != "running":
                ar.transition(run_id, status)
    for n in range(1, rounds + 1):
        ar.append_message(run_id, phase="optimize", seq=n, role="assistant", round_num=n, payload={"candidates": []})
    return run_id


def _fixture():
    return {
        "opened_and_closed": _row("interviewing"),
        "real_interview": _row("interviewing", user_message=True),
        "plan_never_started": _row("running", user_message=True),
        "plan_reaped_at_startup": _row("interrupted", user_message=True),
        "search_cut_off": _row("running", user_message=True, rounds=2),
        "headless_search_cut_off": _row("running", rounds=1),
        "finished": _row("goal_met", user_message=True, rounds=3),
        "interrupted_with_rounds": _row("interrupted", user_message=True, rounds=1),
    }


def _statuses(ids):
    return {name: (ar.get_ai_run(run_id) or {}).get("status") for name, run_id in ids.items()}


def test_a_dry_run_prints_the_plan_and_changes_nothing(capsys):
    ids = _fixture()
    before = _statuses(ids)

    assert cleanup_runs.main([]) == 0

    assert _statuses(ids) == before
    out = capsys.readouterr().out
    assert "1 to delete, 2 to plan_ready, 2 to interrupted" in out and "Dry run" in out
    assert not (db.DB_PATH.parent / "backups").exists()
    print("  ok  cleanup_runs without --apply only reports")


def test_apply_follows_the_three_rules_and_keeps_everything_with_content(capsys):
    ids = _fixture()
    messages_before = {name: len(ar.list_messages(run_id)) for name, run_id in ids.items()}

    assert cleanup_runs.main(["--apply"]) == 0

    assert _statuses(ids) == {
        "opened_and_closed": None,                 # rule 1: no user message -> deleted
        "real_interview": "interviewing",
        "plan_never_started": "plan_ready",        # rule 2
        "plan_reaped_at_startup": "plan_ready",    # rule 2, after the startup reaper got there first
        "search_cut_off": "interrupted",           # rule 3
        "headless_search_cut_off": "interrupted",  # no user message, but it has rounds: kept
        "finished": "goal_met",
        "interrupted_with_rounds": "interrupted",
    }
    for name, run_id in ids.items():
        if name != "opened_and_closed":
            assert len(ar.list_messages(run_id)) == messages_before[name], "no message is removed from a kept run"
    assert len(list((db.DB_PATH.parent / "backups").glob("*-before-cleanup-*.db"))) == 1
    assert not [r for r in ar.list_ai_runs() if r["status"] == "running"]

    capsys.readouterr()
    cleanup_runs.main(["--apply"])
    assert "0 to delete, 0 to plan_ready, 0 to interrupted" in capsys.readouterr().out, "running it twice is harmless"
    print("  ok  cleanup_runs --apply: delete empty rows, un-start unstarted plans, interrupt cut-off searches")
