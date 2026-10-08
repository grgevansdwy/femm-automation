# =============================================================================
# test_agent_replan.py
#
# Changing the goal after a search has ended (milestone M5), with a scripted
# fake LLM (no server) and a counting fake job runner (no FEMM):
# - the follow-up chat turns a request into a revised plan only once the
#   search has ended and only when the caller allows it; code checks the plan,
#   the row waits at 'plan_ready', nothing is searched until Start Search,
# - AgentRunner.start_revised(): a new segment of the same run, fresh budget,
#   memory kept and re-scored when no distance is added, empty when one is,
# - a design the new plan does not allow stays in memory but can never win,
# - stored rounds are never rewritten,
# - restore.py rebuilds the same memory from the database.
#     python3 -m pytest agent/femm_agent/tests/test_agent_replan.py  (run from backend/)
# =============================================================================
import dataclasses
import json
import time

import pytest

from agent.femm_agent.models.state import AgentRunStatus
from agent.femm_agent.services import followup, optimize, replan
from agent.femm_agent.services.restore import restore_run
from agent.femm_agent.services.runner import AgentRunner
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.input_chat.models.variables import VariableSpec
from agent.input_chat.services import interview
from agent.input_chat.services.interview import InterviewState, _build_spec, spec_arguments
from agent.input_chat.services.runner import InterviewRunner
from agent.repositories.ai_run_repository import finalize_spec, get_ai_run, list_messages
from agent.settings.settings import AgentSettings

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": 0.6, "k13": 0.2, "k23": 0.6}
_BASE_R = 72.688  # base_coil's tank_center_r


class _Job:
    error = None

    def __init__(self, result, job_id):
        self.result, self.id, self.status = result, job_id, "done"


class _Jobs:
    """Tank ESR scales with tank_center_r. Every solve is remembered, so asking
    again for the same design at the same distance is a cache hit."""

    def __init__(self):
        self.cache: dict = {}
        self.submits: list = []  # (tank_center_r, distance)

    def find_cached(self, coilset, distance):
        return self.cache.get((coilset.tank_center_r, coilset.num_rc_turns, distance))

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.submits.append((coilset.tank_center_r, distance))
        result = {**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / _BASE_R}
        self.cache[(coilset.tank_center_r, coilset.num_rc_turns, distance)] = {"result": result}
        return _Job(result, f"job{len(self.submits)}")

    def cancel(self, job_id):
        return False

    def candidate_submits(self):
        return [s for s in self.submits if s[0] != _BASE_R]


def _propose(**fields):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "propose_coilset",
                                                      "arguments": {**fields, "rationale": "try it"}}}]}


def _cut(pct):
    return [Objective(metric="tank_esr", kind="reduce", tolerance_pct=pct)]


def _spec(objectives=None, distances=(10.0,), variable_spec=None, **settings):
    s = AgentSettings(**settings)
    return dataclasses.replace(
        AgentSpec.from_settings(s, baseline_config_id="base_coil"), distances=list(distances),
        candidates_per_round=1, objectives=objectives or _cut(50.0), variable_spec=variable_spec or VariableSpec())


def _wait(state):
    deadline = time.monotonic() + 20
    while (get_ai_run(state.id) or {}).get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return get_ai_run(state.id)


class _Search:
    """One run driven by a script of AI replies (past its end: plain text)."""

    def __init__(self, monkeypatch, script, spec):
        self.script, self.requests, self.jobs = list(script), [], _Jobs()

        def fake_call_llm(messages, tools, **kwargs):
            self.requests.append(messages)
            n = len(self.requests)
            return self.script[n - 1] if n <= len(self.script) else {"role": "assistant", "content": "Nothing more."}

        monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
        self.runner = AgentRunner(self.jobs)
        self.state = self.runner.submit(spec)
        self.row = _wait(self.state)

    def revise(self, spec, script=()):
        self.script += [None] * (len(self.requests) - len(self.script)) + list(script)
        self.first_request = len(self.requests)
        finalize_spec(self.state.id, dataclasses.asdict(spec))  # what an accepted plan does to the row
        self.runner.start_revised(self.state.id, spec)
        self.row = _wait(self.state)
        return self.row


def _first_search(monkeypatch, spec=None):
    """Two rounds that miss a 50% cut: tank 71.0 (-2.3%) and 70.0 (-3.7%)."""
    search = _Search(monkeypatch, [_propose(tank_center_r=71.0), _propose(tank_center_r=70.0)],
                     spec or _spec(max_tool_calls=2))
    assert (search.row["status"], search.row["stop_reason"], len(search.state.rounds)) == ("budget_exhausted", "rounds", 2)
    return search


def _payloads(run_id):
    return [json.dumps(m["payload"], sort_keys=True) for m in list_messages(run_id, phase="optimize")]


# --- which memory a revised plan starts with -----------------------------------------------------

def test_memory_is_kept_unless_a_distance_is_added():
    assert replan.memory_mode([3, 5], [3.0, 5.0]) == "kept"
    assert replan.memory_mode([3, 5], [5]) == "kept", "a removed distance: every design already has the rest"
    assert replan.memory_mode([5], [3, 5]) == "fresh", "an added distance: no earlier design was simulated there"
    assert replan.memory_mode([3, 5], [5, 8]) == "fresh"


def test_a_revised_goal_continues_the_same_run_with_earlier_designs_re_scored(monkeypatch):
    search = _first_search(monkeypatch)
    state, first_budget, stored = search.state, search.state.budget, _payloads(search.state.id)
    solved = len(search.jobs.submits)

    # A 1% cut: 71.0 (-2.3%) and 70.0 (-3.7%) both meet it now. 70.0 is the better
    # one and survives its fragility check (+2% is still -1.8%), so it is the
    # winner before any new round; 71.0 ranks below a winner and needs no check.
    revised = dataclasses.replace(state.spec, objectives=_cut(1.0))
    row = search.revise(revised, [_propose(tank_center_r=60.0)])

    assert state.spec is revised and state.plan == 2 and state.segment == 2 and state.memory_from_round == 0
    assert [m["payload"]["segment"] for m in list_messages(state.id, phase="optimize")][:3] == [1, 1, 2], \
        "new rounds are appended to the same run"
    assert _payloads(state.id)[:2] == stored, "the stored rounds keep the result they got under their own plan"
    first = search.requests[search.first_request]
    assert [m["role"] for m in first] == ["system", "user", "user"]
    memory = first[2]["content"]
    assert "'tank_center_r': 71.0" in memory and "'tank_center_r': 70.0" in memory
    assert "{'tank_center_r': 70.0} -> goal_met=True" in memory, "re-scored: -3.7% meets the new 1% goal"
    assert "[survived the fragility check]" in memory, "checked under the new plan before the first round"
    assert "{'tank_center_r': 71.0} -> goal_met=True" in memory
    assert (state.fragility[0]["plan"], state.fragility[0]["round"], state.fragility[0]["passed"]) == (2, 2, True)
    new_solves = search.jobs.submits[solved:]
    assert (60.0, 10.0) in new_solves and not [s for s in new_solves if s[0] in (71.0, 70.0)], \
        "re-scoring cost no solve: only the checks' copies and the new design were solved"
    assert row["status"] == "goal_met" and state.best.coilset.tank_center_r == 60.0 and row["best_round"] == 3
    assert state.budget is not first_budget
    assert [(b["segment"], b["plan"], b.get("memory")) for b in row["budget"]] == [(1, 1, None), (2, 2, "kept")]
    assert row["baseline_coilset"]["tank_center_r"] == _BASE_R, "still measured against the original baseline"


def test_a_goal_an_earlier_design_already_meets_ends_without_a_new_round(monkeypatch):
    search = _first_search(monkeypatch)
    asked = len(search.requests)
    # M23 does not move in this fake solver, so every design tried holds it: nothing is left to push.
    row = search.revise(dataclasses.replace(search.state.spec, objectives=[Objective(metric="M23", kind="match", tolerance_pct=5.0)]))
    assert (row["status"], row["stop_reason"]) == ("goal_met", "nothing_left")
    assert len(search.state.rounds) == 2 and len(search.requests) == asked, "no round, no AI request"
    assert row["best_round"] in (1, 2) and row["budget"][-1]["rounds_used"] == 0


def test_an_added_distance_starts_from_the_beginning_in_the_same_run(monkeypatch):
    search = _first_search(monkeypatch)
    state, stored = search.state, _payloads(search.state.id)

    row = search.revise(dataclasses.replace(state.spec, distances=[10.0, 20.0]), [_propose(tank_center_r=69.0)])

    first = search.requests[search.first_request]
    assert [m["role"] for m in first] == ["system", "user"], "no memory: nothing tried before is shown to the model"
    assert state.memory_from_round == 2 and row["budget"][-1]["memory"] == "fresh"
    assert row["budget"][-1]["distances"] == [10.0, 20.0] and row["budget"][0]["distances"] == [10.0]
    assert _payloads(state.id)[:2] == stored and len(state.rounds) > 2, "the earlier rounds are still there"
    assert [t.delta for t in state.tried] == [{"tank_center_r": 69.0}], "and no earlier design is in the memory"
    assert not any(r == 71.0 or r == 70.0 for r, d in search.jobs.submits if d == 20.0), \
        "no earlier design was solved at the new distance"
    assert state.best.coilset.tank_center_r == 69.0 and row["best_coilset"]["tank_center_r"] == 69.0


def test_the_earlier_best_design_is_not_reported_for_a_plan_that_started_over(monkeypatch):
    search = _first_search(monkeypatch)
    assert search.row["best_coilset"]["tank_center_r"] == 70.0
    row = search.revise(dataclasses.replace(search.state.spec, distances=[10.0, 20.0]))  # the model proposes nothing
    assert row["status"] == "budget_exhausted" and search.state.best is None
    assert row["best_coilset"] is None and row["best_round"] is None


def test_a_removed_distance_keeps_the_memory_and_going_back_starts_over(monkeypatch):
    search = _first_search(monkeypatch, _spec(distances=(10.0, 20.0), max_tool_calls=2))
    state, solved = search.state, len(search.jobs.submits)

    search.revise(dataclasses.replace(state.spec, distances=[10.0]))
    assert state.segment_memory == "kept" and len(search.jobs.submits) == solved
    assert "'tank_center_r': 71.0" in search.requests[search.first_request][2]["content"]

    search.revise(dataclasses.replace(state.spec, distances=[10.0, 20.0]))
    assert state.segment_memory == "fresh" and state.plan == 3
    assert [m["role"] for m in search.requests[search.first_request]] == ["system", "user"]


def test_a_design_the_new_plan_does_not_allow_stays_in_memory_but_cannot_win(monkeypatch):
    search = _first_search(monkeypatch)
    state = search.state
    # An easy goal both earlier designs would meet - but the plan now keeps the tank radius fixed.
    revised = dataclasses.replace(state.spec, objectives=_cut(1.0),
                                  variable_spec=VariableSpec(excluded_fields={"tank_center_r"}))
    row = search.revise(revised, [_propose(tank_center_r=60.0), _propose(num_rc_turns=state.baseline_coilset.num_rc_turns + 1)])

    memory = search.requests[search.first_request][2]["content"]
    assert memory.count("rejected: not_in_current_plan tank_center_r") == 2 and "Best " not in memory
    assert "'tank_center_r': 71.0" in memory, "the model still sees it was tried"
    assert state.rounds[2].candidates[0].valid is False, "and proposing the excluded field again is rejected as before"
    assert all(t.scored is None or "tank_center_r" not in t.delta for t in state.tried)
    assert row["status"] != "goal_met" and (state.best is None or state.best.coilset.tank_center_r == _BASE_R)


def test_a_pinned_value_an_earlier_design_lacks_makes_it_not_allowed():
    from engine.repositories.coilset_repository import get_coilset
    baseline = get_coilset("base_coil")
    design = dataclasses.replace(baseline, tank_center_r=70.0)
    free = _spec()
    def problem(coilset, spec):
        found = replan.design_problem(coilset, baseline, spec)
        return found and (found.code, found.field)

    assert problem(design, free) is None
    turns = baseline.num_rc_turns + 1
    pinned = _spec(variable_spec=VariableSpec(fixed_values={"num_rc_turns": turns}))
    assert problem(design, pinned) == ("not_allowed_value", "num_rc_turns")
    assert replan.design_problem(design, baseline, pinned).allowed == f"pinned to {turns}"
    assert problem(dataclasses.replace(design, num_rc_turns=turns), pinned) is None
    excluded = _spec(variable_spec=VariableSpec(excluded_fields={"tank_center_r"}))
    assert problem(design, excluded) == ("not_enabled", "tank_center_r")
    thick = dataclasses.replace(baseline, tank_trace_thickness=0.05)
    assert problem(thick, free) == ("not_allowed_value", "tank_trace_thickness"), "a value no proposal could carry"
    # The Search fields table the plan carries: a closed field, and a limit.
    closed = _spec(search_fields={"tank_center_r": {"enabled": False}})
    assert problem(design, closed) == ("not_enabled", "tank_center_r")
    limited = _spec(search_fields={"tank_center_r": {"min": 70.5}})
    assert problem(design, limited) == ("out_of_range", "tank_center_r")
    assert problem(dataclasses.replace(baseline, tank_center_r=71.0), limited) is None


# --- restoring from the database -----------------------------------------------------------------

def test_restore_builds_the_same_memory_as_the_live_search_after_a_fresh_start(monkeypatch):
    search = _first_search(monkeypatch)
    state = search.state
    search.revise(dataclasses.replace(state.spec, distances=[10.0, 20.0]), [_propose(tank_center_r=69.0)])
    solved = len(search.jobs.submits)

    restored = restore_run(state.id, search.jobs)

    assert len(search.jobs.submits) == solved, "every solve was a cache hit"
    assert (restored.plan, restored.memory_from_round, restored.segment) == (state.plan, state.memory_from_round, state.segment)
    assert optimize.search_memory(restored.tried, None, 10) == optimize.search_memory(state.tried, None, 10)
    assert [t.result for t in restored.tried] == [t.result for t in state.tried]
    assert restored.best.coilset == state.best.coilset and len(restored.rounds) == len(state.rounds)


@pytest.mark.parametrize("distances, mode, in_memory", [([10.0], "kept", 2), ([10.0, 20.0], "fresh", 0)])
def test_restore_of_a_revised_plan_not_started_yet_shows_what_start_search_will_use(monkeypatch, distances, mode, in_memory):
    search = _first_search(monkeypatch)
    state, solved = search.state, len(search.jobs.submits)
    finalize_spec(state.id, dataclasses.asdict(dataclasses.replace(state.spec, objectives=_cut(3.0), distances=distances)))
    assert get_ai_run(state.id)["status"] == "plan_ready"

    restored = restore_run(state.id, search.jobs)

    assert (restored.status, restored.plan, restored.segment_memory) == (AgentRunStatus.PLAN_READY, 2, mode)
    assert len([t for t in restored.tried if t.scored is not None]) == in_memory and len(restored.rounds) == 2
    assert not [s for s in search.jobs.submits[solved:] if s[0] != _BASE_R], "no earlier design is solved anew"
    if mode == "kept":
        assert restored.best.goal_met and restored.best.coilset.tank_center_r == 70.0, "scored against the new goal"


def test_a_run_stored_before_budget_entries_named_their_distances_gets_them_when_its_plan_is_replaced(monkeypatch):
    from agent.repositories.ai_run_repository import transition
    search = _first_search(monkeypatch)
    state = search.state
    old_entry = {k: v for k, v in search.row["budget"][0].items() if k not in ("plan", "memory_from_round", "distances")}
    transition(state.id, "running")
    transition(state.id, "budget_exhausted", budget=[old_entry])
    finalize_spec(state.id, dataclasses.asdict(dataclasses.replace(state.spec, distances=[10.0, 20.0])))
    row = get_ai_run(state.id)
    assert row["budget"][0]["distances"] == [10.0] and row["spec"]["distances"] == [10.0, 20.0]
    assert restore_run(state.id, search.jobs).segment_memory == "fresh"


# --- the chat: turning a request into a revised plan ---------------------------------------------

def _finalize(**changes):
    args = {"objective_text": "cut tank ESR", "distances": [10.0],
            "objectives": [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 1.0}], **changes}
    return {"role": "assistant", "content": "", "reasoning_content": "thinking",
            "tool_calls": [{"id": "f1", "type": "function", "function": {"name": "finalize_spec", "arguments": args}}]}


def _chat(monkeypatch, replies):
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append((messages, tools))
        return replies[len(sent) - 1]

    monkeypatch.setattr(followup, "call_llm", fake_call_llm)
    return sent


def _conversation(state):
    return InterviewState(baseline_config_id="base_coil", spec=state.spec, finalized=True,
                          transcript=[{"role": "user", "content": "cut tank ESR by half"}])


def test_the_model_gets_finalize_spec_only_after_the_search_has_ended_and_only_when_allowed(monkeypatch):
    state = _first_search(monkeypatch).state
    sent = _chat(monkeypatch, [{"role": "assistant", "content": "ok"}] * 3)

    followup.run_followup_turn(_conversation(state), "make it 3%", state)
    assert sent[-1][1] is None, "not allowed by the caller: no tools, as before"
    assert "can't be changed here" in sent[-1][0][0]["content"]

    followup.run_followup_turn(_conversation(state), "make it 3%", state, allow_replan=True)
    assert [t["function"]["name"] for t in sent[-1][1]] == ["finalize_spec", "propose_search_fields_change"], \
        "no propose_coilset, ever"
    prompt = sent[-1][0][0]["content"]
    assert "## Revising the plan" in prompt and "can't be changed here" not in prompt and "{{" not in prompt
    assert "The same plan as finalize_spec arguments: " in prompt and "tank_center_r:" in prompt

    state.status = AgentRunStatus.RUNNING
    followup.run_followup_turn(_conversation(state), "make it 3%", state, allow_replan=True)
    assert sent[-1][1] is None, "a running search: no tools even when allowed"
    assert "Stop and keep" in sent[-1][0][0]["content"] and "Revising the plan" not in sent[-1][0][0]["content"]


def test_an_accepted_revised_plan_waits_for_start_search_and_then_runs_on_the_same_run(monkeypatch):
    search = _first_search(monkeypatch)
    state, rounds = search.state, len(search.state.rounds)
    _chat(monkeypatch, [_finalize()])
    conversation = _conversation(state)
    chat = InterviewRunner()
    chat.ai_run_id, chat._persisted_spec = state.id, state.spec

    chat.send_followup(conversation, state, "1% is enough", allow_replan=True)
    deadline = time.monotonic() + 10
    while chat.busy and time.monotonic() < deadline:
        time.sleep(0.02)

    assert conversation.error is None and conversation.spec is not state.spec
    assert conversation.spec.objectives == _cut(1.0) and state.spec.objectives == _cut(50.0), "the plan in force is untouched"
    row = get_ai_run(state.id)
    assert row["status"] == "plan_ready" and row["spec"]["objectives"][0]["tolerance_pct"] == 1.0
    assert row["best_coilset"]["tank_center_r"] == 70.0 and len(state.rounds) == rounds, "the result stays; nothing was searched"
    assert [m["role"] for m in conversation.transcript[-3:]] == ["user", "assistant", "tool"]
    assert json.loads(conversation.transcript[-1]["content"])["valid"] is True
    assert replan.plan_diff(state.spec, conversation.spec) == [
        "Changed objective - tank_esr: required, >= 1% lower (was: tank_esr: required, >= 50% lower)"]

    search.first_request = len(search.requests)
    search.runner.start_revised(state.id, conversation.spec)
    row = _wait(state)
    assert row["status"] == "goal_met" and state.winner.coilset.tank_center_r == 70.0, \
        "the earlier design meets the new goal and survived its check under it"
    assert [m["payload"]["segment"] for m in list_messages(state.id, phase="optimize")][2] == 2


def test_a_search_fields_change_asked_for_after_a_search_is_only_shown_to_the_user(monkeypatch):
    from agent.repositories import agent_settings_repository
    state = _first_search(monkeypatch).state
    before = agent_settings_repository.load_overrides()
    propose = {"role": "assistant", "content": "", "tool_calls": [{"id": "p1", "type": "function", "function": {
        "name": "propose_search_fields_change",
        "arguments": {"changes": [{"field": "tank_ferrite_thickness", "enabled": True}], "reason": "you asked to open ferrite"}}}]}
    sent = _chat(monkeypatch, [propose, {"role": "assistant", "content": "I proposed opening tank ferrite thickness."}])
    conversation = _conversation(state)

    followup.run_followup_turn(conversation, "let it change the tank ferrite too", state, allow_replan=True)

    assert conversation.pending_field_change["changes"][0]["field"] == "tank_ferrite_thickness"
    assert conversation.spec is state.spec, "no plan was changed"
    assert agent_settings_repository.load_overrides() == before, "and nothing was written to Settings"
    assert json.loads(conversation.transcript[-2]["content"])["valid"] is True
    assert conversation.transcript[-1]["content"] == "I proposed opening tank ferrite thickness."
    assert len(sent) == 2 and sent[1][1] is None

    _chat(monkeypatch, [{**propose, "tool_calls": [{"function": {"name": "propose_search_fields_change",
                                                                "arguments": {"changes": [{"field": "no_such_field"}]}}}]},
                        {"role": "assistant", "content": "That field does not exist."}])
    conversation = _conversation(state)
    followup.run_followup_turn(conversation, "open the flux capacitor", state, allow_replan=True)
    assert conversation.pending_field_change is None and json.loads(conversation.transcript[-2]["content"])["valid"] is False


def test_a_rejected_revised_plan_changes_nothing_and_becomes_a_question(monkeypatch):
    state = _first_search(monkeypatch).state
    for bad in (_finalize(distances=[]), _finalize(objectives="tank_esr"), _finalize(fixed_values={"no_such_field": 1}),
                _finalize(objectives=[{"metric": "tank_ferrite_thickness", "kind": "reduce", "tolerance_pct": 5}])):
        sent = _chat(monkeypatch, [bad, {"role": "assistant", "content": "Which distance did you mean?"}])
        conversation = _conversation(state)
        followup.run_followup_turn(conversation, "change it", state, allow_replan=True)
        assert conversation.spec is state.spec and conversation.error is None
        assert json.loads(conversation.transcript[-2]["content"])["valid"] is False
        assert conversation.transcript[-1] == {"role": "assistant", "content": "Which distance did you mean?"}
        assert len(sent) == 2 and sent[1][1] is None, "the second call has no tools: no unseen second attempt"
        assert sent[1][0][-1]["role"] == "tool" and "reasoning_content" not in sent[1][0][-2]
    assert get_ai_run(state.id)["status"] == "budget_exhausted"


def test_a_plan_that_changes_nothing_is_not_left_waiting(monkeypatch):
    state = _first_search(monkeypatch).state
    same = _finalize(objectives=[{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 50.0}])
    sent = _chat(monkeypatch, [same, {"role": "assistant", "content": "That is already the plan."}])
    conversation = _conversation(state)
    followup.run_followup_turn(conversation, "keep going", state, allow_replan=True, settings=state.spec.settings)
    assert conversation.spec is state.spec and "already has" in json.loads(conversation.transcript[-2]["content"])["error"]
    assert len(sent) == 2 and get_ai_run(state.id)["status"] == "budget_exhausted"


def test_a_plan_that_arrives_after_the_search_started_again_is_rejected(monkeypatch):
    search = _first_search(monkeypatch)
    state = search.state

    def reply_while_search_restarts(messages, tools, **kwargs):
        state.status = AgentRunStatus.RUNNING  # the user clicked Continue while this turn was in flight
        return _finalize()

    monkeypatch.setattr(followup, "call_llm", lambda m, t, **k: reply_while_search_restarts(m, t) if t else {"role": "assistant", "content": "It is running."})
    conversation = _conversation(state)
    followup.run_followup_turn(conversation, "3% is enough", state, allow_replan=True)
    assert conversation.spec is state.spec
    assert "a search is running" in json.loads(conversation.transcript[-2]["content"])["error"]


def test_a_revised_plan_cannot_be_saved_over_a_running_search(monkeypatch):
    from agent.repositories.ai_run_repository import transition
    state = _first_search(monkeypatch).state
    transition(state.id, "running")
    before = get_ai_run(state.id)["spec"]
    conversation = _conversation(state)
    conversation.spec = dataclasses.replace(state.spec, objectives=_cut(3.0))
    chat = InterviewRunner()
    chat.ai_run_id, chat._persisted_spec, chat._persisted_count = state.id, state.spec, len(conversation.transcript)
    chat._persist_new_messages(conversation)
    row = get_ai_run(state.id)
    assert row["status"] == "running" and row["spec"] == before and "Stop it first" in conversation.error


def test_a_revised_plan_is_checked_against_the_baseline_the_run_started_with(monkeypatch):
    state = _first_search(monkeypatch).state
    state.baseline_coilset = dataclasses.replace(state.baseline_coilset, config_name="as it was at Start Search")
    seen = {}
    real = interview._build_spec
    monkeypatch.setattr(interview, "_build_spec", lambda args, cid, baseline, settings=None: (
        seen.update(baseline=baseline), real(args, cid, baseline, settings))[1])
    _chat(monkeypatch, [_finalize()])
    followup.run_followup_turn(_conversation(state), "3% is enough", state, allow_replan=True)
    assert seen["baseline"] is state.baseline_coilset, "the run's own copy, not the config as it is today"


def test_the_plan_in_force_round_trips_through_finalize_spec_arguments():
    from engine.repositories.coilset_repository import get_coilset
    baseline = get_coilset("base_coil")
    spec = _build_spec({
        "objective_text": "cut ESR", "distances": [3, 5], "favourite_metric": "tank_esr",
        "excluded_fields": ["rc_radius"], "fixed_values": {"num_rc_turns": baseline.num_rc_turns + 1},
        "objectives": [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 10},
                       {"metric": "M23", "kind": "match", "tolerance_pct": 5, "target_pct": -10},
                       {"metric": "L_rc", "kind": "reduce", "tolerance_pct": 0, "target_value": 2e-6, "required": False}],
    }, "base_coil", baseline)
    again = _build_spec(json.loads(json.dumps(spec_arguments(spec))), "base_coil", baseline)
    assert (again.objectives, again.distances, again.variable_spec, again.favourite_metric, again.objective_text) == \
        (spec.objectives, spec.distances, spec.variable_spec, spec.favourite_metric, spec.objective_text)


def test_plan_diff_names_every_change_in_words():
    old = _spec(objectives=_cut(20.0) + [Objective(metric="M23", kind="match", tolerance_pct=5.0)], distances=(3.0, 5.0),
                variable_spec=VariableSpec(excluded_fields={"rc_radius"}, fixed_values={"num_rc_turns": 4}))
    new = _spec(objectives=_cut(10.0) + [Objective(metric="rc_esr", kind="reduce", tolerance_pct=5.0)], distances=(5.0, 8.0),
                variable_spec=VariableSpec(excluded_fields={"loop_r"}, fixed_values={"num_rc_turns": 5, "rc_radius": 30.0}))
    new = dataclasses.replace(new, favourite_metric="rc_esr")
    lines = replan.plan_diff(old, new)
    assert lines == [
        "Changed objective - tank_esr: required, >= 10% lower (was: tank_esr: required, >= 20% lower)",
        "Added objective - rc_esr: required, >= 5% lower",
        f"Removed objective - M23: required, {Objective(metric='M23', kind='match', tolerance_pct=5.0).describe()}",
        "Favourite: rc_esr (was: none)",
        "Distances: added 8 mm; removed 3 mm",
        "Pinned num_rc_turns = 5 (was 4)",
        "Pinned rc_radius = 30",
        "Now kept fixed: loop_r",
        "No longer kept fixed: rc_radius",
    ]
    assert replan.plan_diff(old, dataclasses.replace(old)) == []
    assert "starts from the beginning" in replan.memory_line(old, new)
    assert replan.memory_line(old, dataclasses.replace(old, distances=[5.0])).startswith("Continues from the 0 design(s)")


def test_run_data_tells_the_follow_up_model_about_plans_and_the_search_that_started_over(monkeypatch):
    search = _first_search(monkeypatch)
    state = search.state
    pending = dataclasses.replace(state.spec, distances=[10.0, 20.0])
    text = followup.run_context(state, pending_spec=pending, show_plan_arguments=True)
    assert "REVISED PLAN" in text and "Distances: added 20 mm" in text and "starts from the beginning" in text
    assert '"tolerance_pct": 50.0' in text

    search.revise(pending, [_propose(tank_center_r=69.0)])
    text = followup.run_context(state)
    assert "PLAN (revised 1 time(s) in this chat)" in text and "REVISED PLAN" not in text
    assert "EARLIER SEARCH" in text and "Rounds 1-2: an earlier plan at distances [10.0] mm, 2 design(s) simulated" in text
    assert "Round 3: changes {'tank_center_r': 69.0}" in text and "Round 1: changes" not in text
