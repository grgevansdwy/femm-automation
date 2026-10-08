"""Exercises AgentPanelState directly - the same calls the AI Agent mode's
chat/progress panels make, without needing to click a real window. Same
"real backend, no mocks" convention as test_runs_panel.py: a real interview
turn against the real LLM server, a real handoff into AgentRunner, real
FEMM solves via the shared JobRunner.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.services.job_runner import JobRunner
from frontend.screen_agent.agent_panel import AgentPanelState, _finished_status_text, _message_display_text


def _poll_until(predicate, timeout=300, interval=0.1):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() > deadline:
            raise TimeoutError("condition not met before timeout")
        time.sleep(interval)


def test_interview_hands_off_to_a_real_optimize_run_only_after_confirmation():
    state = AgentPanelState(JobRunner())

    # Picking a config opens the chat immediately - no separate Start
    # click, and no opening LLM call (the model would otherwise reply with
    # a long list of clarifying questions before the user has said anything).
    state.select_config("base_coil")
    assert state.interview_state is not None
    assert not state.interview_state.transcript, "no opening turn should fire on its own"
    assert not state.interview_runner.busy

    state.chat_input = (
        "Match tank-to-RC mutual inductance within 5% of baseline, and reduce ESR on all "
        "three coils by at least 10% each. Evaluate at 5 and 10mm. Keep ferrite and shields "
        "exactly as they already are on this baseline - don't change them."
    )
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy)

    if not state.interview_state.finalized:
        # A second reply in case the model asked one more clarifying question.
        state.chat_input = "That's everything - please propose the plan now."
        state.send_chat()
        _poll_until(lambda: not state.interview_runner.busy)

    assert state.interview_state.finalized, state.interview_state.transcript
    assert state.interview_state.spec is not None

    # A finalized plan alone must NOT start the search - poll() is what
    # main.py calls every frame, and it must stay a no-op until the human
    # explicitly confirms.
    for _ in range(5):
        state.poll()
    assert state.run_state is None, "the search must not start before explicit confirmation"
    assert not state.plan_confirmed

    state.confirm_plan()
    state.poll()
    assert state.run_state is not None
    assert state.run_state.id == state.interview_runner.ai_run_id, "both phases must share one ai_run id"

    # 300s was too tight - a real diagnostic run (see docs/gui_agent_plan.md)
    # converged correctly in 281s with individual rounds ranging 23-67s,
    # confirming this is genuine LLM latency variance, not a stall. 600s
    # gives real headroom without masking an actual hang.
    _poll_until(lambda: state.run_state.status.value != "running", timeout=600)
    assert len(state.run_state.rounds) >= 1

    # The whole session (interview + optimize) must be persisted under that
    # one ai_run id, replayable from the history repository agent_history.py
    # reads. AgentRunner._persist() runs in the same thread's `finally`
    # right after status flips in memory, but isn't instantaneous (writes
    # one row per round plus the final status) - poll for it to actually
    # land rather than assuming the DB write races won.
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    _poll_until(lambda: (get_ai_run(state.run_state.id) or {}).get("status") == state.run_state.status.value, timeout=30)
    record = get_ai_run(state.run_state.id)
    assert record is not None
    assert record["spec"]["baseline_config_id"] == "base_coil"

    messages = list_messages(state.run_state.id)
    phases = {m["phase"] for m in messages}
    assert "interview" in phases
    assert "optimize" in phases

    # The chat stays open after the search: a follow-up question gets a real
    # answer, persisted under the same run - and must not flip the finished
    # run's status back to 'running' (finalize_spec() would).
    finished_status = state.run_state.status.value
    before = len(state.interview_state.transcript)
    state.chat_input = "How many rounds did the search take, and did it meet the goal?"
    state.send_chat()
    assert state.interview_runner.busy
    _poll_until(lambda: not state.interview_runner.busy)
    assert state.interview_state.error is None, state.interview_state.error
    assert state.interview_state.transcript[-1]["role"] == "assistant"
    assert state.interview_state.transcript[-1]["content"]
    assert len(state.interview_state.transcript) == before + 2
    assert state.interview_state.spec is not None and state.plan_confirmed
    assert get_ai_run(state.run_state.id)["status"] == finished_status
    assert any(m["content"] == state.interview_state.transcript[-2]["content"]
               for m in list_messages(state.run_state.id, phase="interview"))

    # Restart on a finished run keeps it in History and opens a fresh chat.
    finished_id = state.run_state.id
    state.restart()
    assert state.phase == "interview" and state.run_state is None
    assert state.interview_runner.ai_run_id != finished_id
    assert get_ai_run(finished_id)["status"] == finished_status


def test_switching_baseline_asks_first_only_when_there_is_a_chat_to_lose():
    """No LLM needed - the transcript is filled in by hand."""
    from agent.repositories.ai_run_repository import get_ai_run
    from engine.repositories.coilset_repository import restore_config, get_config

    other = {**get_config("base_coil"), "id": "other_coil", "name": "other_coil"}
    restore_config(other)

    state = AgentPanelState(JobRunner())
    state.select_config("base_coil")
    assert state.request_baseline("base_coil") is False, "re-picking the same baseline does nothing"

    # Empty chat: switches straight away.
    assert state.request_baseline("other_coil") is False
    assert state.selected_config_id == "other_coil"

    # A chat with messages: only records the pick until confirmed.
    first_id = state.interview_runner.ai_run_id
    state.interview_state.transcript.append({"role": "user", "content": "cut tank ESR"})
    assert state.request_baseline("base_coil") is True
    assert state.selected_config_id == "other_coil" and state.pending_baseline_id == "base_coil"
    state.cancel_baseline_change()
    assert state.pending_baseline_id is None and state.interview_state.transcript

    assert state.request_baseline("base_coil") is True
    state.confirm_baseline_change()
    assert state.selected_config_id == "base_coil" and not state.interview_state.transcript
    assert get_ai_run(first_id) is None, "the abandoned planning chat isn't kept"

    # An unsent draft counts as something to lose too.
    state.chat_input = "half-typed objective"
    assert state.request_baseline("other_coil") is True
    state.confirm_baseline_change()
    assert state.selected_config_id == "other_coil" and state.chat_input == ""


# --- run lifecycle, Stop and Continue: scripted AI and an instant solver, no server needed ---------

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": 0.6, "k13": 0.2, "k23": 0.6}


class _InstantSolver:
    """Every solve is done the moment it is queued. Tank ESR scales with tank_center_r."""

    def __init__(self):
        self.submitted = 0

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        from frontend.services.job_runner import JobStatus

        class _Job:
            status, error, id = JobStatus.DONE, None, f"job{self.submitted}"
            result = {**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / 72.688}
        self.submitted += 1
        return _Job()

    def cancel(self, job_id):
        return False


def _plan_reply():
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "p1", "type": "function", "function": {
        "name": "finalize_spec", "arguments": {
            "objective_text": "cut tank ESR in half", "distances": [5.0],
            "objectives": [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 50.0}]}}}]}


def _proposal(n):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}", "type": "function", "function": {
        "name": "propose_coilset", "arguments": {"tank_center_r": 72.0 - n, "rationale": "smaller tank"}}}]}


def _planned_state(monkeypatch, search_reply, max_rounds=2):
    """A panel whose user asked for a goal and got a plan (scripted interview
    AI), ready for Start Search. `search_reply(n, kwargs)` scripts the search AI."""
    from agent.femm_agent.services import optimize
    from agent.input_chat.services import interview
    from agent.settings.settings import AgentSettings, save_settings

    save_settings(AgentSettings(max_tool_calls=max_rounds, candidates_per_round=1))
    monkeypatch.setattr(interview, "call_llm", lambda messages, tools, **kwargs: _plan_reply())
    calls = []

    def search_ai(messages, tools, **kwargs):
        calls.append(messages)
        return search_reply(len(calls), kwargs)

    monkeypatch.setattr(optimize, "call_llm", search_ai)
    state = AgentPanelState(_InstantSolver())
    state.select_config("base_coil")
    state.chat_input = "Cut tank ESR in half at 5 mm."
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    assert state.interview_state.finalized, state.interview_state.error
    state.poll()
    return state, calls


def _start(state):
    state.confirm_plan()
    state.poll()
    assert state.phase in ("running", "finished") and state.run_state is not None
    return state.run_state


def _finished(state):
    from agent.repositories.ai_run_repository import get_ai_run
    _poll_until(lambda: state.phase == "finished"
                and get_ai_run(state.run_state.id)["status"] == state.run_state.status.value, timeout=20)
    return get_ai_run(state.run_state.id)


def _wait_for_abort(n, kwargs):
    """A search AI that never answers: the request hangs until the search is stopped."""
    from agent.services.llm_client import LLMError
    deadline = time.time() + 20
    while not kwargs["should_abort"]() and time.time() < deadline:
        time.sleep(0.01)
    raise LLMError("aborted", "", elapsed_s=0.0)


def test_no_run_is_saved_until_the_first_message_and_a_closed_chat_is_kept_as_cancelled(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_ai_runs, list_messages

    empty = AgentPanelState(_InstantSolver())
    empty.select_config("base_coil")
    empty.restart()
    empty.select_config("base_coil")
    assert list_ai_runs() == [], "opening the panel, picking a baseline and restarting leave no row behind"

    state, _ = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n))
    run_id = state.interview_runner.ai_run_id
    assert get_ai_run(run_id)["status"] == "plan_ready", "a proposed plan is not a running search"
    assert get_ai_run(run_id)["started_at"] is None

    state.restart()  # the user never clicked Start Search
    row = get_ai_run(run_id)
    assert row["status"] == "cancelled" and row["spec"]["objective_text"] == "cut tank ESR in half"
    assert [m["role"] for m in list_messages(run_id)][0] == "user", "the conversation is kept with it"
    assert state.phase == "interview" and not state.interview_state.transcript
    assert state.interview_runner.ai_run_id is None and len(list_ai_runs()) == 1


def test_restart_during_a_search_stops_it_and_keeps_it_in_history(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    state, _ = _planned_state(monkeypatch, _wait_for_abort)
    run_state = _start(state)
    assert get_ai_run(run_state.id)["status"] == "running" and state.baseline_locked
    _poll_until(lambda: run_state.rounds, timeout=10)

    state.restart()
    assert run_state.cancelled
    assert state.phase == "interview" and state.run_state is None and not state.baseline_locked

    _poll_until(lambda: get_ai_run(run_state.id)["status"] != "running", timeout=10)
    row = get_ai_run(run_state.id)
    assert (row["status"], row["stop_reason"]) == ("cancelled", "user_stopped")
    assert row["spec"] is not None and row["baseline_coilset"]["tank_center_r"] == 72.688
    assert len(list_messages(run_state.id, phase="optimize")) == 1, "the round that was in flight is recorded"
    assert list_messages(run_state.id, phase="interview"), "and so is the conversation"


def test_stop_and_keep_the_best_ends_the_search_but_stays_on_the_run(monkeypatch):
    def search_ai(n, kwargs):
        return _proposal(n) if n == 1 else _wait_for_abort(n, kwargs)

    state, calls = _planned_state(monkeypatch, search_ai, max_rounds=10)
    run_state = _start(state)
    _poll_until(lambda: len(calls) == 2, timeout=10)  # round 1 solved, round 2's request is out

    state.stop_search()
    row = _finished(state)

    assert state.run_state is run_state, "the chat stays on the stopped run"
    assert (row["status"], row["stop_reason"], row["best_round"]) == ("cancelled", "user_stopped", 1)
    assert run_state.best.coilset.tank_center_r == 71.0 and run_state.outcome_report.startswith("Target not achieved: you stopped")
    assert _finished_status_text(run_state) == "Search finished: stopped by you after 2 round(s) (stopped by you)."


def test_typing_continue_resumes_the_same_run_with_a_fresh_budget(monkeypatch):
    from agent.repositories.ai_run_repository import list_ai_runs, list_messages

    state, calls = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n), max_rounds=2)
    run_state = _start(state)
    row = _finished(state)
    assert (row["status"], row["stop_reason"]) == ("budget_exhausted", "rounds") and len(run_state.rounds) == 2

    state.chat_input = "  Continue "
    state.send_chat()
    assert state.chat_input == "" and not state.interview_runner.busy, "code handles it: no AI turn"
    assert state.run_state is run_state and len(state.search_markers) == 2
    row = _finished(state)

    assert len(run_state.rounds) == 4 and run_state.segment == 2
    assert [b["segment"] for b in row["budget"]] == [1, 2] and len(list_ai_runs()) == 1
    assert [m["seq"] for m in list_messages(run_state.id, phase="optimize")] == [1, 2, 3, 4]
    assert list_messages(run_state.id, phase="interview")[-1]["content"] == "Continue", "the chat shows why it resumed"
    assert "'tank_center_r': 71.0" in calls[2][2]["content"], "the model still knows what the first segment tried"

    # The Continue button is the same function; a message that merely contains the word is a question.
    assert state.continue_search()
    _finished(state)
    assert len(run_state.rounds) == 6 and len(state.search_markers) == 3
    monkeypatch.setattr("agent.femm_agent.services.followup.call_llm",
                        lambda messages, tools, **kwargs: {"role": "assistant", "content": "It ran out of rounds."})
    state.chat_input = "should I continue?"
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    assert len(run_state.rounds) == 6 and state.interview_state.transcript[-1]["content"] == "It ran out of rounds."


def test_history_lists_every_status_with_its_own_label_and_delete_is_explicit(monkeypatch):
    from agent.repositories import ai_run_repository as ar
    from frontend.screen_agent import agent_history

    assert set(agent_history.STATUS_LABELS) == set(ar.STATUSES), "every status has a label"
    assert len({label for label, _ in agent_history.STATUS_LABELS.values()}) == len(ar.STATUSES)

    planning = ar.create_ai_run("base_coil")["id"]
    cancelled = ar.create_ai_run("base_coil")["id"]
    ar.transition(cancelled, "cancelled")
    running = ar.create_ai_run("base_coil")["id"]
    ar.finalize_spec(running, {"distances": [5.0]})
    ar.transition(running, "running")

    history = agent_history.AgentHistoryState()
    assert {r["id"] for r in history.visible_runs()} == {planning, cancelled, running}, "nothing is hidden"

    history.selected_id = cancelled
    history.delete(cancelled)
    assert ar.get_ai_run(cancelled) is None and history.selected_id is None
    history.delete(running)
    assert ar.get_ai_run(running) is not None, "a search still running is not deleted from History"


def test_off_topic_shows_a_fixed_message_never_the_models_own_words():
    """_message_display_text() must catch flag_off_topic BEFORE its normal
    "content wins first" rule - a model can put real explanatory text in
    "content" on the very same reply that also calls the tool, and none of
    it should ever reach this function's caller. Pure function, no LLM/DB/
    JobRunner needed - the whole point is this must hold with no live
    system involved at all."""
    msg_with_leaky_content = {
        "role": "assistant",
        "content": "Sure, here's a poem about wireless power coils for you:",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "flag_off_topic", "arguments": {"reason": "asked for a poem"}}}],
    }
    text = _message_display_text(msg_with_leaky_content, tool_outcome={"valid": False, "off_topic": True})

    assert "poem" not in text.lower(), "the model's own leaked content must never appear in the fixed redirect"
    assert text == "That's outside what I can help with here."
    print("  ok  an off-topic flag always shows the fixed redirect, even if the model also wrote real content")


def test_off_topic_display_does_not_need_a_tool_outcome_at_all():
    # tool_outcome can be None the instant the message lands (result not
    # persisted/paired yet) - the fixed text must not depend on it.
    msg = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "flag_off_topic", "arguments": {}}}]}
    assert _message_display_text(msg, tool_outcome=None).startswith("That's outside what I can help with here")
    print("  ok  the off-topic redirect shows immediately, before any tool_outcome has landed")


# --- a baseline that can't be simulated blocks planning ----------------------------------------
# No LLM and no FEMM: a fake job runner whose solves fail (or succeed) on demand.

class _FakeJob:
    def __init__(self, status, error=None, result=None):
        class _Status:
            value = status
        self.status, self.error, self.result = _Status(), error, result


class _FakeSolver:
    """find_cached always misses; submit() returns a job that already failed
    (fail=True) or is still solving (fail=False). `submitted` counts the solves queued."""
    def __init__(self, fail=True):
        self.fail, self.submitted = fail, 0

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.submitted += 1
        return _FakeJob("failed", error="turn space of 1 is less than total turn width of 5") if self.fail \
            else _FakeJob("running")


def _finalized_state(solver):
    """A state with a baseline picked and a finalized (not yet confirmed) plan, no LLM involved."""
    import dataclasses
    from agent.input_chat.models.spec import AgentSpec

    state = AgentPanelState(solver)
    state.select_config("base_coil")
    state.interview_state.spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[5.0, 10.0])
    state.interview_state.finalized = True
    return state


def test_a_failed_baseline_solve_blocks_the_chat_and_start_search():
    state = _finalized_state(_FakeSolver(fail=True))
    state.poll()  # syncs the baseline solves to the plan's distances
    assert state.baseline_error and "Solve failed at 5 mm" in state.baseline_error
    assert state.planning_blocked

    state.chat_input = "reduce the ESR"
    state.send_chat()
    assert state.chat_input == "reduce the ESR", "a blocked send keeps the typed text"
    assert not state.interview_state.transcript and not state.interview_runner.busy, "no LLM turn is started"

    state.confirm_plan()
    assert not state.plan_confirmed
    for _ in range(3):
        state.poll()
    assert state.run_state is None, "the search must not start on a baseline that can't be solved"


def test_a_baseline_that_is_still_solving_does_not_block():
    state = _finalized_state(_FakeSolver(fail=False))
    state.poll()
    assert state.baseline_error is None and not state.planning_blocked
    state.confirm_plan()
    assert state.plan_confirmed


def test_retry_baseline_requeues_only_the_failed_solves_and_unblocks_when_they_succeed():
    solver = _FakeSolver(fail=True)
    state = _finalized_state(solver)
    state.poll()
    queued = solver.submitted
    assert state.planning_blocked

    solver.fail = False  # e.g. a crash or timeout that has since cleared
    state.retry_baseline()
    assert solver.submitted == queued + len(state.baseline_runs), "every failed distance is solved again"
    assert not state.planning_blocked and state.baseline_error is None

    state.retry_baseline()  # nothing failed now - must not queue anything
    assert solver.submitted == queued + len(state.baseline_runs)


def test_a_failed_baseline_does_not_block_once_the_search_has_started():
    state = _finalized_state(_FakeSolver(fail=False))
    state.poll()
    state.run_state = object()  # a search is (pretend) under way
    state.baseline_runs[5.0] = _FakeJob("failed", error="late failure")
    assert state.baseline_error is not None
    assert not state.planning_blocked, "follow-up questions stay available during and after a search"


# ---- the Search fields table: a proposal is applied only by the user's click ---------------------

def _planning_state(monkeypatch):
    """A state mid-interview on a solvable baseline, with the LLM turn replaced
    by a recorder - Apply must send exactly one fixed message, nothing else."""
    state = AgentPanelState(_FakeSolver(fail=False))
    state.select_config("base_coil")
    sent = []
    monkeypatch.setattr(state.interview_runner, "send", lambda interview_state, text: sent.append(text))
    return state, sent


def test_a_proposed_search_fields_change_does_nothing_until_apply(monkeypatch):
    from agent.repositories import agent_settings_repository
    from frontend.screen_agent.agent_panel import FIELD_CHANGE_APPLIED_MESSAGE

    state, sent = _planning_state(monkeypatch)
    assert "tank_mu" not in state.field_limits() and state.field_limits()["num_tank_layers"] == "1 to 8"
    state.interview_state.pending_field_change = {
        "changes": [{"field": "tank_mu", "enabled": True, "max": 2000}], "reason": "you asked to tune ferrite"}

    for _ in range(3):
        state.poll()
    assert agent_settings_repository.load_overrides() == {}, "showing a proposal writes nothing"
    assert "tank_mu" not in state.field_limits()

    state.apply_field_change()
    assert agent_settings_repository.load_overrides() == {
        "search_fields": {"tank_mu": {"enabled": True, "min": None, "max": 2000}}}
    assert state.pending_field_change is None
    assert state.field_limits()["tank_mu"] == "0 to 2000", "this conversation now plans with the new table"
    assert sent == [FIELD_CHANGE_APPLIED_MESSAGE], "the model is told with one fixed, code-written message"

    state.apply_field_change()  # nothing pending any more
    assert sent == [FIELD_CHANGE_APPLIED_MESSAGE]


def test_dismissing_a_proposal_changes_nothing(monkeypatch):
    from agent.repositories import agent_settings_repository

    state, sent = _planning_state(monkeypatch)
    state.interview_state.pending_field_change = {"changes": [{"field": "tank_mu", "enabled": True}], "reason": ""}
    state.dismiss_field_change()
    assert state.pending_field_change is None and sent == []
    assert agent_settings_repository.load_overrides() == {}


def test_a_stale_or_broken_proposal_cannot_be_applied(monkeypatch):
    from agent.repositories import agent_settings_repository

    state, sent = _planning_state(monkeypatch)
    state.interview_state.pending_field_change = {"changes": [{"field": "no_such_field", "enabled": True}], "reason": ""}
    state.apply_field_change()
    assert "Couldn't apply" in state.error and sent == []
    assert agent_settings_repository.load_overrides() == {}


def test_start_search_copies_the_saved_table_into_the_plan_and_blocks_a_plan_it_breaks():
    """The table is copied into the run when it starts. If it was edited in
    Settings after the plan was proposed and the plan no longer fits (here the
    baseline is now outside a limit), Start Search does not start."""
    from agent.repositories.ai_run_repository import get_ai_run
    from agent.settings.settings import AgentSettings, save_settings

    import dataclasses
    from agent.repositories.ai_run_repository import create_ai_run, finalize_spec

    state = _finalized_state(_FakeSolver(fail=False))
    # The chat's row, as the interview leaves it once a plan is proposed.
    state.interview_runner.ai_run_id = create_ai_run("base_coil")["id"]
    finalize_spec(state.interview_runner.ai_run_id, dataclasses.asdict(state.interview_state.spec))
    state.poll()
    baseline_r = state.baseline_coilset.tank_center_r
    save_settings(AgentSettings(search_fields={"tank_center_r": {"enabled": True, "min": None, "max": baseline_r - 1}}))

    state.confirm_plan()
    assert not state.plan_confirmed
    assert len(state.plan_problems) == 1 and "tank_center_r" in state.plan_problems[0]
    for _ in range(3):
        state.poll()
    assert state.run_state is None

    save_settings(AgentSettings(search_fields={"tank_center_r": {"enabled": True, "min": None, "max": baseline_r + 50}}))
    assert state.recheck_plan() == []
    state.confirm_plan()
    assert state.plan_confirmed
    wanted = {"tank_center_r": {"enabled": True, "min": None, "max": baseline_r + 50}}
    assert state.interview_state.spec.settings.search_fields == wanted
    stored = get_ai_run(state.interview_runner.ai_run_id)["spec"]["settings"]["search_fields"]
    assert stored == wanted, "the stored plan records the table the run uses"
