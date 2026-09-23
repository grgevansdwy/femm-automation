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
from agent.femm_agent.models.state import AgentRunState, AgentRunStatus
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
        self._fail = fail_tank_center_r

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.events.append(("submit", coilset.tank_center_r))
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
    assert "not a valid field" in candidates[1].error
    assert "femmcli crashed" in candidates[2].error, "a failed solve rejects only its own candidate"
    assert "over the limit of 4" in candidates[4].error
    assert candidates[0].tool_result == candidates[3].tool_result
    assert state.best is not None and state.best.coilset.tank_center_r == 70.0

    tool_messages = [m for m in sent[1] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2", "c3", "c4", "c5"]
    assert json.loads(tool_messages[0]["content"])["valid"] is True
    assert state.status == AgentRunStatus.BUDGET_EXHAUSTED
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
    assert "round 1: {'tank_center_r': 71.0} -> misses:" in others
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


def test_candidate_solves_get_a_real_permanently_resolvable_config(monkeypatch):
    """Regression test: a candidate's config_id must be a real row in the
    configs table - not the old "agent" placeholder - so its run stays
    openable in FEMM even after the in-memory Job is gone (the bug from the
    "this run's config no longer exists" report). Also checks the name
    carries no "agent"/"candidate" wording, so it reads like an ordinary
    manually-created config."""
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
    optimize.optimize_loop(state, job_runner)

    submitted = [e for e in job_runner.events if e[0] == "submit"]
    assert submitted, "the candidate must have actually been solved (not a cache hit)"

    candidate = state.rounds[0].candidates[0]
    assert candidate.valid, "the candidate must have scored successfully"

    # The fake job runner doesn't hand back a config_id/config_name directly,
    # so recover it the way runs_panel.py's run_geometry() does once a Job's
    # in-memory object is gone: resolve straight from the configs table.
    from engine.repositories.coilset_repository import get_config, list_configs

    matches = [c for c in list_configs() if c["coilset"]["tank_center_r"] == 70.0 and c["id"] != "base_coil"]
    assert matches, "the candidate must have been saved as a real config row"
    saved = matches[0]
    assert get_config(saved["id"]) is not None, "must resolve by id, exactly like a manual run's config"
    assert "agent" not in saved["name"].lower() and "candidate" not in saved["name"].lower(), \
        f"name must look like an ordinary manual config, not {saved['name']!r}"
    print("  ok  a candidate's solve is saved as a real, resolvable config with a plain name")
