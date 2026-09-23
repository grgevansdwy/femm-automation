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


def test_restart_while_planning_discards_the_conversation():
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    state = AgentPanelState(JobRunner())
    state.select_config("base_coil")
    old_runner = state.interview_runner
    old_id = old_runner.ai_run_id

    state.chat_input = "Reduce tank ESR by 10% at 5mm."
    state.send_chat()
    assert old_runner.busy
    assert state.baseline_locked, "the baseline can't change mid-reply"

    state.restart()  # while the reply is still being generated
    assert get_ai_run(old_id) is None
    assert state.phase == "interview" and not state.interview_state.transcript
    assert state.interview_runner is not old_runner and not state.interview_runner.busy

    # The abandoned reply lands on its own thread and writes nothing.
    _poll_until(lambda: not old_runner.busy)
    assert get_ai_run(old_id) is None
    assert list_messages(old_id) == []


def test_restart_during_a_search_cancels_and_deletes_it():
    import dataclasses

    from agent.input_chat.models.spec import AgentSpec
    from agent.repositories.ai_run_repository import finalize_spec, get_ai_run, list_messages

    state = AgentPanelState(JobRunner())
    state.select_config("base_coil")
    # Skip the interview - a finalized plan, as if the model had proposed it.
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[5.0])
    state.interview_state.spec, state.interview_state.finalized = spec, True
    finalize_spec(state.interview_runner.ai_run_id, dataclasses.asdict(spec))
    state.confirm_plan()
    state.poll()
    run_state = state.run_state
    assert state.phase == "running" and state.baseline_locked
    _poll_until(lambda: run_state.rounds, timeout=600)

    state.restart()
    assert run_state.cancelled
    assert get_ai_run(run_state.id) is None
    assert state.phase == "interview" and state.run_state is None and not state.baseline_locked

    # The search thread stops at its next checkpoint and persists nothing.
    _poll_until(lambda: run_state.id not in state.agent_runner.runs, timeout=900)
    assert run_state.status.value == "running", "a cancelled run never gets a final status"
    assert get_ai_run(run_state.id) is None
    assert list_messages(run_state.id) == []


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
