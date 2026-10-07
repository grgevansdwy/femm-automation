# =============================================================================
# test_report.py
#
# Checks agent/report.py (the command-line run report) on a small database and
# log built here: the per-run numbers, the tables, a run that has no log, and
# that the database is only read. No LLM, no FEMM.
#     python3 -m pytest agent/tests/test_report.py  (run from backend/)
# =============================================================================
import pytest

from agent import report
from agent.repositories.ai_run_repository import append_message, create_ai_run, finalize_spec, update_run_status
from agent.services import run_log
from agent.services.run_log import RunLog
from engine.repositories import db


def _objective(change, tolerance=10.0, kind="reduce", required=True):
    return {"metric": "tank_esr", "kind": kind, "required": required, "tolerance_pct": tolerance,
            "worst_change_pct": change}


def _candidate(delta, goal_met=False, valid=True):
    return {"delta": delta, "valid": valid, "error": None if valid else "bad",
            "tool_result": {"goal_met": goal_met} if valid else None}


def _finished_run(with_log=True) -> str:
    run_id = create_ai_run("base_coil")["id"]
    finalize_spec(run_id, {"objective_text": "cut tank ESR"})
    rounds = [
        [_candidate({"tank_center_r": 71.0}), _candidate({"bogus": 1}, valid=False)],
        [_candidate({"tank_center_r": 71.0}), _candidate({"tank_center_r": 60.0}, goal_met=True)],
    ]
    for n, candidates in enumerate(rounds, start=1):
        append_message(run_id, phase="optimize", seq=n, role="assistant", round_num=n, content="",
                       payload={"candidates": candidates, "llm_seconds": 30.0, "femm_seconds": 10.0,
                                "llm_stats": {"tokens": 100}})
    update_run_status(run_id, status="goal_met", finished_at="2099-01-01T00:00:00+00:00", best_round=2,
                      best_tool_result={"goal_met": True, "objectives": [
                          _objective(-14.0), _objective(-20.0), _objective(3.0, kind="match")]})
    if with_log:
        log = RunLog(run_id)
        log.run_start(segment=1, tools=[])
        for n in (1, 2):
            log.bind(phase="search", round=n, search_attempt=1, ai_try=1).llm_call(
                messages=[{"role": "user", "content": "go"}], reply={}, model="m", max_tokens=100,
                sampling={"temperature": 1.0, "top_p": 0.95, "seed": -1}, tools_sha256=None,
                prompt_tokens=1000 * n, reply_tokens=50, chars_in=10, elapsed_s=30.0)
        log.warning("AgentRunner._save_rounds", "could not save a finished round")
        log.run_end(status="goal_met", stop_reason=None, rounds=2, simulations=3, cache_hits=1, seconds=80.0)
    return run_id


def _summary(run_id):
    ((run, rounds),) = report._read_db(db.DB_PATH, run_id=run_id)
    return report.summarize_run(run, rounds, report._read_log(run_log.LOG_DIR, run_id))


def test_one_runs_numbers_come_from_the_database_and_its_log():
    s = _summary(_finished_run())
    assert (s["status"], s["rounds"], s["candidates"], s["rejected"]) == ("goal_met", 2, 4, 1)
    assert (s["simulations"], s["cache_hits"], s["first_pass_round"], s["repeated"]) == (3, 1, 2, 1)
    assert s["ai_share"] == pytest.approx(0.75)
    assert (s["prompt_tokens"], s["reply_tokens"], s["llm_calls"], s["llm_errors"], s["warnings"]) == (3000, 100, 2, 0, 1)
    # points past the bar: 4 and 10 (the match objective has none) -> 0.5 * 4 + 0.5 * 7
    assert s["best_score"] == pytest.approx(5.5)


def test_a_run_without_a_log_still_gets_a_row():
    s = _summary(_finished_run(with_log=False))
    assert (s["rounds"], s["candidates"], s["first_pass_round"]) == (2, 4, 2)
    assert s["reply_tokens"] == 200, "falls back to the reply tokens stored with each round"
    assert s["simulations"] is s["prompt_tokens"] is s["stop_reason"] is None and not s["has_log"]


def test_the_command_prints_both_tables_and_one_run_in_detail(capsys):
    logged, unlogged = _finished_run(), _finished_run(with_log=False)
    create_ai_run("base_coil")  # still interviewing: no rounds, no spec

    assert report.main([]) == 0
    out = capsys.readouterr().out
    assert logged[:8] in out and unlogged[:8] in out and "interviewing" in out
    assert "Rejected share" in out and "Best score" in out

    assert report.main(["--run", logged[:10]]) == 0
    out = capsys.readouterr().out
    assert "AI requests (from the log)" in out and "AgentRunner._save_rounds" in out

    assert report.main(["--run", unlogged]) == 0
    assert "No log file for this run" in capsys.readouterr().out

    assert report.main(["--since", "2999-01-01"]) == 0
    assert "No agent runs" in capsys.readouterr().out
    assert report.main(["--run", "no-such-run"]) == 1
    assert report.main(["--request", "1"]) == 1, "--request needs --run"


def test_request_prints_the_rebuilt_body_or_says_why_not(capsys):
    run_id = _finished_run()
    assert report.main(["--run", run_id, "--request", "2"]) == 0
    assert capsys.readouterr().out.startswith('{"model": ')
    assert report.main(["--run", run_id, "--request", "7"]) == 1
    assert "no search request for round 7" in capsys.readouterr().err


def test_the_report_never_writes_to_the_database(tmp_path):
    _finished_run()
    before = db.DB_PATH.read_bytes()
    report.main([])
    assert db.DB_PATH.read_bytes() == before
    missing = tmp_path / "nowhere.db"
    assert report.main(["--db", str(missing)]) == 0 and not missing.exists()
