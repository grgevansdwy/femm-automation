# =============================================================================
# test_agent_optimize.py
#
# Checks agent/femm_agent/services/optimize.py's batched round - several propose_coilset calls in
# one LLM reply, solved concurrently - without an LLM or FEMM: call_llm is
# patched, and solves go through a fake job runner that logs when each solve
# is queued and when anything waits on one.
#     python3 -m pytest agent/femm_agent/tests/test_agent_optimize.py  (run from backend/)
# =============================================================================
import dataclasses
import json

from agent.femm_agent.services import optimize
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.femm_agent.models.state import AgentRunState, Tried
from engine.repositories.coilset_repository import get_coilset

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": None, "k13": None, "k23": None}


class _Job:
    """Already solved, but reading .status - which is how
    scorer.wait_for_jobs() waits on a job - is logged as a wait."""

    def __init__(self, events, result=None, error=None):
        self._events, self.result, self.error = events, result, error
        self.id = f"job{len(events)}"

    @property
    def status(self):
        self._events.append(("wait", self.id))
        return "failed" if self.error else "done"


class _FakeJobRunner:
    def __init__(self, fail_tank_center_r=None):
        self.events = []
        self.identities = []  # (config_id, config_name) of each submit
        self._fail = fail_tank_center_r

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.events.append(("submit", coilset.tank_center_r))
        self.identities.append((config_id, config_name))
        if coilset.tank_center_r == self._fail:
            return _Job(self.events, error="femmcli crashed")
        # A smaller tank lowers tank ESR a little - not enough to meet the goal.
        return _Job(self.events, result={**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / 73})


def _tool_call(call_id, **args):
    return {"id": call_id, "type": "function", "function": {"name": "propose_coilset", "arguments": args}}


def test_a_round_queues_every_solve_before_waiting_and_isolates_failures(monkeypatch):
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=2,
                               candidates_per_round=4,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)])
    state = AgentRunState(id="r1", spec=spec, baseline_coilset=baseline)
    jobs = _FakeJobRunner(fail_tank_center_r=71.0)

    sent = []
    batch = {"role": "assistant", "content": "", "tool_calls": [
        _tool_call("c1", tank_center_r=70.0, rationale="shrink the tank"),
        _tool_call("c2", not_a_field=1, rationale="bad field"),
        _tool_call("c3", tank_center_r=71.0, rationale="shrink it less"),     # its solve fails
        _tool_call("c4", tank_center_r=70.0, rationale="same as c1"),         # repeat - not solved twice
        _tool_call("c5", tank_center_r=69.0, rationale="one too many"),       # over the per-round limit
    ]}

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return batch if len(sent) == 1 else {"role": "assistant", "content": "thinking"}

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, jobs)

    # The baseline is solved (and waited on) first; the round's events start at its first solve.
    assert jobs.events[0] == ("submit", baseline.tank_center_r)
    round_events = jobs.events[jobs.events.index(("submit", 70.0)):]
    submits = [e for e in round_events if e[0] == "submit"]
    assert submits == [("submit", 70.0), ("submit", 71.0)], "c4 reuses c1's solve, c2/c5 never reach FEMM"
    assert round_events[:len(submits)] == submits, \
        "every candidate's solves are queued before any of them is waited on"

    candidates = state.rounds[0].candidates
    assert [c.valid for c in candidates] == [True, False, False, True, False]
    assert candidates[1].error_code == "unknown_field"
    assert candidates[2].error_code == "solver_failed", "a failed solve rejects only its own candidate"
    assert "femmcli crashed" not in candidates[2].error, "the solver's own text never reaches the model or the GUI"
    assert candidates[4].error_code == "over_candidate_limit"
    assert candidates[0].tool_result == candidates[3].tool_result
    assert state.best is not None and state.best.coilset.tank_center_r == 70.0

    tool_messages = [m for m in sent[1] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3", "c4", "c5"]
    assert json.loads(tool_messages[0]["content"])["valid"] is True
    assert [json.loads(m["content"]).get("label") for m in tool_messages] == ["C1", None, None, "C2", None], \
        "only a simulated design gets a label, and labels stay dense"
    assert [c.label for c in candidates] == ["C1", None, None, "C2", None]
    assert state.stop_reason == "rounds"
    print("  ok  a batch queues all its solves first, isolates failures, and tags results with tool_call_id")


def test_prompt_asks_for_a_batch_only_when_the_spec_allows_one():
    from agent.femm_agent.services.prompts import system_prompt
    from agent.input_chat.models.variables import free_fields_for

    baseline = get_coilset("base_coil")
    spec = AgentSpec(baseline_config_id="base_coil", distances=[10.0])
    free = free_fields_for(baseline, spec.variable_spec)
    metrics = {10.0: _METRICS}
    batched = system_prompt(baseline, free, dataclasses.replace(spec, candidates_per_round=3), metrics)
    single = system_prompt(baseline, free, dataclasses.replace(spec, candidates_per_round=1), metrics)
    assert "up to 3 candidates" in batched and "probe:" in batched
    assert "one candidate per turn" in single and "probe:" not in single
    print("  ok  the system prompt describes batching only when candidates_per_round > 1")


def test_prompt_says_the_search_goes_on_after_a_pass_and_how_passing_designs_compare():
    from agent.femm_agent.services.prompts import system_prompt
    from agent.input_chat.models.variables import free_fields_for

    baseline = get_coilset("base_coil")
    spec = AgentSpec(baseline_config_id="base_coil", distances=[10.0])
    free = free_fields_for(baseline, spec.variable_spec)
    blended = system_prompt(baseline, free, spec, {10.0: _METRICS})
    favourite = system_prompt(baseline, free, dataclasses.replace(spec, favourite_metric="tank_esr"), {10.0: _METRICS})

    for text in (blended, favourite):
        assert "does NOT end at the first candidate with goal_met=true" in text and "{{" not in text
        assert "A candidate meeting the goal is not a reason to stop" in text
        assert "less than about 1 point(s)" in text
    assert "half the SMALLEST points" in blended and "alone" not in blended.split("## After a candidate")[1].split("##")[0]
    assert "compared on tank_esr alone" in favourite and "half the SMALLEST points" not in favourite
    print("  ok  the search prompt: keep improving after a pass; blended score or the favourite")


def test_each_request_carries_the_latest_round_raw_plus_the_best_candidates_so_far(monkeypatch):
    """The model's memory is rebuilt every round (optimize.search_memory()):
    only the latest round's results go in full, the best few candidates and
    everything else tried go as one line each, and no chain-of-thought is
    ever resent. Smaller tank_center_r = lower tank ESR in the fake runner,
    so the ranking is known in advance."""
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=3,
                               candidates_per_round=5, keep_best_candidates=2,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="r2", spec=spec, baseline_coilset=baseline)
    replies = [
        {"role": "assistant", "content": "", "reasoning_content": "SECRET-THOUGHTS-1", "tool_calls": [
            _tool_call("a", tank_center_r=70.0, rationale="r1"), _tool_call("b", tank_center_r=71.0, rationale="r1"),
            _tool_call("x", not_a_field=1, rationale="r1")]},
        {"role": "assistant", "content": "", "reasoning_content": "SECRET-THOUGHTS-2", "tool_calls": [
            _tool_call("c", tank_center_r=69.0, rationale="r2"), _tool_call("d", tank_center_r=72.0, rationale="r2")]},
        {"role": "assistant", "content": "done"},
    ]
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return replies[len(sent) - 1]

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _FakeJobRunner())

    assert len(sent[0]) == 2, "round 1: just the system prompt and the task, no memory yet"
    third = sent[2]
    assert "SECRET-THOUGHTS" not in json.dumps(third), "chain-of-thought is never resent"
    assert [m.get("tool_call_id") for m in third if m["role"] == "tool"] == ["c", "d"], "only the latest round in full"

    memory = next(m["content"] for m in third if m["role"] == "user" and "Best 2" in m["content"])
    best_lines = memory.split("Other candidates")[0]
    assert best_lines.index("69.0") < best_lines.index("70.0") and "71.0" not in best_lines, "best 2 overall, best first"
    others = memory.split("Other candidates")[1]
    assert "C2 (round 1): {'tank_center_r': 71.0} -> misses:" in others
    assert "round 1: {'not_a_field': 1} -> rejected:" in others
    assert "72.0" not in others, "the latest round is already shown in full, not repeated as a line"
    print("  ok  each request = latest round raw + best-N lines + one line per other tried candidate, no CoT")


def test_round_reasoning_text_updates_live_as_the_reply_streams_in(monkeypatch):
    """optimize.py must wire call_llm's on_update straight onto
    round_state.reasoning_text (agent/services/llm_client.py's live-streaming
    callback) - visible in the GUI's Search Progress the instant text
    arrives, not just once the whole round has finished."""
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               candidates_per_round=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="r_live", spec=spec, baseline_coilset=baseline)
    seen = []

    def fake_call_llm(messages, tools, **kwargs):
        on_update = kwargs["on_update"]
        assert state.rounds[-1].reasoning_text == "", "nothing streamed yet"
        on_update("", "Thinking")
        seen.append(state.rounds[-1].reasoning_text)
        on_update("", "Thinking about the tank")
        seen.append(state.rounds[-1].reasoning_text)
        on_update("", "Thinking about the tank coil's ESR...")
        seen.append(state.rounds[-1].reasoning_text)
        return {"role": "assistant", "content": "", "reasoning_content": "Thinking about the tank coil's ESR...",
                "tool_calls": [_tool_call("a", tank_center_r=70.0, rationale="shrink it")]}

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _FakeJobRunner())

    assert seen == ["Thinking", "Thinking about the tank", "Thinking about the tank coil's ESR..."], \
        "reasoning_text grows live as on_update fires, readable mid-request"
    assert state.rounds[0].reasoning_text == "Thinking about the tank coil's ESR...", \
        "the final value still matches the completed reply"
    print("  ok  round_state.reasoning_text streams live via call_llm's on_update, before the round finishes")


def test_candidate_solves_are_runs_only_never_saved_as_configs(monkeypatch):
    """An agent candidate is recorded as a run (which stores its own
    geometry) and must NOT create a row in the configs table - the config
    list holds only configs a person made. Its run name tells candidates
    apart: "<baseline name> [AI <hash>]"."""
    from engine.repositories.coilset_repository import list_configs

    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               candidates_per_round=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="r_cfg", spec=spec, baseline_coilset=baseline)

    def fake_call_llm(messages, tools, **kwargs):
        return {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("a", tank_center_r=70.0, rationale="shrink it")]}

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    job_runner = _FakeJobRunner()
    configs_before = {c["id"] for c in list_configs()}
    optimize.optimize_loop(state, job_runner)

    assert state.rounds[0].candidates[0].valid, "the candidate must have scored successfully"
    assert {c["id"] for c in list_configs()} == configs_before, "a candidate must not create a config"

    baseline_identity, candidate_identity = job_runner.identities[0], job_runner.identities[-1]
    assert baseline_identity[0] == "base_coil", "the baseline's own run keeps its real config id"
    config_id, name = candidate_identity
    assert config_id is None and name.startswith(f"{baseline.config_name} [AI "), candidate_identity
    print("  ok  a candidate's solve is a run only, named '<baseline> [AI <hash>]', with no config created")


# --- malformed / useless proposals are rejected per candidate, never crash the run ---------------

def _prepare_one(function, **kwargs):
    from agent.input_chat.models.variables import VariableSpec, allowed_fields, free_fields_for
    baseline = get_coilset("base_coil")
    variable_spec = kwargs.pop("variable_spec", VariableSpec())
    fixed = variable_spec.fixed_values
    return optimize._prepare({"id": "x", "function": function}, 0, 5, baseline, fixed,
                             free_fields_for(baseline, variable_spec), allowed_fields(baseline, variable_spec),
                             kwargs.pop("designs", None))


def test_prepare_rejects_arguments_that_are_not_a_json_object():
    for bad in (None, [], ["a", "b"], "oops", 5):
        p = _prepare_one({"name": "propose_coilset", "arguments": bad})
        assert p.rejection.code == "bad_arguments" and "must be a JSON object" in p.error, bad
        assert p.jobs is None and p.candidate is None


def test_prepare_rejects_a_call_to_any_other_tool():
    p = _prepare_one({"name": "delete_everything", "arguments": {"rationale": "x", "num_loop_turns": 2}})
    assert p.rejection.code == "unknown_tool" and "propose_coilset" in p.error
    assert "delete_everything" not in p.error, "the model's own text is never echoed back"
    p = _prepare_one({"arguments": {"rationale": "x", "num_loop_turns": 2}})  # no name at all
    assert p.rejection.code == "unknown_tool"


def test_prepare_rejects_a_proposal_that_changes_nothing():
    baseline = get_coilset("base_coil")
    assert "changes nothing" in _prepare_one({"name": "propose_coilset", "arguments": {"rationale": "x"}}).error
    same = {"tank_center_r": baseline.tank_center_r, "rationale": "x"}  # "changed" to the value it already has
    assert "changes nothing" in _prepare_one({"name": "propose_coilset", "arguments": same}).error
    ok = _prepare_one({"name": "propose_coilset", "arguments": {"tank_center_r": baseline.tank_center_r + 1, "rationale": "x"}})
    assert ok.error is None and ok.candidate is not None


def test_an_empty_proposal_is_allowed_when_pins_make_the_start_point_a_different_design():
    from agent.input_chat.models.variables import VariableSpec
    baseline = get_coilset("base_coil")
    pinned = VariableSpec(fixed_values={"tank_center_r": baseline.tank_center_r + 1})
    p = _prepare_one({"name": "propose_coilset", "arguments": {"rationale": "just the pinned design"}}, variable_spec=pinned)
    assert p.error is None


def test_a_malformed_call_costs_only_its_own_candidate_and_the_run_carries_on(monkeypatch):
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               candidates_per_round=4,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)])
    state = AgentRunState(id="r2", spec=spec, baseline_coilset=baseline)
    jobs = _FakeJobRunner()
    sent = []
    batch = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "b1", "type": "function", "function": {"name": "propose_coilset", "arguments": None}},
        {"id": "b2", "type": "function", "function": {"name": "other_tool", "arguments": {"tank_center_r": 70.0}}},
        _tool_call("b3", rationale="nothing"),
        _tool_call("b4", tank_center_r=70.0, rationale="the one good one"),
    ]}

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return batch

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, jobs)  # must not raise

    candidates = state.rounds[0].candidates
    assert [c.valid for c in candidates] == [False, False, False, True]
    assert state.best is not None and state.best.coilset.tank_center_r == 70.0
    assert state.stop_reason == "rounds"
    print("  ok  malformed calls are rejected one by one and the run carries on")


# --- starting designs: labels and the `base` argument --------------------------------------------

def test_a_proposal_can_build_on_an_earlier_design_by_its_label(monkeypatch):
    """ "C1 plus one value" builds C1's design with that value. `delta` stays
    every change from the baseline, `step` is what the call itself gave, and a
    bad `base` or a step that changes nothing is rejected on its own."""
    baseline = get_coilset("base_coil")
    wider = baseline.loop_r + 1
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=3,
                               candidates_per_round=5,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="b1", spec=spec, baseline_coilset=baseline)
    replies = [
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", tank_center_r=70.0, rationale="r1")]},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("b", base="C1", loop_r=wider, rationale="C1 plus a wider loop"),
            _tool_call("c", base="C9", loop_r=wider, rationale="no such design"),
            _tool_call("d", base="C1", tank_center_r=70.0, rationale="restates C1")]},
        {"role": "assistant", "content": "done"},
    ]
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return replies[len(sent) - 1]

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _FakeJobRunner())

    built, unknown, restated = state.rounds[1].candidates
    assert (built.label, built.base_label) == ("C2", "C1")
    assert built.step == {"loop_r": wider} and built.delta == {"tank_center_r": 70.0, "loop_r": wider}
    assert built.candidate_coilset["tank_center_r"] == 70.0 and built.candidate_coilset["loop_r"] == wider
    assert "label" not in built.tool_result, "the stored result stays comparable with scorer.tool_result()"
    assert {"label", "base_label", "step"} <= set(dataclasses.asdict(built)), "saved with the round's payload"
    assert not unknown.valid and unknown.label is None
    assert unknown.rejection == {"valid": False, "error": "unknown_base", "field": "base",
                                 "allowed": "a label shown in your results or memory, or no base",
                                 "detail": "No design with this label exists."}
    assert (restated.error_code, restated.rejection["detail"]) == \
        ("no_change", "This changes nothing from C1. Change at least one field.")

    memory = next(m["content"] for m in sent[2] if m["role"] == "user" and "Best" in m["content"])
    assert "C2 (round 2, from C1): {'tank_center_r': 70.0, 'loop_r': " in memory
    assert f"single   C1 -> C2: loop_r {baseline.loop_r:g} -> {wider:g} | tank_esr +0.0 pts" in memory
    assert json.loads(next(m["content"] for m in sent[2] if m.get("tool_call_id") == "b"))["label"] == "C2"
    print("  ok  base + step builds on an earlier design; unknown and no-op bases are rejected alone")


def test_a_field_outside_the_allowed_set_cannot_enter_through_a_base():
    """The audit compares the built design with the ORIGINAL baseline, so a base
    carrying a field that isn't free in this run is rejected, not inherited."""
    from types import SimpleNamespace
    baseline = get_coilset("base_coil")
    smuggler = Tried(1, {"tank_mu": baseline.tank_mu + 1}, SimpleNamespace(coilset=baseline), {}, label="C1")
    p = _prepare_one({"name": "propose_coilset",
                      "arguments": {"base": "C1", "loop_r": baseline.loop_r + 1, "rationale": "x"}},
                     designs={"C1": smuggler})
    assert p.candidate is None and (p.rejection.code, p.rejection.field) == ("not_enabled", "tank_mu")


def test_labels_keep_counting_when_the_search_carries_on_and_earlier_designs_stay_usable(monkeypatch):
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="b2", spec=spec, baseline_coilset=baseline)
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("a", tank_center_r=70.0, rationale="r")]},
        {"role": "assistant", "content": "", "tool_calls": [
            _tool_call("b", tank_center_r=69.0, rationale="r"),
            _tool_call("c", base="C1", loop_r=baseline.loop_r + 1, rationale="C1 is from the first segment")]},
    ])
    monkeypatch.setattr(optimize, "call_llm", lambda messages, tools, **kwargs: next(replies))
    optimize.optimize_loop(state, _FakeJobRunner())
    state.budget = None  # a "continue": a fresh budget, the same run and the same tried list
    optimize.optimize_loop(state, _FakeJobRunner())

    assert [c.label for r in state.rounds for c in r.candidates] == ["C1", "C2", "C3"]
    assert state.rounds[1].candidates[1].base_label == "C1"
    assert [t.label for t in state.tried] == ["C1", "C2", "C3"]


def test_the_tool_schema_offers_base_and_still_rejects_unknown_fields():
    from agent.femm_agent.models.schema import build_tools
    from agent.input_chat.models.variables import free_fields_for
    parameters = build_tools(free_fields_for(get_coilset("base_coil")), "base_coil")[0]["function"]["parameters"]
    assert parameters["properties"]["base"]["type"] == "string"
    assert parameters["required"] == ["rationale"] and parameters["additionalProperties"] is False


# --- the nudge and the edited prompt -------------------------------------------------------------

def test_untried_fields_are_listed_only_when_the_caller_passes_them():
    tried = [Tried(1, {"loop_r": 1.0}, None, {"error": "bad"})]
    assert "no notebook line" not in optimize.search_memory(tried, 2, 3)
    nudged = optimize.search_memory(tried, 2, 3, untried=["loop_r", "rc_radius"])
    assert "Fields with no notebook line yet: loop_r, rc_radius." in nudged
    assert not any(word in nudged.lower() for word in ("several", "multiple", "too many")), \
        "the nudge never comments on how many values a proposal changed"


def test_the_untried_fields_line_appears_only_once_the_verdict_is_not_improving(monkeypatch):
    """Rounds 2 and 3 bring nothing better than round 1, so code's verdict turns
    'stalled' after round 3 - only the request after that carries the line."""
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=4,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="n1", spec=spec, baseline_coilset=baseline)
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return {"role": "assistant", "content": "", "tool_calls": [
            _tool_call(f"t{len(sent)}", loop_r=baseline.loop_r + len(sent), rationale="r")]}  # the fake solver ignores loop_r

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _FakeJobRunner())

    assert [r.verdict for r in state.rounds] == ["improving", "improving", "stalled", "stalled"]
    nudged = ["Fields with no notebook line yet:" in "".join(m["content"] or "" for m in messages if m["role"] == "user")
              for messages in sent]
    assert nudged == [False, False, False, True]
    line = next(l for l in "\n".join(m["content"] for m in sent[3] if m["role"] == "user").splitlines()
                if l.startswith("Fields with no notebook line yet:"))
    assert "tank_center_r" in line and "loop_r" not in line


def test_memory_stays_bounded_over_a_full_length_search(monkeypatch):
    """25 rounds x 5 candidates: the notebook block never exceeds its cap, and
    the memory grows by a short line per design, not by full results."""
    from agent.femm_agent.services.notebook import NOTEBOOK_MAX_LINES
    baseline = get_coilset("base_coil")
    spec = AgentSpec(baseline_config_id="base_coil")
    spec = dataclasses.replace(spec, distances=[10.0], max_tool_calls=25, candidates_per_round=5,
                               settings=dataclasses.replace(spec.settings, stall_rounds=99),
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=90.0)])
    state = AgentRunState(id="m1", spec=spec, baseline_coilset=baseline)
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        n = len(sent)
        return {"role": "assistant", "content": "", "tool_calls": [
            _tool_call(f"t{n}_{i}", tank_center_r=round(72.0 - n * 0.1 - i * 0.01, 2), rationale="r") for i in range(5)]}

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _FakeJobRunner())

    assert len(sent) == 25 and len([t for t in state.tried if t.label]) == 125
    memory = [next((m["content"] for m in messages[2:] if m["role"] == "user"), "") for messages in sent]
    notebook = [line for line in memory[-1].splitlines() if line.startswith(("  single", "  combined"))]
    assert len(notebook) == NOTEBOOK_MAX_LINES and f"Newest {NOTEBOOK_MAX_LINES} of 120 lines." in memory[-1]
    growth = [len(b) - len(a) for a, b in zip(memory[10:], memory[11:])]
    assert max(growth) < 5 * 200, "about one short line per new design"
    assert len(memory[-1]) < 30_000


def test_the_default_search_prompt_renders_and_quotes_skin_depth_at_the_operating_frequency():
    from agent.femm_agent.services.prompts import system_prompt
    from agent.input_chat.models.variables import free_fields_for
    from agent.settings.prompt_defs import PROMPT_DEFS
    from agent.settings.templating import check_placeholders

    definition = PROMPT_DEFS["search_system"]
    assert check_placeholders(definition.default, definition.placeholders, definition.required) == []
    baseline = get_coilset("base_coil")
    spec = AgentSpec(baseline_config_id="base_coil", distances=[10.0])
    prompt = system_prompt(baseline, free_fields_for(baseline, spec.variable_spec), spec, {10.0: _METRICS})
    assert "{{" not in prompt and "}}" not in prompt
    assert "0.025mm" in prompt and "6.78MHz" in prompt and "100kHz" not in prompt and "1MHz)" not in prompt
    assert "## Starting design" in prompt and "lever notebook" in prompt
