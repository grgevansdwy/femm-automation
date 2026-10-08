# =============================================================================
# test_agent_search_fields.py
#
# The guardrails around a proposal (SPEC-DETAILED 4.6, 4.7): a field the Search
# fields table closes can't be proposed, a value outside a limit never reaches
# the solver, invalid geometry is caught before a solve is queued, and every
# rejection reaches the model in one fixed format with no exception text.
# Fake LLM, fake job runner, isolated database.
# =============================================================================
import dataclasses
import json

import pytest

from agent.femm_agent.models.schema import REJECTION_DETAIL, Rejection, build_tools, coerce_delta, limit_text
from agent.femm_agent.models.state import AgentRunState
from agent.femm_agent.services import optimize
from agent.femm_agent.services.optimize import search_memory
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.input_chat.models.variables import (
    NUMERIC_FIELDS, VariableSpec, allowed_fields, audit_changed_fields, free_fields_for, search_field_table,
    stored_allowed_fields,
)
from agent.settings.settings import AgentSettings
from engine.repositories.coilset_repository import get_coilset

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": None, "k13": None, "k23": None}
_RESULT_KEYS = {"valid", "error", "field", "allowed", "detail"}


class _Job:
    def __init__(self, n, result=None, error=None):
        self.id, self.result, self.error = f"job{n}", result, error
        self.status = "failed" if error else "done"


class _CountingJobs:
    """Counts fresh solves by the coilset submitted; `errors` maps a
    tank_center_r to the repr(exc) text its job fails with."""

    def __init__(self, errors=None):
        self.submitted, self._errors = [], errors or {}

    def find_cached(self, coilset, distance):
        return None

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.submitted.append(coilset)
        error = self._errors.get(coilset.tank_center_r)
        return _Job(len(self.submitted), None if error else dict(_METRICS), error)


def _settings(**rows):
    return AgentSettings(search_fields=rows)


def _prepare(arguments, settings=None, variable_spec=None, baseline=None, name="propose_coilset", index=0):
    baseline = baseline or get_coilset("base_coil")
    variable_spec = variable_spec or VariableSpec()
    table = search_field_table(settings or AgentSettings())
    return optimize._prepare({"id": "x", "function": {"name": name, "arguments": arguments}}, index, 5, baseline,
                             variable_spec.fixed_values, free_fields_for(baseline, variable_spec, table),
                             allowed_fields(baseline, variable_spec, table))


def _args(**values):
    return {"rationale": "x", **values}


# ---- the table decides what may be proposed ----------------------------------------------------

def test_a_disabled_field_is_absent_from_the_tool_schema_and_rejected_in_code():
    baseline = get_coilset("base_coil")
    settings = _settings(tank_trace_width={"enabled": False, "min": None, "max": None})
    free = free_fields_for(baseline, None, search_field_table(settings))
    properties = build_tools(free, "base_coil")[0]["function"]["parameters"]["properties"]
    assert "tank_trace_width" not in properties and "tank_mu" not in properties
    assert properties["num_tank_layers"]["maximum"] == 8, "the limit travels in the schema the model sees"

    p = _prepare(_args(tank_trace_width=1.0), settings)
    assert p.rejection.code == "not_enabled" and p.rejection.field == "tank_trace_width" and p.candidate is None
    assert _prepare(_args(tank_mu=900)).rejection.code == "not_enabled", "ferrite ships closed"
    assert _prepare(_args(rc_ferrite_y_offset=1.0)).rejection.code == "not_enabled"


def test_unticking_a_field_means_no_candidate_can_change_it():
    """N1 under the table: even if a closed field got past coerce_delta, the
    audit against the baseline flags it."""
    baseline = get_coilset("base_coil")
    settings = _settings(loop_r={"enabled": False, "min": None, "max": None})
    allowed = allowed_fields(baseline, VariableSpec(), search_field_table(settings))
    sneaked = dataclasses.replace(baseline, loop_r=baseline.loop_r + 1, tank_center_r=baseline.tank_center_r + 1)
    audit = audit_changed_fields(baseline, sneaked, allowed)
    assert set(audit["unexpected_fields"]) == {"loop_r"} and set(audit["changed_fields"]) == {"loop_r", "tank_center_r"}


def test_values_outside_a_limit_are_rejected_with_the_allowed_range():
    settings = _settings(tank_center_r={"enabled": True, "min": 20, "max": 80})
    low = _prepare(_args(tank_center_r=10), settings).rejection
    assert (low.code, low.field, low.allowed) == ("out_of_range", "tank_center_r", "20 to 80")
    assert _prepare(_args(tank_center_r=81), settings).rejection.code == "out_of_range"
    assert _prepare(_args(tank_center_r=80), settings).rejection is None, "a limit itself is allowed"
    assert _prepare(_args(num_tank_turns=0)).rejection.code == "out_of_range", "a count is at least 1"
    assert _prepare(_args(tank_turn_spacing=-0.1)).rejection.code == "out_of_range", "a dimension is at least 0"
    thick = _prepare(_args(rc_trace_thickness=0.1)).rejection
    assert thick.code == "not_allowed_value" and thick.allowed == "one of 0.035, 0.07, 0.14"


def test_layers_are_capped_at_8_until_the_user_clears_the_limit():
    assert _prepare(_args(num_tank_layers=12)).rejection.code == "out_of_range"
    assert _prepare(_args(num_tank_layers=8)).rejection is None
    cleared = _settings(num_tank_layers={"enabled": True, "min": None, "max": None})
    assert _prepare(_args(num_tank_layers=12), cleared).rejection is None


def test_string_numbers_are_coerced_and_non_numbers_are_a_wrong_type():
    assert _prepare(_args(num_tank_turns="5")).delta == {"num_tank_turns": 5}
    for bad in ("many", None, [3], 2.5, True, float("nan"), "inf"):
        assert _prepare(_args(num_tank_turns=bad)).rejection.code == "wrong_type", bad


# ---- geometry is checked before anything is queued ---------------------------------------------

def test_invalid_geometry_is_rejected_without_a_single_solve(monkeypatch):
    baseline = get_coilset("base_coil")
    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)])
    state = AgentRunState(id="g1", spec=spec, baseline_coilset=baseline)
    jobs = _CountingJobs()
    reply = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "propose_coilset",
                                                      "arguments": _args(tank_center_r=0.5)}}]}  # winding crosses the axis
    monkeypatch.setattr(optimize, "call_llm", lambda messages, tools, **kwargs: reply)
    optimize.optimize_loop(state, jobs)

    assert all(c is not None and c.tank_center_r == baseline.tank_center_r for c in jobs.submitted), \
        "only the baseline was ever solved"
    (candidate,) = state.rounds[0].candidates
    assert not candidate.valid and candidate.error_code == "invalid_geometry"
    assert candidate.candidate_coilset["tank_center_r"] == 0.5, "the geometry is still kept for the GUI to draw"
    assert "tank" in candidate.error and "too large for a center radius" in candidate.error


# ---- the fixed error format ---------------------------------------------------------------------

def _all_rejections(monkeypatch):
    """One rejection per code a search can produce today, as (code, tool result)."""
    baseline = get_coilset("base_coil")
    prepared = {
        "over_candidate_limit": _prepare(_args(tank_center_r=70.0), index=5),
        "unknown_tool": _prepare(_args(tank_center_r=70.0), name="/etc/passwd"),
        "bad_arguments": _prepare(["not", "an", "object"]),
        "unknown_field": _prepare(_args(**{"../../secret": 1})),
        "not_enabled": _prepare(_args(tank_mu=900)),
        "wrong_type": _prepare(_args(num_tank_turns="RuntimeError('boom')")),
        "out_of_range": _prepare(_args(num_tank_layers=16)),
        "not_allowed_value": _prepare(_args(loop_trace_thickness=0.5)),
        "no_change": _prepare(_args(tank_center_r=baseline.tank_center_r)),
        "invalid_geometry": _prepare(_args(tank_center_r=0.5)),
    }
    results = {code: p.rejection.as_result() for code, p in prepared.items()}

    spec = dataclasses.replace(AgentSpec(baseline_config_id="base_coil"), distances=[10.0], max_tool_calls=1,
                               objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)])
    state = AgentRunState(id="e1", spec=spec, baseline_coilset=baseline)
    jobs = _CountingJobs(errors={
        71.0: "RuntimeError('femmcli failed (exit 1):\\n/tmp/xfemm_job_ab12/model.lua: Traceback ...')",
        72.0: "SolverTimeout('femmcli did not finish within 120 s')",
    })
    reply = {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{r}", "type": "function", "function": {"name": "propose_coilset", "arguments": _args(tank_center_r=r)}}
        for r in (71.0, 72.0)]}
    sent = []

    def fake_call_llm(messages, tools, **kwargs):
        sent.append(messages)
        return reply

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, jobs)
    crashed, timed_out = state.rounds[0].candidates
    assert (crashed.error_code, timed_out.error_code) == ("solver_failed", "solver_timeout")
    results["solver_failed"] = Rejection(crashed.error_code).as_result()
    results["solver_timeout"] = Rejection(timed_out.error_code).as_result()
    assert "/tmp" not in json.dumps([dataclasses.asdict(c) for c in state.rounds[0].candidates])
    return results


def test_every_rejection_has_exactly_the_five_keys_and_a_known_code(monkeypatch):
    results = _all_rejections(monkeypatch)
    # unknown_base belongs to the `base` argument (M4a). The three limit codes are
    # raised when a budget ends a round (test_agent_budget.py, test_agent_retry.py).
    assert set(results) == set(REJECTION_DETAIL) - {"unknown_base", "simulation_limit", "time_limit", "search_stopped"}
    for code, result in results.items():
        assert set(result) == _RESULT_KEYS and result["valid"] is False and result["error"] == code, code
        json.dumps(result)


def test_no_rejection_carries_a_path_an_exception_or_the_models_own_text(monkeypatch):
    for code, result in _all_rejections(monkeypatch).items():
        text = json.dumps(result)
        for leak in ("/tmp", "/home", "Traceback", "Error(", "Exception", "passwd", "boom", "femmcli", ".py"):
            assert leak not in text, (code, leak, text)


def test_rejection_as_a_valueerror_reads_as_one_line_for_a_person():
    rejection = Rejection("out_of_range", "num_tank_layers", "1 to 8")
    assert isinstance(rejection, ValueError)
    assert str(rejection) == "num_tank_layers: The value is outside the allowed range for this field. Allowed: 1 to 8."


def test_limit_text_wording():
    assert limit_text({"minimum": 1, "maximum": 8}) == "1 to 8"
    assert limit_text({"minimum": 0.0}) == "at least 0"
    assert limit_text({"maximum": 5}) == "at most 5"
    assert limit_text({"enum": [0, 1]}) == "one of 0, 1"
    assert limit_text({}) == "any value"


def test_the_tried_list_names_a_rejection_by_code_field_and_limit():
    from agent.femm_agent.models.state import Tried
    rejected = Tried(1, {"num_tank_layers": 16}, None, Rejection("out_of_range", "num_tank_layers", "1 to 8").as_result())
    memory = search_memory([rejected], latest_round=2, keep_best=3)
    assert "rejected: out_of_range num_tank_layers (allowed: 1 to 8)" in memory


def test_coerce_delta_without_a_known_field_list_calls_everything_unknown():
    with pytest.raises(Rejection) as caught:
        coerce_delta({"tank_mu": 1}, {})
    assert caught.value.code == "unknown_field"
    with pytest.raises(Rejection) as caught:
        coerce_delta({"tank_mu": 1}, {}, NUMERIC_FIELDS)
    assert caught.value.code == "not_enabled"


# ---- the run uses its own copy of the table -----------------------------------------------------

def test_a_search_reads_the_table_from_its_settings_snapshot_not_the_saved_settings(monkeypatch):
    from agent.settings.settings import save_settings
    baseline = get_coilset("base_coil")
    snapshot = _settings(tank_mu={"enabled": True, "min": None, "max": None})
    spec = dataclasses.replace(AgentSpec.from_settings(snapshot, baseline_config_id="base_coil"), distances=[10.0],
                               max_tool_calls=1, objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)])
    save_settings(AgentSettings())  # the saved table closes tank_mu; the run's copy opens it
    state = AgentRunState(id="s1", spec=spec, baseline_coilset=baseline)
    offered = []

    def fake_call_llm(messages, tools, **kwargs):
        offered.append(set(tools[0]["function"]["parameters"]["properties"]))
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "propose_coilset", "arguments": _args(tank_mu=900)}}]}

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    optimize.optimize_loop(state, _CountingJobs())
    assert "tank_mu" in offered[0] and state.rounds[0].candidates[0].valid


# ---- runs stored before the table ---------------------------------------------------------------

def test_an_old_stored_spec_with_enabled_groups_still_audits(monkeypatch):
    baseline = get_coilset("base_coil")
    old = {"variable_spec": {"enabled_groups": ["tank_ferrite"], "excluded_fields": ["loop_r"], "fixed_values": {}},
           "settings": {"max_tool_calls": 50}}
    allowed = stored_allowed_fields(baseline, old)
    assert "tank_mu" in allowed and "rc_ferrite_y_offset" in allowed and "loop_r" not in allowed
    new = {"variable_spec": {"excluded_fields": []}, "settings": {"search_fields": {}}}
    allowed = stored_allowed_fields(baseline, new)
    assert "tank_mu" not in allowed and "rc_ferrite_y_offset" not in allowed and "loop_r" in allowed
    assert stored_allowed_fields(baseline, {}) >= {"loop_r"}, "a spec with nothing stored falls back to the old rule"
