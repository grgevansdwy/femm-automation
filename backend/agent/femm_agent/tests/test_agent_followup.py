# =============================================================================
# test_agent_followup.py
#
# Checks agent/femm_agent/services/followup.py - the chat after a search has started - without an
# LLM or FEMM: the run snapshot the model gets, how the planning transcript is
# resent, and that a follow-up turn never gives the model any tools.
# frontend/tests/test_agent_panel.py covers the same thing live, end to end.
# =============================================================================
import dataclasses

from agent.femm_agent.services import followup
from agent.input_chat.services.interview import InterviewState
from agent.input_chat.models.objective import Objective
from agent.femm_agent.services.scorer import ObjectiveResult, Scored
from agent.input_chat.models.spec import AgentSpec
from agent.femm_agent.models.state import AgentRunState, AgentRunStatus, CandidateResult, RoundState, RoundStatus
from engine.repositories.coilset_repository import get_coilset

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": None, "k13": None, "k23": None}


def _run_state(status=AgentRunStatus.GOAL_MET):
    baseline = get_coilset("base_coil")
    objective = Objective(metric="tank_esr", kind="reduce", tolerance_pct=20.0)
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0],
                               objective_text="cut tank ESR 20%", objectives=[objective])
    best_coilset = dataclasses.replace(baseline, tank_center_r=70.5)
    best = Scored(
        coilset=best_coilset,
        metrics={10.0: {**_METRICS, "tank_esr": 0.341}},
        objective_results=[ObjectiveResult(objective=objective, change_pct=-20.1, value=0.341, baseline_value=0.427,
                                           margin=0.005, passed=True, points=0.1, change_pct_by_distance={10.0: -20.1},
                                           worst_distance=10.0)],
        goal_met=True, changed_fields={"tank_center_r": (72.688, 70.5)},
    )
    rejected = RoundState(round_num=1, status=RoundStatus.DONE, candidates=[
        CandidateResult(delta={"tank_center_r": 500.0}, valid=False, error="crosses the y-axis",
                        rationale="try a huge tank"),
    ])
    winner = RoundState(round_num=2, status=RoundStatus.DONE, candidates=[
        CandidateResult(delta={"tank_center_r": 70.5}, valid=True, tool_result={
            "valid": True, "goal_met": True,
            "objectives": [{"metric": "tank_esr", "value": 0.341, "worst_change_pct": -20.1, "passed": True,
                            "required": True, "target_value": None}],
        }, candidate_coilset=dataclasses.asdict(best_coilset), rationale="shrink the tank slightly"),
    ])
    return AgentRunState(id="r1", spec=spec, baseline_coilset=baseline, status=status, rounds=[rejected, winner],
                         best=best, baseline_metrics={10.0: _METRICS})


def test_run_context_covers_plan_baseline_rounds_and_best():
    text = followup.run_context(_run_state())
    assert "finished - goal met" in text
    assert "cut tank ESR 20%" in text and "tank_esr: Required, >= 20% lower" in text
    assert "tank_esr=0.427" in text  # baseline numbers
    assert "Round 1: changes {'tank_center_r': 500.0} -> rejected: crosses the y-axis" in text
    assert "shrink the tank slightly" in text
    assert "Found in round 2." in text
    assert "tank_esr: 0.341 vs baseline 0.427 (-20.10%" in text
    print("  ok  run_context() carries the plan, baseline, every round and the best design")


def test_prompt_wording_tracks_whether_the_search_is_still_running():
    running = followup.followup_system_prompt(_run_state(AgentRunStatus.RUNNING))
    finished = followup.followup_system_prompt(_run_state())
    assert "still running" in running and "its search keeps running" in running and "one search runs at a time" in running
    assert "has finished" in finished and "this one stays in the list" in finished
    assert "Restart" not in running + finished, "that button is gone: a new chat is + New"
    assert "BEST CANDIDATE SO FAR" in running and "PROPOSED DESIGN" in finished
    assert 'single word "continue"' in finished and 'single word "continue"' not in running
    assert "Stop and keep the best" in running and "Never call a target impossible" in finished
    assert "{{" not in running + finished
    print("  ok  the follow-up prompt says the right thing about a new chat while running vs. finished")


def test_followup_turn_sends_no_tools_and_plain_text_history(monkeypatch):
    sent = {}

    def fake_call_llm(messages, tools, **kwargs):
        sent["messages"], sent["tools"] = messages, tools
        return {"role": "assistant", "content": "Tank ESR dropped 20.1%.", "reasoning_content": "scratch"}

    monkeypatch.setattr(followup, "call_llm", fake_call_llm)
    conversation = InterviewState(baseline_config_id="base_coil", transcript=[
        {"role": "user", "content": "cut tank ESR 20%"},
        {"role": "assistant", "content": "", "reasoning_content": "thinking...",
         "tool_calls": [{"function": {"name": "finalize_spec", "arguments": {}}}]},
        {"role": "tool", "content": '{"valid": true}'},
    ], finalized=True)
    spec_before = conversation.spec

    followup.run_followup_turn(conversation, "how much did tank ESR drop?", _run_state())

    assert sent["tools"] is None
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert all(set(m) == {"role", "content"} for m in sent["messages"]), "no tool_calls/reasoning resent"
    assert conversation.transcript[-1] == {"role": "assistant", "content": "Tank ESR dropped 20.1%."}
    assert conversation.finalized and conversation.spec is spec_before, "a follow-up never touches the plan"
    print("  ok  a follow-up turn calls the model with no tools and leaves the plan alone")
