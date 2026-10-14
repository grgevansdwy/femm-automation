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
from frontend.screen_agent.agent_panel import AgentPanelState, _message_display_text


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
    # one ai_run id, replayable from the repository agent_sidebar.py
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

    # A new chat leaves the finished run as it is, in the sidebar.
    finished_id = state.run_state.id
    state.new_chat()
    assert state.phase == "interview" and state.run_state is None
    assert state.interview_runner.ai_run_id != finished_id
    assert get_ai_run(finished_id)["status"] == finished_status


def test_the_baseline_is_locked_once_anything_is_typed_in_the_chat(monkeypatch):
    """A scripted interview AI - no server needed."""
    from agent.input_chat.services import interview
    from agent.repositories.ai_run_repository import list_ai_runs
    from engine.repositories.coilset_repository import restore_config, get_config

    other = {**get_config("base_coil"), "id": "other_coil", "name": "other_coil"}
    restore_config(other)
    monkeypatch.setattr(interview, "call_llm",
                        lambda messages, tools, **kwargs: {"role": "assistant", "content": "At which distances?"})

    state = AgentPanelState(JobRunner())
    state.select_config("base_coil")
    untouched = state.chat
    assert not state.baseline_locked
    state.request_baseline("base_coil")
    assert state.chat is untouched, "re-picking the same baseline does nothing"

    # Nothing typed yet: the baseline can still be changed, in the same empty chat.
    state.request_baseline("other_coil")
    assert state.selected_config_id == "other_coil" and list_ai_runs() == []

    # An unsent draft already locks it.
    state.chat_input = "half-typed objective"
    assert state.baseline_locked
    state.request_baseline("base_coil")
    assert state.selected_config_id == "other_coil" and state.chat_input == "half-typed objective"
    state.chat_input = ""
    assert not state.baseline_locked

    # A sent message locks it for good: "+ New" is the way to another baseline.
    state.chat_input = "half-typed objective"
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    assert state.baseline_locked
    first_chat = state.chat
    state.request_baseline("base_coil")
    assert state.chat is first_chat and state.selected_config_id == "other_coil"
    assert [m["content"] for m in state.interview_state.transcript] == ["half-typed objective", "At which distances?"]

    state.new_chat("base_coil")
    assert state.selected_config_id == "base_coil" and not state.baseline_locked and not state.interview_state.transcript


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


def test_no_run_is_saved_until_the_first_message_and_a_new_chat_leaves_the_plan_waiting(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_ai_runs, list_messages

    empty = AgentPanelState(_InstantSolver())
    empty.select_config("base_coil")
    empty.new_chat()
    empty.select_config("base_coil")
    assert list_ai_runs() == [], "opening the panel, picking a baseline and New leave no row behind"

    state, _ = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n))
    run_id = state.interview_runner.ai_run_id
    assert get_ai_run(run_id)["status"] == "plan_ready", "a proposed plan is not a running search"
    assert get_ai_run(run_id)["started_at"] is None

    state.new_chat()  # the user never clicked Start Search
    row = get_ai_run(run_id)
    assert row["status"] == "plan_ready" and row["spec"]["objective_text"] == "cut tank ESR in half"
    assert [m["role"] for m in list_messages(run_id)][0] == "user", "the conversation is kept with it"
    assert state.phase == "interview" and not state.interview_state.transcript
    assert state.interview_runner.ai_run_id is None and len(list_ai_runs()) == 1


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
    from frontend.screen_agent.agent_panel import _finished_message
    assert "The search was stopped by you." in _finished_message(state.chat)


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

    # The Continue button records the same message; a message that merely contains the word is a question.
    assert state.can_continue and state.request_continue()
    _finished(state)
    assert len(run_state.rounds) == 6 and len(state.search_markers) == 3
    assert list_messages(run_state.id, phase="interview")[-1]["content"] == "continue"
    state.chat_input = "Please continue."
    state.send_chat()
    assert not state.interview_runner.busy, "code handles it: no AI turn"
    _finished(state)
    assert len(run_state.rounds) == 8 and len(state.search_markers) == 4
    monkeypatch.setattr("agent.femm_agent.services.followup.call_llm",
                        lambda messages, tools, **kwargs: {"role": "assistant", "content": "It ran out of rounds."})
    state.chat_input = "should I continue?"
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    assert len(run_state.rounds) == 8 and state.interview_state.transcript[-1]["content"] == "It ran out of rounds."


# --- changing the goal after a search (M5): the revised plan card and Start Search ---------------

def _revised_plan_reply(tolerance=1.0, distances=(5.0,)):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "p2", "type": "function", "function": {
        "name": "finalize_spec", "arguments": {
            "objective_text": "cut tank ESR a little", "distances": list(distances),
            "objectives": [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": tolerance}]}}}]}


def _finished_search(monkeypatch, followup_replies):
    """A chat whose first search (tank 71.0 and 70.0 against a 50% cut) has
    ended without meeting its goal. The follow-up AI answers from
    `followup_replies`, in order; `asked` collects (messages, tools) per request."""
    state, calls = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n), max_rounds=2)
    run_state = _start(state)
    row = _finished(state)
    assert (row["status"], len(run_state.rounds)) == ("budget_exhausted", 2)
    asked = []

    def followup_ai(messages, tools, **kwargs):
        asked.append((messages, tools))
        return followup_replies[len(asked) - 1]

    monkeypatch.setattr("agent.femm_agent.services.followup.call_llm", followup_ai)
    return state, run_state, calls, asked


def _say(state, text):
    state.chat_input = text
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy and not state.chat.restoring, timeout=20)
    state.poll()


def test_a_goal_changed_after_the_search_waits_as_a_plan_card_and_then_runs_on_the_same_run(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_ai_runs, list_messages
    from frontend.screen_agent.agent_panel import _marker_text

    state, run_state, calls, asked = _finished_search(monkeypatch, [_revised_plan_reply()])
    assert not (state.interview_state.finalized and not state.plan_confirmed), "no plan card after a finished search"

    _say(state, "1% is enough")

    assert [tool["function"]["name"] for tool in asked[0][1]] == ["finalize_spec", "propose_search_fields_change"]
    assert state.chat.revised_plan is state.interview_state.spec and state.chat.plan_in_force is run_state.spec
    assert state.interview_state.finalized and not state.plan_confirmed, "the plan card is on screen"
    assert get_ai_run(run_state.id)["status"] == "plan_ready" and len(run_state.rounds) == 2, "nothing searched yet"
    notes = state.revised_plan_notes()
    assert notes[0].startswith("Changed: Changed objective - tank_esr: required, >= 1% lower (was:")
    assert notes[-1] == "Continues from the 2 design(s) already simulated, re-scored against this plan."
    assert state.revised_plan_blocked() is None

    state.confirm_plan()
    state.poll()
    assert state.run_state is run_state and state.plan_confirmed and state.chat.revised_plan is None
    row = _finished(state)

    assert row["status"] == "goal_met" and run_state.plan == 2 and run_state.segment == 2
    assert len(list_ai_runs()) == 1 and len(run_state.rounds) > 2, "the same run, new rounds appended"
    assert [m["payload"]["segment"] for m in list_messages(run_state.id, phase="optimize")][:3] == [1, 1, 2]
    assert "'tank_center_r': 70.0} -> goal_met=True" in calls[2][2]["content"], "earlier designs re-scored in memory"
    assert len(state.search_markers) == 2
    assert _marker_text(state.chat, 1) == "Search started on the revised plan, with everything already tried re-scored against it."


def test_a_revised_plan_with_a_new_distance_says_it_starts_over_and_does(monkeypatch):
    from frontend.screen_agent.agent_panel import _marker_text

    state, run_state, calls, asked = _finished_search(monkeypatch, [_revised_plan_reply(distances=(5.0, 8.0))])
    _say(state, "also check 8 mm")

    notes = state.revised_plan_notes()
    assert "Changed: Distances: added 8 mm" in notes and "starts from the beginning" in notes[-1]
    assert set(state.baseline_runs) == {5.0, 8.0}, "the baseline is simulated at the new distance before Start Search"
    state.confirm_plan()
    state.poll()
    _finished(state)

    assert run_state.memory_from_round == 2 and [m["role"] for m in calls[2]] == ["system", "user"], "no memory"
    assert _marker_text(state.chat, 1).startswith("Search started on the revised plan, from the beginning")


def test_start_search_on_a_revised_plan_waits_for_the_baseline_at_a_new_distance(monkeypatch):
    state, run_state, calls, asked = _finished_search(monkeypatch, [_revised_plan_reply(distances=(5.0, 8.0))])
    _say(state, "also check 8 mm")
    del state.baseline_runs[8.0]  # as if that solve had not been queued yet

    assert state.revised_plan_blocked() == "The baseline is still being simulated at 8 mm."
    state.confirm_plan()
    assert state.chat.error == "The baseline is still being simulated at 8 mm." and run_state.segment == 1


def test_while_the_search_runs_the_chat_cannot_change_the_plan_but_can_stop_the_search_when_asked(monkeypatch):
    state, calls = _planned_state(monkeypatch, _wait_for_abort)
    run_state = _start(state)
    asked = []
    replies = [{"role": "assistant", "content": "Stop it first."}]
    monkeypatch.setattr("agent.femm_agent.services.followup.call_llm",
                        lambda messages, tools, **kwargs: asked.append(tools) or replies.pop(0))
    _say(state, "make it 1% instead")

    assert [[t["function"]["name"] for t in tools] for tools in asked] == [["stop_search"]], \
        "while a search runs the one tool is stop_search - never finalize_spec"
    assert state.chat.revised_plan is None and state.plan_confirmed
    assert not run_state.cancelled, "a question does not stop anything"

    stop = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "s1", "type": "function", "function": {"name": "stop_search", "arguments": {}}}]}
    replies[:] = [stop, {"role": "assistant", "content": "Stopping; the best design is kept."}]
    _say(state, "I am satisfied, please stop and keep the best")
    assert run_state.cancelled, "the AI's tool call stopped the search"
    _finished(state)
    assert state.interview_state.transcript[-1]["content"] == "Stopping; the best design is kept."


def test_a_revised_plan_left_waiting_can_be_started_after_the_chat_is_reopened(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    state, run_state, calls, asked = _finished_search(monkeypatch, [_revised_plan_reply()])
    _say(state, "1% is enough")
    run_id = run_state.id

    reopened = AgentPanelState(state.job_runner)  # as after closing and starting the app again
    reopened.open_chat(run_id)
    reopened.poll()
    chat = reopened.chat
    assert chat.run_state is None and chat.revised_plan is not None and not reopened.plan_confirmed
    assert set(chat.baseline_runs) == set(chat.revised_plan.distances), "the Baseline box has results to show"
    assert reopened.can_continue, "a reopened, ended search offers Continue"
    notes = reopened.revised_plan_notes()
    assert "reopened chat" in notes[0] and notes[1].startswith("Continues from the designs already simulated")

    reopened.confirm_plan()
    _poll_until(lambda: not chat.restoring and chat.run_state is not None, timeout=20)
    reopened.poll()
    _poll_until(lambda: get_ai_run(run_id)["status"] not in ("running", "plan_ready"), timeout=20)

    assert get_ai_run(run_id)["status"] == "goal_met" and chat.error == ""
    segments = [m["payload"]["segment"] for m in list_messages(run_id, phase="optimize")]
    assert segments[:2] == [1, 1] and 2 in segments, "its rounds were appended to the stored run"
    assert chat.run_state.plan == 2 and len(reopened.search_markers) == 2


def test_a_search_fields_change_proposed_after_a_search_waits_for_apply(monkeypatch):
    from agent.settings.settings import load_settings

    propose = {"role": "assistant", "content": "", "tool_calls": [{"id": "f1", "type": "function", "function": {
        "name": "propose_search_fields_change",
        "arguments": {"changes": [{"field": "tank_ferrite_thickness", "enabled": True}], "reason": "you asked"}}}]}
    state, run_state, calls, asked = _finished_search(monkeypatch, [
        propose, {"role": "assistant", "content": "I proposed opening the tank ferrite thickness."},
        {"role": "assistant", "content": "Applied. What should the plan do with it?"}])
    before = dict(load_settings().search_fields)
    _say(state, "let it change the tank ferrite too")

    assert state.pending_field_change["changes"][0]["field"] == "tank_ferrite_thickness"
    assert load_settings().search_fields == before and state.chat.revised_plan is None, "shown, nothing changed"

    state.apply_field_change()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    assert load_settings().search_fields["tank_ferrite_thickness"]["enabled"] is True
    assert state.pending_field_change is None and len(asked) == 3, "the follow-up model was told, not the interview"
    assert state.interview_state.transcript[-1]["content"] == "Applied. What should the plan do with it?"


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


# ---- the Field Config table: a proposal is applied only by the user's click ---------------------

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
