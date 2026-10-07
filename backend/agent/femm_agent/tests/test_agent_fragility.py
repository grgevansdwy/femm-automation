# =============================================================================
# test_agent_fragility.py
#
# The fragility check (services/fragility.py, SPEC-DETAILED 4.8): a design that
# meets the goal only wins if four copies of it, with every continuous
# dimension changed together by -2%, -1%, +1% and +2%, still meet it.
# End to end through AgentRunner with a scripted fake LLM, a counting fake job
# runner and a clock moved by hand - no server, no FEMM, no sleeping.
#
# The fake solver's tank ESR scales with tank_center_r (base 72.688), so with
# "cut tank ESR by 10%" a design passes at r <= 65.419: r = 65.4 passes by a
# hair and its +1% copy does not; r = 60 passes with room to spare.
# =============================================================================
import dataclasses
import time

import pytest

from agent.femm_agent.services import fragility, optimize
from agent.femm_agent.services.restore import restore_run
from agent.femm_agent.services.runner import AgentRunner
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.repositories.ai_run_repository import get_ai_run
from agent.services.llm_client import LLMError
from agent.services.run_log import read_log
from agent.settings.settings import AgentSettings
from engine.models.coilset import CoilSet
from engine.repositories.coilset_repository import get_coilset

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": 0.6, "k13": 0.2, "k23": 0.6}
_BASE_R = 72.688
_BARELY, _ROOMY = 65.4, 60.0


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Job:
    def __init__(self, result, job_id, error=None):
        self.result, self.id, self.error = result, job_id, error
        self.status = "failed" if error else "done"


class _Jobs:
    """Tank ESR scales with tank_center_r. Solves are cached. `crash`: radii
    whose solve fails. `solve_s`: how far each fresh solve moves the clock."""

    def __init__(self, clock=None, solve_s=0.0, crash=()):
        self.clock, self.solve_s, self.crash = clock, solve_s, set(crash)
        self.cache: dict = {}
        self.submits: list = []  # (coilset, distance, clock time when queued)

    def find_cached(self, coilset, distance):
        return self.cache.get((coilset.tank_center_r, distance))

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.submits.append((coilset, distance, self.clock.t if self.clock else None))
        if self.clock is not None:
            self.clock.t += self.solve_s
        if coilset.tank_center_r in self.crash:
            return _Job(None, f"job{len(self.submits)}", error="RuntimeError('femmcli failed: /tmp/xfemm_job_1/model.lua')")
        result = {**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / _BASE_R}
        self.cache[(coilset.tank_center_r, distance)] = {"result": result}
        return _Job(result, f"job{len(self.submits)}")

    def cancel(self, job_id):
        return False

    def radii(self):
        return [round(c.tank_center_r, 4) for c, _, _ in self.submits if c.tank_center_r != _BASE_R]


def _propose(*radii):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{r}", "type": "function", "function": {"name": "propose_coilset",
                                                         "arguments": {"tank_center_r": r, "rationale": "try it"}}}
        for r in radii]}


def _text():
    return {"role": "assistant", "content": "Nothing more to try."}


def _spec(per_round=1, distances=(10.0,), **settings):
    s = AgentSettings(**settings)
    return dataclasses.replace(
        AgentSpec.from_settings(s, baseline_config_id="base_coil"), distances=list(distances),
        candidates_per_round=per_round,
        objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=10.0)])


def _wait(state):
    deadline = time.monotonic() + 20
    while (get_ai_run(state.id) or {}).get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return get_ai_run(state.id)


def _search(monkeypatch, script, spec, jobs=None, clock=None, request_s=0.0):
    """script: one entry per AI request (a reply or an exception); past its
    end the model answers with plain text. Returns (state, row, requests, jobs)."""
    requests = []

    def fake_call_llm(messages, tools, **kwargs):
        requests.append((messages, clock.t if clock else None))
        if clock is not None:
            clock.t += request_s
        step = script[len(requests) - 1] if len(requests) <= len(script) else _text()
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    monkeypatch.setattr(optimize, "RETRY_PAUSE_S", 0.0)
    jobs = jobs or _Jobs(clock)
    runner = AgentRunner(jobs, **({"clock": clock} if clock else {}))
    state = runner.submit(spec)
    row = _wait(state)
    state.runner = runner
    return state, row, requests, jobs


def _copy_radii(r):
    return [float(f"{r * (1 + pct / 100):.6g}") for pct in fragility.NUDGES]


# ---- the copies ---------------------------------------------------------------------------------

def test_copies_scale_every_dimension_together_and_leave_everything_else_alone():
    baseline = get_coilset("base_coil")
    copies = fragility.nudged_copies(baseline)
    assert [pct for pct, _ in copies] == [-2.0, -1.0, 1.0, 2.0]
    untouched = {f.name for f in dataclasses.fields(CoilSet)} - set(fragility.nudged_fields())
    assert {"num_tank_turns", "num_tank_layers", "num_parallel_rc_traces", "tank_ferrite_present", "rc_shield",
            "tank_mu", "rc_mu", "rc_loss_angle", "tc_loss_angle", "tank_trace_thickness", "rc_trace_thickness",
            "config_name"} <= untouched, "counts, flags, permeability, loss angle and copper thickness are not nudged"
    assert {"tank_center_r", "tank_trace_width", "tank_turn_spacing", "loop_offset", "rc_PCB_thickness",
            "tank_ferrite_outer_r", "rc_shield_radius", "backplane_y_distance"} <= set(fragility.nudged_fields())
    for pct, copy in copies:
        for name in untouched:
            assert getattr(copy, name) == getattr(baseline, name), (pct, name)
        for name in fragility.nudged_fields():
            assert getattr(copy, name) == pytest.approx(getattr(baseline, name) * (1 + pct / 100), rel=1e-5), (pct, name)
    assert fragility.nudged_copies(baseline) == copies, "the same design always gives the same copies"


def test_reserves_scale_with_the_search_and_never_swallow_a_small_budget():
    one, three = _spec(), _spec(distances=(5.0, 10.0, 15.0))
    assert fragility.simulation_reserve(one, 300) == 4 and fragility.simulation_reserve(three, 300) == 12
    assert fragility.simulation_reserve(three, 10) == 5, "never more than half the budget"
    assert fragility.time_reserve(5400) == 240 and fragility.time_reserve(400) == 100


# ---- N2: a pass that does not survive is not a pass ----------------------------------------------

def test_a_design_that_passes_by_a_hair_and_fails_its_copy_is_not_the_winner(monkeypatch):
    state, row, requests, jobs = _search(monkeypatch, [_propose(_BARELY)], _spec())

    (candidate,) = state.rounds[0].candidates
    assert candidate.valid and candidate.tool_result["goal_met"] is True, "it does meet the goal as simulated"
    assert candidate.tool_result["fragility"] == "failed at +1%: tank_esr -9.1%"
    assert state.winner is None and state.best is None
    assert (row["status"], row["stop_reason"]) == ("stopped_by_model", "model_stopped"), "not goal_met"
    assert row["best_coilset"] is None, "a design that failed the check is never saved as the result"
    (check,) = row["fragility"]
    assert check["passed"] is False and [n["passed"] for n in check["nudges"]] == [True, True, False, False]
    assert "Target not achieved" in state.outcome_report
    assert "1 design(s) met the goal but failed the fragility check" in state.outcome_report
    assert "none is presented as a winner" in state.outcome_report and "fragile" not in state.outcome_report.replace("fragility", "")


def test_a_design_with_margin_survives_and_wins_and_a_worse_one_is_not_checked(monkeypatch):
    state, row, _, jobs = _search(monkeypatch, [_propose(_BARELY, _ROOMY)], _spec(per_round=2))

    assert state.winner is not None and state.winner.coilset.tank_center_r == _ROOMY
    assert (row["status"], row["best_coilset"]["tank_center_r"]) == ("goal_met", _ROOMY)
    assert jobs.radii() == [_BARELY, _ROOMY] + _copy_radii(_ROOMY), "65.4 ranks below the winner: no check spent on it"
    (check,) = row["fragility"]
    assert check["passed"] and check["summary"] == "passed" and len(check["nudges"]) == 4
    assert "fragility check" in state.outcome_report and state.outcome_report.startswith("Goal met")
    roomy = state.rounds[0].candidates[1]
    assert roomy.tool_result["fragility"] == "passed" and "fragility" not in state.rounds[0].candidates[0].tool_result


def test_when_the_best_design_fails_the_next_ranked_one_is_checked(monkeypatch):
    # 60's +2% copy crashes the solver: 60 fails its check, and 62 (next best) is checked and wins.
    jobs = _Jobs(crash={_copy_radii(_ROOMY)[3]})
    state, row, _, _ = _search(monkeypatch, [_propose(_ROOMY, 62.0)], _spec(per_round=2), jobs=jobs)

    assert row["status"] == "goal_met" and state.winner.coilset.tank_center_r == 62.0
    failed, passed = row["fragility"]
    assert (failed["delta"], failed["passed"], failed["summary"]) == ({"tank_center_r": _ROOMY}, False, "failed at +2%: solver failed")
    assert passed["delta"] == {"tank_center_r": 62.0} and passed["passed"]
    assert "/tmp" not in str(row["fragility"]), "the solver's own text stays in the run log"
    (warning,) = [e for e in read_log(state.log.path) if e["event"] == "warning"]
    assert warning["where"] == "fragility.check_design" and "femmcli failed" in warning["exception"]


def test_five_failures_send_the_search_on_with_one_memory_line_each(monkeypatch):
    barely = [65.40, 65.39, 65.38, 65.37, 65.36, 65.35]
    state, row, requests, jobs = _search(monkeypatch, [_propose(*barely)], _spec(per_round=6))

    # Round 1 passes six times by a hair; two silent rounds end it; five are checked and fail,
    # the search carries on, and when it ends again the sixth is checked too.
    assert len(row["fragility"]) == 6 and not any(c["passed"] for c in row["fragility"])
    assert row["status"] != "goal_met" and state.winner is None
    resumed = next(messages for messages, _ in requests if any("Fragility check:" in str(m.get("content")) for m in messages))
    note = next(m["content"] for m in resumed if "Fragility check:" in str(m.get("content")))
    assert note.count("failed at") == fragility.MAX_FAILED_PER_EPISODE == 5
    later = requests[requests.index(next(r for r in requests if r[0] is resumed)) + 1][0]
    memory = next(m["content"] for m in later if "failed the fragility check" in str(m.get("content")))
    assert memory.count("failed at") == 5, "one line per failed design, in the rebuilt memory"
    assert "Best " not in memory.split("Met the goal but failed")[0] or "65.3" not in memory.split("Met the goal but failed")[0]
    assert "6 design(s) met the goal but failed the fragility check" in state.outcome_report


def test_with_no_budget_left_the_run_ends_without_a_winner_and_says_so(monkeypatch):
    state, row, requests, _ = _search(monkeypatch, [_propose(_BARELY)], _spec(max_tool_calls=1))

    assert (row["status"], row["stop_reason"]) == ("budget_exhausted", "rounds") and len(requests) == 1
    assert state.outcome_report.startswith("Target not achieved: the search used all 1 rounds")
    assert "met the goal but failed the fragility check" in state.outcome_report


def test_a_design_is_never_checked_twice_even_across_a_continue(monkeypatch):
    state, row, _, jobs = _search(monkeypatch, [_propose(_BARELY)], _spec())
    assert len(state.fragility) == 1
    fresh_before = len(jobs.submits)

    state.runner.continue_run(state.id)  # the scripted model only replies with text now
    row = _wait(state)
    assert len(state.fragility) == 1 and len(row["fragility"]) == 1
    assert len(jobs.submits) == fresh_before, "no solve at all: nothing new was tried, nothing was checked again"


# ---- budget: the check is paid for, and there is always room for it ------------------------------

def test_check_solves_count_against_the_budget_and_cache_hits_do_not(monkeypatch):
    state, row, _, jobs = _search(monkeypatch, [_propose(_ROOMY)], _spec())
    assert row["status"] == "goal_met"
    assert state.budget.simulations_used == 5, "one candidate + its four copies; the baseline is free"

    # The same design proposed again in a new search on the same solver: everything is cached.
    again, row, _, _ = _search(monkeypatch, [_propose(_ROOMY)], _spec(), jobs=jobs)
    assert row["status"] == "goal_met" and again.budget.simulations_used == 0 and again.budget.cache_hits == 5


def test_the_simulation_reserve_leaves_room_for_one_check(monkeypatch):
    # 10 simulations, one distance: 4 are held back, so the search stops after 6 designs.
    improving = [_propose(r) for r in (60.0, 59.0, 58.0, 57.0, 56.0, 55.0, 54.0, 53.0)]
    state, row, requests, jobs = _search(monkeypatch, improving, _spec(max_simulations=10, plateau_rounds=20))

    assert (row["status"], row["stop_reason"]) == ("goal_met", "simulations")
    assert jobs.radii() == [60.0, 59.0, 58.0, 57.0, 56.0, 55.0] + _copy_radii(55.0)
    assert state.budget.simulations_used == 10 and state.winner.coilset.tank_center_r == 55.0
    assert row["budget"][0]["max_simulations"] == 10, "the limit reported is the user's, not the limit less the reserve"


def test_the_time_reserve_stops_the_search_early_enough_to_check_inside_the_limit(monkeypatch):
    clock = _Clock()
    improving = [_propose(r) for r in (60.0, 59.0, 58.0, 57.0, 56.0, 55.0)]
    spec = _spec(plateau_rounds=20)  # 5400 s limit, 240 s held back once a design has passed
    state, row, requests, jobs = _search(monkeypatch, improving, spec, clock=clock, request_s=1290.0)

    # Requests start at 0, 1290, 2580, 3870; at 5160 only the reserve is left, so no fifth starts.
    assert [started for _, started in requests] == [0.0, 1290.0, 2580.0, 3870.0]
    assert (row["status"], row["stop_reason"]) == ("goal_met", "time")
    # The fourth reply lands at 5160, when the search's own time is up: its design (57) is not simulated.
    assert state.rounds[3].candidates[0].error_code == "time_limit"
    assert state.winner.coilset.tank_center_r == 58.0
    assert all(queued_at < 5400.0 for _, _, queued_at in jobs.submits), "N3: no solve starts after the limit"
    assert [queued_at for c, _, queued_at in jobs.submits if round(c.tank_center_r, 4) in _copy_radii(58.0)] == [5160.0] * 4


def test_no_time_reserve_is_held_before_any_design_passes(monkeypatch):
    clock = _Clock()
    spec = _spec(stall_rounds=20)
    state, row, requests, _ = _search(monkeypatch, [_propose(r) for r in (71.0, 70.5, 70.0, 69.5, 69.0, 68.5)], spec,
                                      clock=clock, request_s=1290.0)
    assert [started for _, started in requests] == [0.0, 1290.0, 2580.0, 3870.0, 5160.0], "the full limit is used"
    assert row["stop_reason"] == "time" and row["fragility"] is None


def test_a_pass_that_cannot_be_checked_in_time_is_reported_and_continue_starts_with_the_check(monkeypatch):
    clock = _Clock()
    jobs = _Jobs(clock, solve_s=300.0)  # a slow solver: the reserve is not enough for four copies
    spec = _spec(plateau_rounds=20)
    state, row, requests, _ = _search(monkeypatch, [_propose(r) for r in (60.0, 59.0, 58.0, 57.0)], spec,
                                      jobs=jobs, clock=clock, request_s=1000.0)

    assert (row["status"], row["stop_reason"]) == ("budget_exhausted", "time") and state.winner is None
    assert state.unchecked_pass and row["fragility"] is None
    assert 'reply "continue" to check it' in state.outcome_report
    assert "failed the fragility check" not in state.outcome_report, "nothing failed: it was never checked"
    assert all(queued_at < 5400.0 for _, _, queued_at in jobs.submits)

    jobs.solve_s, asked_before = 0.0, len(requests)
    state.runner.continue_run(state.id)
    row = _wait(state)
    assert row["status"] == "goal_met" and state.winner is not None and not state.unchecked_pass
    first_new = next(i for i, (c, _, _) in enumerate(jobs.submits) if c.tank_center_r in _copy_radii(state.winner.coilset.tank_center_r))
    assert len(requests) > asked_before and row["fragility"][0]["passed"]
    assert all(c.tank_center_r in _copy_radii(state.winner.coilset.tank_center_r) for c, _, _ in jobs.submits[first_new:first_new + 3])


# ---- G9: a copy is fabrication error, not a proposal ---------------------------------------------

def test_a_copy_outside_a_search_fields_limit_is_still_simulated(monkeypatch):
    spec = _spec(search_fields={"tank_center_r": {"enabled": True, "min": None, "max": 60.0}})
    state, row, _, jobs = _search(monkeypatch, [_propose(_ROOMY)], spec)
    assert row["status"] == "goal_met"
    assert max(jobs.radii()) == pytest.approx(61.2), "the +2% copy is past the limit of 60 and was solved anyway"


def test_a_copy_with_invalid_geometry_fails_the_check_without_a_solve(monkeypatch):
    real = fragility.validate_coilset
    monkeypatch.setattr(fragility, "validate_coilset",
                        lambda coilset: [("tank", "too large")] if coilset.tank_center_r > 61.0 else real(coilset))
    state, row, _, jobs = _search(monkeypatch, [_propose(_ROOMY)], _spec(max_tool_calls=1))

    (check,) = row["fragility"]
    assert not check["passed"] and check["summary"] == "failed at +2%: invalid geometry"
    assert jobs.radii() == [_ROOMY] + _copy_radii(_ROOMY)[:3], "the invalid copy was never queued"
    assert row["status"] == "budget_exhausted"


# ---- the check needs no model --------------------------------------------------------------------

def test_a_search_that_loses_the_ai_server_after_a_pass_still_ends_goal_met(monkeypatch):
    lost = LLMError("unreachable", "connection refused", elapsed_s=0.1)
    state, row, _, _ = _search(monkeypatch, [_propose(_ROOMY)] + [lost] * 20, _spec())
    assert (row["status"], row["stop_reason"]) == ("goal_met", "llm_failed")
    assert state.winner.coilset.tank_center_r == _ROOMY and row["fragility"][0]["passed"]


def test_a_search_that_loses_the_ai_server_with_only_a_fragile_pass_is_failed_not_goal_met(monkeypatch):
    lost = LLMError("unreachable", "connection refused", elapsed_s=0.1)
    state, row, _, _ = _search(monkeypatch, [_propose(_BARELY)] + [lost] * 20, _spec())
    assert (row["status"], row["stop_reason"]) == ("failed", "llm_failed")
    assert "met the goal but failed the fragility check" in state.outcome_report


# ---- a stored run keeps its checks ---------------------------------------------------------------

def test_restore_brings_back_the_checks_and_the_winner_without_checking_again(monkeypatch):
    state, row, _, jobs = _search(monkeypatch, [_propose(_BARELY, _ROOMY)], _spec(per_round=2))
    fresh = len(jobs.submits)

    restored = restore_run(state.id, jobs)
    assert restored.winner is not None and restored.winner.coilset.tank_center_r == _ROOMY
    assert restored.best is restored.winner and restored.fragility == row["fragility"]
    assert fragility.unchecked_passing(restored) == [] and not restored.unchecked_pass
    assert len(jobs.submits) == fresh
