# =============================================================================
# test_agent_live_reasoning.py
#
# Checks that the planning chat (agent/input_chat/services/interview.py) and the follow-up chat
# (agent/femm_agent/services/followup.py) both wire call_llm's on_update callback onto
# InterviewState.live_reasoning - the model's chain-of-thought, readable in
# the GUI the instant it streams in, not just once the whole reply lands.
# The search round's equivalent wiring (round_state.reasoning_text) is
# covered in test_agent_optimize.py, next to the rest of optimize.py's tests.
# No LLM or FEMM - call_llm is patched in both agent.input_chat.services.interview and
# agent.femm_agent.services.followup (each module imports its own name).
#     python3 -m pytest agent/tests/test_agent_live_reasoning.py  (run from backend/)
# =============================================================================
import dataclasses

from agent.input_chat.services import interview

from agent.femm_agent.services import followup
from agent.input_chat.services.interview import InterviewState, run_interview_turn
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.femm_agent.models.state import AgentRunState, AgentRunStatus
from engine.repositories.coilset_repository import get_coilset


def test_interview_turn_streams_live_reasoning_and_clears_it_on_the_next_turn(monkeypatch):
    seen = []

    def fake_call_llm(messages, tools, **kwargs):
        on_update = kwargs["on_update"]
        on_update("", "Let me ")
        seen.append(state.live_reasoning)
        on_update("", "Let me ask about the objective.")
        seen.append(state.live_reasoning)
        return {"role": "assistant", "content": "What's the objective?"}

    monkeypatch.setattr(interview, "call_llm", fake_call_llm)
    state = InterviewState(baseline_config_id="base_coil")
    assert state.live_reasoning == ""

    run_interview_turn(state, "help me design something")

    assert seen == ["Let me ", "Let me ask about the objective."], \
        "live_reasoning grows live as on_update fires, mid-turn"
    # Left in place after the turn (the GUI only shows it while busy=True,
    # gated in agent_panel.py) - but cleared again the instant the NEXT
    # turn starts, so a stale answer never leaks into a new one.
    assert state.live_reasoning == "Let me ask about the objective."

    def fake_call_llm_2(messages, tools, **kwargs):
        assert state.live_reasoning == "", "cleared before the new turn's first chunk arrives"
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(interview, "call_llm", fake_call_llm_2)
    run_interview_turn(state, "reduce tank ESR by 10% at 5mm")
    print("  ok  run_interview_turn() streams live_reasoning live and resets it each new turn")


def _followup_run_state():
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[5.0],
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=10.0)])
    return AgentRunState(id="r1", spec=spec, baseline_coilset=baseline, status=AgentRunStatus.RUNNING)


def test_followup_turn_streams_live_reasoning_on_the_same_shared_field(monkeypatch):
    """agent/femm_agent/services/followup.py reuses the planning chat's own InterviewState/
    live_reasoning field (same object, same attribute) - not a second,
    separate one - so the GUI's one "Thinking..." block works for both."""
    seen = []

    def fake_call_llm(messages, tools, **kwargs):
        on_update = kwargs["on_update"]
        on_update("", "Checking the latest round")
        seen.append(conversation.live_reasoning)
        return {"role": "assistant", "content": "Round 3 is still evaluating."}

    monkeypatch.setattr(followup, "call_llm", fake_call_llm)
    conversation = InterviewState(baseline_config_id="base_coil", transcript=[
        {"role": "user", "content": "cut tank esr"},
    ], finalized=True, live_reasoning="stale from an earlier turn")

    followup.run_followup_turn(conversation, "how's it going?", _followup_run_state())

    assert seen == ["Checking the latest round"], "cleared at the start of the turn, then streamed live"
    assert conversation.live_reasoning == "Checking the latest round"
    print("  ok  run_followup_turn() streams live_reasoning on the same InterviewState field")
