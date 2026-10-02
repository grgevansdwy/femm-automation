# =============================================================================
# test_agent_objective.py
#
# Checks the generalized objective model: agent/input_chat/models/objective.py's margin() and
# validation, agent/femm_agent/services/scorer.py's evaluate()/rank_key()/tool_result() over an
# arbitrary objective list, interview._build_spec()'s parsing, and
# variables.audit_changed_fields(). Needs no FEMM, no LLM - solves are
# replaced by a fake job runner that "finds" pre-built metrics in its cache:
#     python3 -m agent.tests.test_agent_objective  (run from backend/)
# =============================================================================
import dataclasses
import random

import pytest

from agent.input_chat.services.interview import _build_spec, _interview_tools
from agent.input_chat.models.objective import Objective, margin
from agent.femm_agent.services.prompts import system_prompt
from agent.femm_agent.services.scorer import coil_diameters, evaluate, rank_key, tool_result
from agent.input_chat.models.spec import AgentSpec
from agent.input_chat.models.variables import VariableSpec, allowed_fields, audit_changed_fields, free_fields_for
from engine.repositories.coilset_repository import get_coilset

DISTANCES = [3.0, 5.0, 8.0, 12.0]
_METRICS = ("L_loop", "L_tank", "L_rc", "M12", "M13", "M23",
            "loop_esr", "tank_esr", "rc_esr", "k12", "k13", "k23")


class _CachedRunner:
    """Every solve is a cache hit returning the metrics this candidate was built with."""
    def __init__(self, metrics_by_distance):
        self.metrics_by_distance = metrics_by_distance

    def find_cached(self, coilset, distance):
        return {"result": self.metrics_by_distance[distance]}

    def touch(self, row):
        return row


def _baseline_metrics():
    return {d: {m: 1.0 + i * 0.1 for i, m in enumerate(_METRICS)} for d in DISTANCES}


def _candidate_metrics(baseline_metrics, changes_pct):
    """changes_pct: metric -> % change, either one number (same at every
    distance) or a {distance: %} dict."""
    out = {}
    for d, row in baseline_metrics.items():
        out[d] = dict(row)
        for metric, change in changes_pct.items():
            c = change[d] if isinstance(change, dict) else change
            out[d][metric] = row[metric] * (1 + c / 100.0)
    return out


def _score(spec, changes_pct, **field_changes):
    """Scores a candidate = base_coil + field_changes, whose solve returns
    baseline metrics shifted by changes_pct."""
    baseline = get_coilset('base_coil')
    base = _baseline_metrics()
    candidate = dataclasses.replace(baseline, **field_changes)
    return evaluate(candidate, baseline, base, spec, _CachedRunner(_candidate_metrics(base, changes_pct)))


def test_margin_normalizes_by_tolerance():
    """The plan's worked example: missing a 5% band by 0.5pp is 10x worse
    than missing a 50% reduction target by 0.5pp."""
    m_gate = Objective("M23", "match", 5.0)
    esr_cut = Objective("tank_esr", "reduce", 50.0)
    assert margin(m_gate, 5.5) == pytest.approx(-0.10)
    assert margin(esr_cut, -49.5) == pytest.approx(-0.01)
    assert margin(Objective("L_loop", "match", 1.0, target_pct=-10.0), -10.5) == pytest.approx(0.5)
    assert margin(Objective("L_loop", "match", 1.0, target_pct=-10.0), -40.0) < 0, "a target is two-sided"
    assert margin(Objective("M23", "increase", 2.0), 3.0) == pytest.approx(0.5)
    # 0% tolerance normalizes against the 1pp floor instead of dividing by zero
    assert margin(Objective("rc_esr", "reduce", 0.0, required=False), 2.0) == pytest.approx(-2.0)
    print("  ok  margin() is a fraction of tolerance, two-sided for a match target, safe at 0%")


def test_objective_rejects_bad_entries():
    with pytest.raises(ValueError):
        Objective("M99", "match", 5.0)
    with pytest.raises(ValueError):
        Objective("M23", "maintain", 5.0)
    with pytest.raises(ValueError):
        Objective("M23", "match", -1.0)
    with pytest.raises(ValueError):
        Objective("M23", "match", 0.0)  # required exact match can never pass
    with pytest.raises(ValueError):
        Objective("L_loop", "reduce", 10.0, target_pct=-10.0)
    Objective("M23", "match", 0.0, required=False)  # fine as a preference
    Objective("rc_esr", "reduce", 0.0)              # "don't get worse" is a real requirement
    print("  ok  Objective() rejects unknown metrics/kinds and unpassable shapes")


def test_objective_rejects_k_as_a_target_since_its_derived_from_m_and_l():
    """k12/k13/k23 = M / sqrt(L_a * L_b) (engine/core.py) - not an independent, controllable
    quantity, so an objective can't target it even though it's a legal metric name elsewhere
    (QUANTITY_GLOSSES, for display) - see interview.py's finalize_spec for the matching
    tool-schema-level exclusion."""
    for k in ("k12", "k13", "k23"):
        with pytest.raises(ValueError, match="derived from M and L"):
            Objective(k, "match", 5.0)
    print("  ok  Objective() rejects k12/k13/k23 as an objective target")


def test_finalize_specs_objective_metric_enum_excludes_k():
    """The model can't even select k12/k13/k23 as an objective's metric in the first place -
    the tool schema's enum, not just Objective()'s validation, keeps it out."""
    tools = _interview_tools(max_distances=5)
    finalize_spec = next(t for t in tools if t["function"]["name"] == "finalize_spec")
    metric_enum = finalize_spec["function"]["parameters"]["properties"]["objectives"]["items"]["properties"]["metric"]["enum"]
    assert not {"k12", "k13", "k23"} & set(metric_enum)
    assert "M23" in metric_enum, "the underlying M is still a legal objective target"
    print("  ok  finalize_spec's objectives.metric enum excludes k12/k13/k23")


def test_goal_met_needs_every_required_objective_at_every_distance():
    spec = AgentSpec(distances=DISTANCES, objectives=[
        Objective("L_loop", "reduce", 10.0),
        Objective("L_rc", "match", 2.0),
        Objective("rc_esr", "reduce", 0.0, required=False),
    ])
    ok = _score(spec, {"L_loop": -12.0, "L_rc": 1.0, "rc_esr": 5.0})
    assert ok.goal_met, "an unmet preference must not block the goal"

    # L_rc drifts out of its band at only the closest distance - still a fail
    drifts = _score(spec, {"L_loop": -12.0, "L_rc": {3.0: 2.5, 5.0: 0.5, 8.0: 0.2, 12.0: 0.1}})
    assert not drifts.goal_met
    l_rc = drifts.objective_results[1]
    assert not l_rc.passed and l_rc.worst_distance == 3.0

    result = tool_result(drifts, spec)
    assert [o["metric"] for o in result["objectives"]] == ["L_loop", "L_rc", "rc_esr"]
    assert result["objectives"][1]["worst_change_pct"] == pytest.approx(2.5)
    assert "M23" not in {o["metric"] for o in result["objectives"]}, "no hidden M gate"
    print("  ok  goal_met = every required objective passing at every distance, nothing else")


def test_tool_result_is_json_safe_even_with_numpy_metrics():
    """Fresh solves can hand the scorer numpy numbers; the tool result is
    json.dumps()'d straight back to the model, so every pass/fail must be a
    plain bool."""
    import json

    import numpy as np

    spec = AgentSpec(distances=DISTANCES, objectives=[Objective("M12", "match", 5.0), Objective("L_loop", "reduce", 10.0)])
    baseline = get_coilset('base_coil')
    base = _baseline_metrics()
    candidate_metrics = {d: {m: np.float64(v) for m, v in r.items()}
                         for d, r in _candidate_metrics(base, {"M12": 1.0, "L_loop": -12.0}).items()}
    scored = evaluate(baseline, baseline, base, spec, _CachedRunner(candidate_metrics))
    result = tool_result(scored, spec)
    assert type(result["goal_met"]) is bool
    assert all(type(o["passed"]) is bool for o in result["objectives"])
    json.dumps(result)
    print("  ok  tool_result() stays JSON-serializable with numpy metrics")


def _legacy_rank_key(s, m_tol=5.0):
    """The scorer's ranking before the objective list, recomputed from the
    same metrics: tier 0 = M23 within tolerance everywhere, sorted by the
    least-improved coil's ESR, then tank ESR, diameters, self-L."""
    base = _baseline_metrics()
    d0 = DISTANCES[len(DISTANCES) // 2]

    def pct(metric, d):
        return (s.metrics[d][metric] - base[d][metric]) / base[d][metric] * 100.0

    m_err = [pct("M23", d) for d in DISTANCES]
    if all(abs(e) <= m_tol for e in m_err):
        esr = [pct(m, d0) for m in ("loop_esr", "tank_esr", "rc_esr")]
        # every candidate here shares base_coil's geometry, so both diameter deltas are 0
        return (0, max(esr), esr[1], 0.0, 0.0, pct("L_tank", d0), pct("L_rc", d0))
    return (1, max(abs(e) for e in m_err))


def test_default_objectives_rank_exactly_like_the_old_scorer():
    """AgentSpec()'s default objective list must reproduce the old fixed
    formula's ordering and goal_met. ESR is held distance-independent here
    (the old scorer only read it at the middle distance; the new one takes
    the worst of all distances, which agree when ESR doesn't move)."""
    rng = random.Random(1234)
    spec = AgentSpec(distances=DISTANCES)
    scored = []
    for _ in range(300):
        changes = {
            "M23": {d: rng.uniform(-8, 8) for d in DISTANCES},
            "loop_esr": rng.uniform(-25, 5), "tank_esr": rng.uniform(-25, 5), "rc_esr": rng.uniform(-25, 5),
            "L_tank": rng.uniform(-5, 5), "L_rc": rng.uniform(-5, 5),
        }
        s = _score(spec, changes)
        legacy_goal = (all(abs(changes["M23"][d]) <= 5.0 for d in DISTANCES)
                       and all(changes[m] <= -10.0 for m in ("loop_esr", "tank_esr", "rc_esr")))
        assert s.goal_met == legacy_goal
        scored.append(s)

    assert sorted(scored, key=rank_key) == sorted(scored, key=_legacy_rank_key)
    assert any(s.goal_met for s in scored) and not all(s.goal_met for s in scored), "fixture should mix both"
    print("  ok  default objectives: same goal_met and same ranking as the old scorer on 300 candidates")


def test_holding_a_match_ranks_above_breaking_it_and_match_headroom_is_not_rewarded():
    spec = AgentSpec(distances=DISTANCES, objectives=[
        Objective("L_loop", "reduce", 10.0),
        Objective("L_rc", "match", 2.0),
    ])
    held_small_cut = _score(spec, {"L_loop": -5.0, "L_rc": 1.9})
    held_big_cut = _score(spec, {"L_loop": -15.0, "L_rc": 1.9})
    dead_center_small_cut = _score(spec, {"L_loop": -12.0, "L_rc": 0.0})
    broke_band = _score(spec, {"L_loop": -30.0, "L_rc": 2.5})

    order = sorted([broke_band, dead_center_small_cut, held_small_cut, held_big_cut], key=rank_key)
    assert order == [held_big_cut, dead_center_small_cut, held_small_cut, broke_band]
    print("  ok  a held match is a gate; among holders, the bigger required cut wins regardless of match headroom")


def _finalize_args(**overrides):
    args = {
        "objective_text": "L_loop 10% lower, L_rc within 2%, don't care about ESR or M",
        "distances": [5.0],
        "objectives": [
            {"metric": "L_loop", "kind": "match", "target_pct": -10, "tolerance_pct": 1},
            {"metric": "L_rc", "kind": "match", "tolerance_pct": 2},
        ],
    }
    args.update(overrides)
    return args


def test_build_spec_takes_exactly_the_objectives_given():
    spec = _build_spec(_finalize_args(), "base_coil", get_coilset('base_coil'))
    assert spec.objectives == [
        Objective("L_loop", "match", 1.0, target_pct=-10.0),
        Objective("L_rc", "match", 2.0),
    ]
    print("  ok  _build_spec() keeps exactly the objectives the interview gave - no M23/ESR default")


def test_build_spec_rejects_bad_objective_lists():
    baseline = get_coilset('base_coil')
    with pytest.raises(KeyError):
        _build_spec({"objective_text": "x", "distances": [5.0]}, "base_coil", baseline)
    with pytest.raises(ValueError):
        _build_spec(_finalize_args(objectives=[{"metric": "M99", "kind": "match", "tolerance_pct": 5}]),
                    "base_coil", baseline)
    with pytest.raises(ValueError, match="at least one required"):
        _build_spec(_finalize_args(objectives=[{"metric": "M23", "kind": "match", "tolerance_pct": 5, "required": False}]),
                    "base_coil", baseline)
    with pytest.raises(ValueError, match="more than once"):
        _build_spec(_finalize_args(objectives=[{"metric": "M23", "kind": "match", "tolerance_pct": 5},
                                               {"metric": "M23", "kind": "increase", "tolerance_pct": 1}]),
                    "base_coil", baseline)
    print("  ok  _build_spec() rejects missing, unknown, preference-only and duplicate objectives")


def test_audit_reports_every_changed_field_and_flags_out_of_scope_ones():
    baseline = get_coilset('base_coil')
    free = free_fields_for(baseline)
    in_scope = dataclasses.replace(baseline, tank_trace_width=baseline.tank_trace_width + 0.1)
    audit = audit_changed_fields(baseline, in_scope, free)
    assert set(audit["changed_fields"]) == {"tank_trace_width"}
    assert audit["unexpected_fields"] == {}

    # tank_PCB_thickness is never free by default - a change there must surface
    out_of_scope = dataclasses.replace(in_scope, tank_PCB_thickness=baseline.tank_PCB_thickness + 0.4)
    audit = audit_changed_fields(baseline, out_of_scope, free)
    assert set(audit["changed_fields"]) == {"tank_trace_width", "tank_PCB_thickness"}
    assert set(audit["unexpected_fields"]) == {"tank_PCB_thickness"}
    print("  ok  audit_changed_fields() diffs every field and flags the ones outside free_fields")


def test_design_variables_and_diameters_are_objectives_too():
    """Objectives on a free field or a derived diameter are checked on the
    candidate's geometry, with no distance."""
    baseline = get_coilset('base_coil')
    spec = AgentSpec(distances=DISTANCES, objectives=[
        Objective("tank_trace_width", "increase", 20.0),
        Objective("tank_outer_diameter", "match", 1.0),
        Objective("num_rc_turns", "reduce", 0.0, required=False),
    ])
    wider = _score(spec, {}, tank_trace_width=baseline.tank_trace_width * 1.25)
    width, diam, turns = wider.objective_results
    assert width.passed and width.change_pct == pytest.approx(25.0)
    assert width.change_pct_by_distance is None, "a design variable has no distance"
    assert diam.value == pytest.approx(coil_diameters(wider.coilset)["tank_outer_diameter"])
    assert turns.passed and wider.goal_met == diam.passed

    result = tool_result(wider, spec)["objectives"][0]
    assert result["change_pct_by_distance"] == {} and result["value"] == pytest.approx(baseline.tank_trace_width * 1.25)
    print("  ok  free design variables and outer diameters work as objectives, with no distance")


def test_absolute_targets_and_bounds():
    at_most = Objective("L_loop", "reduce", 0.0, target_value=2e-6)
    assert margin(at_most, 0.0, value=1.9e-6) == pytest.approx(5.0)   # 5% under the bound, over the 1pp floor
    assert margin(at_most, 0.0, value=2.1e-6) < 0
    band = Objective("rc_outer_diameter", "match", 2.0, target_value=100.0)
    assert margin(band, 0.0, value=101.0) == pytest.approx(0.5)
    assert band.describe() == "within +/-2% of 100" and at_most.describe() == "<= 2e-06"
    with pytest.raises(ValueError):
        Objective("L_loop", "reduce", 5.0, target_value=2e-6)          # a bound has no tolerance
    with pytest.raises(ValueError):
        Objective("L_loop", "match", 5.0, target_pct=-10, target_value=2e-6)
    with pytest.raises(ValueError):
        Objective("L_loop", "match", 5.0, target_value=0.0)
    print("  ok  target_value gives absolute bands (match) and bounds (reduce/increase)")


def test_build_spec_pins_values_expands_features_and_checks_quantities_are_searchable():
    baseline = get_coilset('base_coil')
    spec = _build_spec(_finalize_args(
        fixed_values={"rc_trace_thickness": "0.14", "num_tank_turns": 3.0},
        excluded_fields=["tank_ferrite"],
        objectives=[{"metric": "L_loop", "kind": "match", "tolerance_pct": 2},
                    {"metric": "tank_turn_spacing", "kind": "reduce", "tolerance_pct": 10}],
    ), "base_coil", baseline)
    vs = spec.variable_spec
    assert vs.fixed_values == {"rc_trace_thickness": 0.14, "num_tank_turns": 3}
    assert isinstance(vs.fixed_values["num_tank_turns"], int)
    assert {"tank_ferrite_present", "tank_mu"} <= vs.excluded_fields
    free = free_fields_for(baseline, vs)
    assert "rc_trace_thickness" not in free and "tank_mu" not in free
    assert {"rc_trace_thickness", "num_tank_turns"} <= allowed_fields(baseline, vs)

    def rejects(match, **args):
        with pytest.raises(ValueError, match=match):
            _build_spec(_finalize_args(**args), "base_coil", baseline)

    rejects("pinned", fixed_values={"loop_r": 60},
            objectives=[{"metric": "loop_r", "kind": "reduce", "tolerance_pct": 5}])
    rejects("isn't free", objectives=[{"metric": "tank_PCB_thickness", "kind": "reduce", "tolerance_pct": 5}])
    rejects("neither a CoilSet field nor a consent group", excluded_fields=["tank_feritte"])
    rejects("must be one of", fixed_values={"rc_trace_thickness": 0.1})
    print("  ok  _build_spec() pins fixed_values, expands feature exclusions, rejects unsearchable objectives")


def test_system_prompt_is_computed_from_the_run_not_hardcoded():
    baseline = get_coilset('base_coil')
    # base_coil has ferrite fitted, but that alone no longer frees it (see
    # test_agent_variables.py) - the ferrite physics note only shows up once
    # the run's own spec actually consents to it.
    spec = AgentSpec(baseline_config_id="base_coil", distances=[2.0, 20.0],
                     objectives=[Objective("L_rc", "increase", 5.0)],
                     variable_spec=VariableSpec(enabled_groups={"tank_ferrite", "rc_ferrite"}))
    free = free_fields_for(baseline, spec.variable_spec)
    prompt = system_prompt(baseline, free, spec, _baseline_metrics())
    assert f"loop {baseline.loop_r:g}mm" in prompt and "[2.0, 20.0] mm" in prompt
    assert "56-73" not in prompt and "M23" not in prompt.split("Physics reference")[1], \
        "no baseline- or objective-specific leftovers in the physics reference"
    assert "Ferrite (" in prompt, "the run's spec consented to ferrite, so its note appears"
    assert "tie-breaker" not in prompt.lower()
    print("  ok  system_prompt() derives radii/distances/feature notes from the run's own consent, not hardcoded")


if __name__ == '__main__':
    print("checking the generalized objective model")
    test_margin_normalizes_by_tolerance()
    test_objective_rejects_bad_entries()
    test_goal_met_needs_every_required_objective_at_every_distance()
    test_default_objectives_rank_exactly_like_the_old_scorer()
    test_holding_a_match_ranks_above_breaking_it_and_match_headroom_is_not_rewarded()
    test_build_spec_takes_exactly_the_objectives_given()
    test_build_spec_rejects_bad_objective_lists()
    test_audit_reports_every_changed_field_and_flags_out_of_scope_ones()
    test_design_variables_and_diameters_are_objectives_too()
    test_absolute_targets_and_bounds()
    test_build_spec_pins_values_expands_features_and_checks_quantities_are_searchable()
    test_system_prompt_is_computed_from_the_run_not_hardcoded()
    print("all objective-model checks passed")
