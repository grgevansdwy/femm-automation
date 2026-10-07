# =============================================================================
# test_agent_retry.py
#
# Checks the search's failure handling end to end through AgentRunner, with a
# scripted fake LLM (no server) and a fake job runner (no FEMM):
# - a round's AI request is retried once before the round fails,
# - a failed round restarts the whole search once, from zero memory,
# - the end-of-run report says why the goal wasn't met (AI server cause,
#   budget used up, internal error), and History gets the same report.
#     python3 -m pytest agent/femm_agent/tests/test_agent_retry.py  (run from backend/)
# =============================================================================
import dataclasses
import time

import pytest

from agent.femm_agent.services import optimize
from agent.services.llm_client import LLMError
from agent.input_chat.models.objective import Objective
from agent.femm_agent.services.runner import AgentRunner
from agent.input_chat.models.spec import AgentSpec
from agent.repositories.ai_run_repository import get_ai_run, list_messages
from agent.services.run_log import read_log

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": 0.6, "k13": 0.2, "k23": 0.6}


class _Job:
    status = "done"
    error = None

    def __init__(self, result):
        self.result, self.id = result, "job"


class _Jobs:
    """Tank ESR scales with tank_center_r, so a smaller tank is better."""

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        return _Job({**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / 72.688})

    def cancel(self, job_id):
        return False


def _propose(r):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{r}", "type": "function", "function": {"name": "propose_coilset",
                                                         "arguments": {"tank_center_r": r, "rationale": "smaller"}}}]}


def _timeout():
    return LLMError("no_response", "", elapsed_s=600.0, time_limit_s=600.0)


def _spec(rounds=3, cut_pct=10.0):
    return dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=rounds,
                               candidates_per_round=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=cut_pct)])


def _run(monkeypatch, script, spec):
    """script: one entry per AI request, in order - a reply dict or an
    exception to raise. Returns (final state, every request's messages)."""
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        step = script[len(sent) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    monkeypatch.setattr(optimize, "RETRY_PAUSE_S", 0.0)
    runner = AgentRunner(_Jobs())
    state = runner.submit(spec)
    deadline = time.monotonic() + 20
    while (get_ai_run(state.id) or {}).get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return state, sent


def test_one_failed_ai_try_is_retried_within_the_round(monkeypatch):
    state, sent = _run(monkeypatch, [_propose(71.0), _timeout(), _propose(60.0)], _spec())

    assert state.status.value == "goal_met" and state.search_attempt == 1, "no restart needed"
    assert len(state.rounds) == 2
    assert [f["ai_try"] for f in state.rounds[1].llm_failures] == [1]
    assert sent[1] == sent[2], "the retry resends the same request"
    assert state.outcome_report is None
    print("  ok  a single failed AI try is retried in the same round")


def test_a_failed_round_restarts_the_search_from_zero_memory(monkeypatch):
    # Attempt 1: round 1 proposes, round 2 fails both tries -> restart.
    # Attempt 2: round 1 meets the goal.
    state, sent = _run(monkeypatch, [_propose(71.0), _timeout(), _timeout(), _propose(60.0)], _spec())

    assert state.status.value == "goal_met" and state.search_attempt == 2
    assert len(sent[1]) > 2, "attempt 1's second round carried memory of round 1"
    assert len(sent[3]) == 2, "attempt 2 starts from zero: just the system prompt and the task"
    assert [(r.round_num, r.search_attempt, r.attempt_round) for r in state.rounds] == [(1, 1, 1), (2, 1, 2), (3, 2, 1)]
    assert len(state.rounds[1].llm_failures) == 2 and not state.rounds[1].candidates
    assert state.best.coilset.tank_center_r == 60.0

    saved = [m["payload"] for m in list_messages(state.id, phase="optimize")]
    assert [p["search_attempt"] for p in saved] == [1, 1, 2]
    assert [f["kind"] for f in saved[1]["llm_failures"]] == ["no_response", "no_response"]
    assert get_ai_run(state.id)["best_round"] == 3, "the round that actually produced the best candidate"
    print("  ok  a round failing every try restarts the search once, from zero memory")


def test_failing_again_after_the_restart_reports_why(monkeypatch):
    too_slow = LLMError("too_slow", "", elapsed_s=600.0, tokens=9000, time_limit_s=600.0)
    state, _ = _run(monkeypatch, [_propose(71.0), _timeout(), too_slow, _timeout(), _timeout()], _spec())

    assert state.status.value == "failed" and state.search_attempt == 2
    report = state.outcome_report
    assert report.startswith("Search failed: the AI server sent nothing back within 600 s")
    assert "restarted from zero and failed again" in report
    assert "attempt 1, round 2, try 2: The AI was still generating" in report
    assert "attempt 2, round 3, try 1: The AI server sent nothing back" in report
    assert "Causes: no_response x3, too_slow x1." in report
    assert "Closest candidate (round 1) still missed: tank_esr" in report
    assert get_ai_run(state.id)["error"] == report, "History shows the same report"
    print("  ok  a search that fails after its restart ends with a report of the causes")


def test_budget_exhausted_reports_the_closest_miss(monkeypatch):
    state, _ = _run(monkeypatch, [_propose(71.0), _propose(70.0)], _spec(rounds=2, cut_pct=50.0))

    assert state.status.value == "budget_exhausted"
    report = state.outcome_report
    assert report.startswith("Goal not met: used every round (2 per attempt)")
    assert "Closest candidate (round 2) still missed: tank_esr -3.7% (needs >= 50% lower)" in report
    assert get_ai_run(state.id)["error"] == report
    print("  ok  running out of rounds reports the closest candidate and what it missed")


def test_an_internal_error_is_not_retried(monkeypatch):
    state, sent = _run(monkeypatch, [_propose(71.0), ValueError("bug in the loop")], _spec())

    assert state.status.value == "failed" and state.search_attempt == 1 and len(sent) == 2
    assert state.outcome_report.startswith("Failed with an internal error (a bug in this app, not the AI server)")


def test_restart_during_the_retry_pause_discards_the_run(monkeypatch):
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        raise _timeout()

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    monkeypatch.setattr(optimize, "RETRY_PAUSE_S", 30.0)
    runner = AgentRunner(_Jobs())
    state = runner.submit(_spec())
    while not sent:
        time.sleep(0.01)
    runner.cancel(state.id)
    deadline = time.monotonic() + 3
    while state.id in runner.runs and time.monotonic() < deadline:
        time.sleep(0.02)
    assert state.id not in runner.runs, "the pause ends as soon as the run is cancelled"
    assert len(sent) == 1 and list_messages(state.id, phase="optimize") == []


# --- rounds are saved as they finish, not only at the end -----------------------------------------

def _seqs(run_id):
    return [m["seq"] for m in list_messages(run_id, phase="optimize")]


def test_each_round_is_saved_as_soon_as_it_finishes(monkeypatch):
    from agent.repositories.ai_run_repository import create_ai_run
    from agent.femm_agent.models.state import AgentRunState
    from engine.repositories.coilset_repository import get_coilset

    state = AgentRunState(id=create_ai_run("base_coil")["id"], spec=_spec(rounds=3, cut_pct=50.0),
                          baseline_coilset=get_coilset("base_coil"))
    saved_when_asked = []
    replies = iter([_propose(71.0), _propose(70.0), _propose(69.0)])

    def fake_call_llm(messages, tools, **kwargs):
        saved_when_asked.append(_seqs(state.id))  # what's in the database as each round's request goes out
        return next(replies)

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _Jobs(), on_round_done=AgentRunner._save_rounds)

    assert saved_when_asked == [[], [1], [1, 2]], "round N is already in the database when round N+1 starts"
    assert _seqs(state.id) == [1, 2, 3] and state.rounds_persisted == 3


def test_the_final_save_writes_only_what_is_still_missing(monkeypatch):
    state, _ = _run(monkeypatch, [_propose(71.0), _propose(70.0)], _spec(rounds=2, cut_pct=50.0))
    assert _seqs(state.id) == [1, 2], "each round exactly once, however many times it was offered"
    AgentRunner._write_rounds(state, only_finished=False)  # a second full save must add nothing
    assert _seqs(state.id) == [1, 2]


def test_a_database_error_while_saving_a_round_does_not_stop_the_search(monkeypatch):
    from agent.repositories import ai_run_repository as repo

    real, calls = repo.append_message, []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "append_message", flaky)
    state, _ = _run(monkeypatch, [_propose(71.0), _propose(70.0)], _spec(rounds=2, cut_pct=50.0))

    assert state.status.value == "budget_exhausted", "the failed save did not fail the run"
    assert _seqs(state.id) == [1, 2], "the end-of-run save picked up the round the hook missed"


def test_nothing_is_saved_for_a_discarded_run_even_if_restart_lands_mid_write(monkeypatch):
    from agent.repositories import ai_run_repository as repo
    from agent.repositories.ai_run_repository import create_ai_run
    from agent.femm_agent.models.state import AgentRunState, RoundState, RoundStatus
    from engine.repositories.coilset_repository import get_coilset

    def fresh():
        s = AgentRunState(id=create_ai_run("base_coil")["id"], spec=_spec(), baseline_coilset=get_coilset("base_coil"))
        s.rounds.append(RoundState(round_num=1, status=RoundStatus.DONE))
        return s

    cancelled = fresh()
    cancelled.cancelled = True
    AgentRunner._save_rounds(cancelled)
    assert _seqs(cancelled.id) == [], "a run already discarded writes nothing"

    # Restart arrives after the cancel check but before/while the row is written: the row lands,
    # then the writer notices and deletes the run - no orphaned messages, no leftover run row.
    racing, real = fresh(), repo.append_message

    def write_then_cancel(*args, **kwargs):
        row = real(*args, **kwargs)
        racing.cancelled = True
        return row

    monkeypatch.setattr(repo, "append_message", write_then_cancel)
    AgentRunner._save_rounds(racing)
    assert _seqs(racing.id) == [] and get_ai_run(racing.id) is None


# --- the run log (agent/services/run_log.py) ---------------------------------

def _run_logged(monkeypatch, server_script, spec, jobs=None):
    """Like _run(), but through the real call_llm against a scripted transport,
    so the requests are really logged. server_script: one entry per request -
    a reply dict or an LLMError."""
    from agent.services import llm_client
    served = []

    def fake_request(stream, body, settings, time_limit_s, max_tokens, should_abort, on_update):
        served.append(body)
        step = server_script[len(served) - 1]
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(llm_client, "_request", fake_request)
    monkeypatch.setattr(optimize, "RETRY_PAUSE_S", 0.0)
    runner = AgentRunner(jobs or _Jobs())
    state = runner.submit(spec)
    deadline = time.monotonic() + 20
    while (get_ai_run(state.id) or {}).get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return state, served


def test_a_search_leaves_a_log_of_every_request_from_start_to_end(monkeypatch):
    state, served = _run_logged(monkeypatch, [_propose(71.0), _timeout(), _propose(60.0)], _spec())
    assert state.status.value == "goal_met"

    events = read_log(state.log.path)
    assert [e["event"] for e in events] == ["run_start", "llm_call", "llm_call", "llm_call", "run_end"]
    start, calls, end = events[0], events[1:4], events[4]
    assert start["run_id"] == state.id and start["segment"] == 1
    assert start["spec"]["baseline_config_id"] == "base_coil" and start["settings"]["temperature"] == 1.0
    assert start["baseline_coilset"]["tank_center_r"] == 72.688
    assert start["tools"][0]["function"]["name"] == "propose_coilset"

    assert [(c["phase"], c["round"], c["search_attempt"], c["ai_try"]) for c in calls] == \
        [("search", 1, 1, 1), ("search", 2, 1, 1), ("search", 2, 1, 2)]
    assert "reply" in calls[0] and calls[1]["error"]["kind"] == "no_response" and "reply" in calls[2]
    assert calls[1]["messages"] == calls[2]["messages"], "the retry resends the same request"
    assert len({c["prompt_sha256"] for c in calls}) == 1 and calls[0]["tools_sha256"]

    # N5: every round's request can be rebuilt from the log alone, byte for byte.
    from agent import report
    assert report.rebuild_request(events, 1) == served[0]
    assert report.rebuild_request(events, 2, ai_try=1) == served[1]
    assert report.rebuild_request(events, 2) == served[2]

    assert (end["status"], end["stop_reason"], end["rounds"]) == ("goal_met", None, 2)
    # One distance: the baseline and two candidates, none cached by this fake.
    assert (end["simulations"], end["cache_hits"], end["llm_failures"]) == (3, 0, 1)
    print("  ok  a search logs run_start, every AI request (failed tries too) and run_end")


def test_a_round_that_cannot_be_saved_is_logged_as_a_warning_and_the_search_carries_on(monkeypatch):
    from agent.repositories import ai_run_repository
    real_append, failed = ai_run_repository.append_message, []

    def flaky_append(*args, **kwargs):
        if not failed:
            failed.append(1)
            raise RuntimeError("database is locked")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(ai_run_repository, "append_message", flaky_append)
    state, _ = _run_logged(monkeypatch, [_propose(71.0), _propose(60.0)], _spec())

    assert state.status.value == "goal_met"
    assert len(list_messages(state.id, phase="optimize")) == 2, "the end-of-run save wrote what the hook missed"
    warnings = [e for e in read_log(state.log.path) if e["event"] == "warning"]
    assert [w["where"] for w in warnings] == ["AgentRunner._save_rounds"]
    assert "RuntimeError: database is locked" in warnings[0]["exception"]
    print("  ok  a database error while saving a round is logged, not swallowed silently")


def test_a_failed_solve_puts_the_full_exception_in_the_log_not_just_its_message(monkeypatch):
    class _FailingJobs(_Jobs):
        def submit(self, coilset, config_id, config_name, distance):
            if coilset.tank_center_r == 71.0:
                raise RuntimeError("femmcli crashed")
            return super().submit(coilset, config_id, config_name, distance)

    state, _ = _run_logged(monkeypatch, [_propose(71.0), _propose(60.0)], _spec(), jobs=_FailingJobs())

    assert state.rounds[0].candidates[0].error == "femmcli crashed"
    (warning,) = [e for e in read_log(state.log.path) if e["event"] == "warning"]
    assert warning["where"] == "optimize_loop.launch" and warning["round"] == 1
    assert "Traceback" in warning["exception"] and "femmcli crashed" in warning["exception"]
