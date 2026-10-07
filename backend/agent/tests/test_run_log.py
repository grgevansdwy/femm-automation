# =============================================================================
# test_run_log.py
#
# Checks agent/services/run_log.py: one JSON object per line, the four event
# types, bind() context, and that a log that can't be written never raises.
#     python3 -m pytest agent/tests/test_run_log.py  (run from backend/)
# =============================================================================
import json
from datetime import datetime

from agent.services import run_log
from agent.services.run_log import RunLog, read_log


def test_each_event_is_one_json_line_in_the_runs_own_file():
    log = RunLog("abc")
    assert not log.path.exists(), "the file is only created by the first write"
    log.run_start(segment=1, git_commit="deadbeef", settings={"model": "m"}, spec={}, baseline_coilset={}, tools=[])
    log.bind(phase="search", round=2, search_attempt=1, ai_try=1).llm_call(messages=[{"role": "user"}], reply={})
    log.run_end(status="goal_met", stop_reason=None, rounds=2)

    assert log.path == run_log.LOG_DIR / "abc.jsonl"
    lines = log.path.read_text().splitlines()
    assert len(lines) == 3 and all(isinstance(json.loads(line), dict) for line in lines)
    events = read_log(log.path)
    assert [e["event"] for e in events] == ["run_start", "llm_call", "run_end"]
    assert all(e["run_id"] == "abc" and e["ts"] for e in events)
    assert (events[1]["phase"], events[1]["round"]) == ("search", 2)
    assert "phase" not in events[2], "bind() context belongs to the bound view, not the run's log"


def test_a_warning_keeps_the_full_exception_text():
    log = RunLog("abc")
    try:
        raise RuntimeError("database is locked")
    except RuntimeError as exc:
        log.bind(phase="search", round=4).warning("AgentRunner._save_rounds", "could not save a finished round", exc)
    log.warning("somewhere", "no exception here")

    first, second = read_log(log.path)
    assert (first["where"], first["round"]) == ("AgentRunner._save_rounds", 4)
    assert "RuntimeError: database is locked" in first["exception"] and "Traceback" in first["exception"]
    assert second["exception"] is None


def test_bound_views_share_one_file():
    log = RunLog("abc")
    log.bind(phase="interview").llm_call(n=1)
    log.bind(phase="search", round=1).bind(ai_try=2).llm_call(n=2)
    a, b = read_log(log.path)
    assert a["phase"] == "interview" and (b["phase"], b["round"], b["ai_try"]) == ("search", 1, 2)


def test_an_unwritable_folder_never_raises_and_is_reported_once(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "a_file_not_a_folder"
    blocker.write_text("")
    monkeypatch.setattr(run_log, "LOG_DIR", blocker / "logs")
    log = RunLog("abc")
    log.run_start(segment=1)
    log.bind(phase="search").llm_call(n=1)
    log.warning("x", "y")
    assert capsys.readouterr().err.count("could not write") == 1


def test_values_json_cannot_hold_are_written_as_text_not_dropped():
    log = RunLog("abc")
    log.run_start(when=datetime(2026, 10, 7), where=run_log.LOG_DIR)
    event = read_log(log.path)[0]
    assert event["when"].startswith("2026-10-07") and event["where"] == str(run_log.LOG_DIR)


def test_a_line_cut_short_by_a_crash_is_skipped_when_reading():
    log = RunLog("abc")
    log.run_start(segment=1)
    with log.path.open("a") as f:
        f.write('{"event": "llm_call", "mess')
    assert [e["event"] for e in read_log(log.path)] == ["run_start"]
