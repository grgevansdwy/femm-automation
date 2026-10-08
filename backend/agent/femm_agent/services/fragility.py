"""The fragility check: a design only wins if it still meets the goal when
its dimensions are a little off.

Fabrication and the solver's mesh both shift a result by a point or two, so
a pass that does not survive that is not a pass. When the search is about to
stop with passing designs it has not checked, code (never the model)
re-simulates the best one as four copies with every continuous dimension
multiplied together by 0.98, 0.99, 1.01 and 1.02. All four must meet every
required objective at every distance. A design that fails is never the
winner; the next-ranked passing design is checked instead, up to
MAX_FAILED_PER_EPISODE failures in one go, after which the search carries on
if its budget allows (optimize_loop).

The check's solves count against the search's budget like any other. So
that a check can still be afforded, the search itself stops short of the
limits by a reserve: simulation_reserve() always, time_reserve() once a
design has passed. If a passing design still cannot be checked, the run ends
without a winner and says so - a "continue" starts with the check.

A nudged copy models fabrication error, not a proposal: the Search fields
limits do not apply to it. Geometry that is no longer valid, or a solve that
fails, is a failed check for that design.
"""

import dataclasses
import json

from agent.femm_agent.services.scorer import SimulationLimit, evaluate_async, rank_key, score, tool_result, wait_for_jobs
from engine.core import validate_coilset
from engine.models.coilset import CoilSet

NUDGES = (-2.0, -1.0, 1.0, 2.0)  # percent, applied to every continuous dimension at once
MAX_FAILED_PER_EPISODE = 5       # designs that may fail the check before the search carries on
TIME_RESERVE_S = 240.0           # two solver timeouts: held back from the search once a design has passed

# Float fields that are not a length: permeability, loss angle, and copper
# thickness (which only comes in three weights). Counts and on/off flags are
# ints, so they are never nudged either.
_NOT_A_DIMENSION = frozenset({"tank_mu", "rc_mu", "rc_loss_angle", "tc_loss_angle",
                              "loop_trace_thickness", "tank_trace_thickness", "rc_trace_thickness"})


def nudged_fields() -> tuple[str, ...]:
    """Every continuous dimension of a CoilSet: its float fields, less _NOT_A_DIMENSION."""
    return tuple(f.name for f in dataclasses.fields(CoilSet) if f.type is float and f.name not in _NOT_A_DIMENSION)


def nudged_copies(coilset) -> list[tuple[float, CoilSet]]:
    """(percent, copy) for each of NUDGES: `coilset` with all its continuous
    dimensions scaled together. Rounded to 6 significant digits so the same
    design always gives the same copies (and the same cached solves)."""
    fields = nudged_fields()
    return [(pct, dataclasses.replace(coilset, **{
        name: float(f"{getattr(coilset, name) * (1 + pct / 100):.6g}") for name in fields}))
        for pct in NUDGES]


def simulation_reserve(spec, max_simulations: int) -> int:
    """Simulations the search leaves unspent so one design can be checked:
    four copies at every distance - but never more than half the budget."""
    return min(len(NUDGES) * len(spec.distances), max_simulations // 2)


def time_reserve(time_limit_s: float) -> float:
    """Seconds the search leaves unspent once a design has passed - never
    more than a quarter of its time limit."""
    return min(TIME_RESERVE_S, time_limit_s / 4)


def design_key(coilset) -> str:
    """What identifies a design across rounds, restarts and a restore."""
    data = dataclasses.asdict(coilset) if dataclasses.is_dataclass(coilset) else dict(coilset)
    return json.dumps(data, sort_keys=True, default=str)


def unchecked_passing(state) -> list:
    """The state.Tried entries still owed a check, best first: designs that
    meet the goal, have not been checked, and rank above the winner so far
    (a design worse than a winner that already survived needs no check).
    One entry per distinct design."""
    favourite = state.spec.favourite_metric
    winner_key = rank_key(state.winner, favourite) if state.winner is not None else None
    seen, owed = set(), []
    for t in sorted((t for t in state.tried if t.scored is not None and t.scored.goal_met and t.check is None),
                    key=lambda t: rank_key(t.scored, favourite)):
        key = design_key(t.scored.coilset)
        if key in seen or (winner_key is not None and rank_key(t.scored, favourite) >= winner_key):
            continue
        seen.add(key)
        owed.append(t)
    return owed


def check_summary(check: dict) -> str:
    """ "passed", or which copy broke and by how much - e.g.
    "failed at -2%: tank_esr -19.1%". The text the model and the GUI are shown."""
    if check["passed"]:
        return "passed"
    nudge = next(n for n in check["nudges"] if not n["passed"])
    where = f"failed at {nudge['pct']:+g}%"
    if nudge.get("reason"):
        return f"{where}: {nudge['reason'].replace('_', ' ')}"
    missed = [o for o in nudge["tool_result"]["objectives"] if o["required"] and not o["passed"]]
    worst = min(missed, key=lambda o: o["margin"])
    shown = f"{worst['value']:g}" if worst.get("target_value") is not None else f"{worst['worst_change_pct']:+.1f}%"
    return f"{where}: {worst['metric']} {shown}"


def check_design(tried, state, job_runner) -> dict | None:
    """Simulates one passing design's four nudged copies and judges them.
    Returns the check ({round, delta, key, passed, summary, nudges}), or None
    when it could not be completed - the budget ran out or the search was
    stopped part-way and no copy had failed yet - in which case the design
    stays unchecked. A copy that already failed settles it either way."""
    spec, budget, baseline = state.spec, state.budget, state.baseline_coilset
    nudges, launched, incomplete = [], [], False
    for pct, copy in nudged_copies(tried.scored.coilset):
        if validate_coilset(copy):
            nudges.append({"pct": pct, "passed": False, "reason": "invalid_geometry"})
            continue
        if state.cancelled or budget.remaining_s() <= 0:
            incomplete = True
            break
        try:
            launched.append((pct, copy, evaluate_async(copy, spec.distances, job_runner, budget=budget)))
        except SimulationLimit:
            incomplete = True
            break

    dropped: set[int] = set()

    def drop_queued_when_time_is_up():
        # No solve starts after the search's time limit (same rule as a round's solves).
        if budget.remaining_s() > 0:
            return
        for _, _, jobs in launched:
            for job in jobs.values():
                if (not isinstance(job, dict) and id(job) not in dropped and job.status not in ("done", "failed")
                        and job_runner.cancel(job.id)):
                    dropped.add(id(job))
                    budget.simulations_used -= 1

    for pct, copy, jobs in launched:
        try:
            metrics = wait_for_jobs(jobs, on_wait=drop_queued_when_time_is_up)
            scored = score(copy, metrics, baseline, state.baseline_metrics, spec)
            nudges.append({"pct": pct, "passed": bool(scored.goal_met), "tool_result": tool_result(scored, spec)})
        except Exception as exc:
            if state.cancelled or any(id(job) in dropped for job in jobs.values()):
                incomplete = True  # never ran: says nothing about the design
                continue
            nudges.append({"pct": pct, "passed": False, "reason": "solver_failed"})
            if state.log is not None:
                state.log.warning("fragility.check_design", f"a nudged copy ({pct:+g}%) of {tried.delta} failed to solve", exc)

    failed = any(not n["passed"] for n in nudges)
    if incomplete and not failed:
        return None
    check = {"round": tried.round_num, "delta": tried.delta, "key": design_key(tried.scored.coilset),
             "passed": not failed, "nudges": sorted(nudges, key=lambda n: n["pct"])}
    check["summary"] = check_summary(check)
    return check


def record_check(state, check: dict) -> None:
    """Attaches a check to the run and to every tried entry of that design
    (the same design can be proposed twice), and shows it on the design's own
    result, where the GUI and a resent tool result pick it up."""
    if check not in state.fragility:
        state.fragility.append(check)
    for t in state.tried:
        if t.scored is not None and t.check is None and design_key(t.scored.coilset) == check["key"]:
            t.check = check
            t.result["fragility"] = check["summary"]


@dataclasses.dataclass
class Episode:
    """One run of the check over the designs that were owed one."""
    winner_found: bool = False                                   # a design survived (state.winner is set)
    failed: list = dataclasses.field(default_factory=list)       # the checks that failed, in order
    unchecked: bool = False                                      # a passing design is still owed a check
    out_of_budget: bool = False                                  # ...because the budget could not cover it


def run_episode(state, job_runner) -> Episode:
    """Checks passing designs, best first, until one survives (it becomes
    state.winner), MAX_FAILED_PER_EPISODE have failed, none is left, or the
    budget cannot cover the next check. The search's reserve is what pays
    for it. Afterwards state.best is the winner if there is one, else the
    best design that has not failed a check. A design is never checked twice."""
    episode = Episode()
    budget = state.budget
    held = (budget.reserved_simulations, budget.reserved_s)
    budget.reserved_simulations, budget.reserved_s = 0, 0.0  # this is what the reserve was for
    try:
        while len(episode.failed) < MAX_FAILED_PER_EPISODE:
            owed = unchecked_passing(state)
            if not owed:
                break
            check = check_design(owed[0], state, job_runner)
            if check is None:
                episode.out_of_budget = True
                break
            record_check(state, check)
            if check["passed"]:
                state.winner = owed[0].scored
                episode.winner_found = True
                break
            episode.failed.append(check)
    finally:
        budget.reserved_simulations, budget.reserved_s = held
    episode.unchecked = bool(unchecked_passing(state)) and not episode.winner_found
    state.unchecked_pass = episode.unchecked
    refresh_best(state)
    return episode


def refresh_best(state) -> None:
    """state.best = the winner, else the best-ranked design that has not failed a check."""
    if state.winner is not None:
        state.best = state.winner
        return
    favourite = state.spec.favourite_metric
    standing = [t.scored for t in state.tried if t.scored is not None and not t.check_failed]
    state.best = min(standing, key=lambda s: rank_key(s, favourite)) if standing else None


def best_result(state) -> dict | None:
    """state.best as the tool-result dict shown for the run's proposed design,
    marked "fragility": "passed" when it is the winner. None without a best."""
    if state.best is None:
        return None
    result = tool_result(state.best, state.spec)
    if state.winner is not None and state.best is state.winner:
        result["fragility"] = "passed"
    return result


def resume_note(failed: list) -> str:
    """What code tells the model when designs passed but none survived and
    the search carries on: what was measured, nothing about what to do with it
    beyond needing more margin."""
    lines = ["Fragility check: a design only counts if it still meets the goal with every dimension "
             "changed together by -2%, -1%, +1% and +2%. These met the goal but did not survive it:"]
    lines += [f"  round {c['round']}: {c['delta']} -> {c['summary']}" for c in failed]
    lines.append("None of them is a winner. Keep searching for a design that clears its targets with more margin.")
    return "\n".join(lines)
