"""Turns one simulate() result into a pass/fail + ranking. Plain code, never
left to the LLM's judgment."""

import dataclasses
import time

from agent.objective import Objective, is_per_distance, margin


@dataclasses.dataclass
class ObjectiveResult:
    """One Objective checked against one candidate. A simulated quantity is
    checked at every distance and the worst distance decides; a design
    variable or derived geometry value has no distance."""
    objective: Objective
    change_pct: float               # % change vs baseline (at the worst distance, if per-distance)
    value: float                    # the raw value there
    baseline_value: float
    margin: float                   # as a fraction of tolerance (see objective.margin())
    passed: bool                    # margin >= 0 (at every distance, if per-distance)
    change_pct_by_distance: dict | None = None  # distance -> % change, per-distance quantities only
    worst_distance: float | None = None


@dataclasses.dataclass
class Scored:
    """One candidate's full evaluation, bundled into one object."""
    coilset: object          # the candidate itself, for the final diff-vs-baseline report
    metrics: dict            # distance -> dict (dataclasses.asdict(SimResult) - see evaluate_async())
    objective_results: list  # one ObjectiveResult per spec.objectives entry, same order
    goal_met: bool           # every REQUIRED objective passed
    # field -> (baseline, candidate) for every CoilSet field that differs -
    # set by optimize.py from variables.audit_changed_fields().
    changed_fields: dict = dataclasses.field(default_factory=dict)


def pct(new, old):
    """Percent change from old to new (0 if old is 0, to avoid div-by-zero).
    Relative to old's own sign, so for a negative M/k it's the change in
    magnitude - "reduce M13" means a weaker coupling either way."""
    return 0.0 if old == 0 else (new - old) / old * 100.0


def _outer_diameter(radius, num_turns, turn_spacing, num_parallel_traces, trace_spacing, trace_width):
    """Outer diameter from geometry alone - same formula femm_service.check_valid_coil() uses."""
    turn_width = (num_parallel_traces - 1) * trace_spacing + trace_width
    coil_width = (num_turns - 1) * turn_spacing + turn_width
    return 2 * (radius + coil_width / 2)


def coil_diameters(coilset):
    """Every coil's outer diameter, keyed by objective.DERIVED_QUANTITIES name."""
    return {
        "loop_outer_diameter": _outer_diameter(
            coilset.loop_r, coilset.num_loop_turns, coilset.loop_turn_spacing,
            coilset.num_parallel_loop_traces, coilset.loop_trace_spacing, coilset.loop_trace_width),
        "tank_outer_diameter": _outer_diameter(
            coilset.tank_center_r, coilset.num_tank_turns, coilset.tank_turn_spacing,
            coilset.num_parallel_tank_traces, coilset.tank_trace_spacing, coilset.tank_trace_width),
        "rc_outer_diameter": _outer_diameter(
            coilset.rc_radius, coilset.num_rc_turns, coilset.rc_turn_spacing,
            coilset.num_parallel_rc_traces, coilset.rc_trace_spacing, coilset.rc_trace_width),
    }


def design_values(coilset) -> dict:
    """Every distance-independent quantity an objective can name: the
    CoilSet's own fields plus the derived outer diameters."""
    return {**dataclasses.asdict(coilset), **coil_diameters(coilset)}


def evaluate_async(coilset, distances, job_runner):
    """Kicks off one solve per distance - never blocks. Each is either a
    cache hit (job_runner.find_cached()/touch(), already resolved, a plain
    dict) or a fresh job_runner.submit() (a live Job, resolved later by
    wait_for_jobs()) - this is what routes agent solves through the same
    cache and worker pool as a manually-run solve, instead of calling
    simulate() directly."""
    jobs = {}
    for d in distances:
        cached = job_runner.find_cached(coilset, d)
        jobs[d] = job_runner.touch(cached) if cached is not None else \
            job_runner.submit(coilset, config_id="agent", config_name="agent-candidate", distance=d)
    return jobs


def wait_for_jobs(jobs: dict, poll_interval=0.05) -> dict:
    """Blocks the CALLING thread until every job in `jobs` is done or
    failed, returning distance -> result dict. Call this from the agent's
    own background thread (agent/runner.py) - never from a GUI render
    thread, which must stay non-blocking every frame."""
    results = {}
    for d, job in jobs.items():
        if isinstance(job, dict):  # a cache hit - find_cached()/touch() already resolved it
            results[d] = job["result"]
            continue
        while job.status not in ("done", "failed"):
            time.sleep(poll_interval)
        if job.status == "failed":
            raise RuntimeError(job.error or f"solve failed at distance {d}")
        results[d] = job.result
    return results


def evaluate(coilset, baseline, baseline_metrics, spec, job_runner):
    """Simulates one candidate at every distance (via job_runner - cached,
    concurrent with any other candidate solving this round) and scores it
    vs. `baseline` (the original CoilSet, and its already-solved metrics).
    `spec` is an agent.spec.AgentSpec. Raises whatever the solve raises
    (invalid geometry, a failed job) - the caller turns that into a
    rejected tool result instead of catching it here."""
    metrics = wait_for_jobs(evaluate_async(coilset, spec.distances, job_runner))
    design, baseline_design = design_values(coilset), design_values(baseline)
    results = [
        _check_per_distance(obj, metrics, baseline_metrics, spec.distances) if is_per_distance(obj.metric)
        else _check_once(obj, design[obj.metric], baseline_design[obj.metric])
        for obj in spec.objectives
    ]
    return Scored(coilset=coilset, metrics=metrics, objective_results=results,
                  goal_met=all(r.passed for r in results if r.objective.required))


def _check_once(obj: Objective, value, baseline_value) -> ObjectiveResult:
    change = pct(value, baseline_value)
    m = margin(obj, change, value)
    return ObjectiveResult(objective=obj, change_pct=change, value=value, baseline_value=baseline_value,
                           margin=m, passed=m >= 0)


def _check_per_distance(obj: Objective, metrics: dict, baseline_metrics: dict, distances) -> ObjectiveResult:
    at = {d: _check_once(obj, metrics[d][obj.metric], baseline_metrics[d][obj.metric]) for d in distances}
    worst_d = min(distances, key=lambda d: at[d].margin)
    return dataclasses.replace(at[worst_d], worst_distance=worst_d,
                               change_pct_by_distance={d: r.change_pct for d, r in at.items()})


def rank_key(s: Scored):
    """Sort key, lower is better. Two tiers, generalizing the old "M gate,
    then ESR" ranking to any objective list:

    - A required `match` is a constraint to hold, not something to push
      further - once inside its band, being even closer to target earns
      nothing. Candidates holding every required match rank first (tier 0),
      ordered by their reduce/increase margins, worst first (a candidate is
      only as good as its weakest improvement), then by preferences.
    - Candidates breaking a required match (tier 1) rank by how far outside
      the band(s) they landed, so there's still a "best attempt" to report.

    There are no built-in tie-breakers: anything that should break ties is
    a preference in spec.objectives. With AgentSpec's default objectives
    this orders candidates the way the old scorer did: tier 0 = M23 held,
    sorted by the least-improved coil's ESR."""
    required = [r for r in s.objective_results if r.objective.required]
    gates = [r for r in required if r.objective.kind == "match"]
    targets = [r for r in required if r.objective.kind != "match"]
    prefs = [r for r in s.objective_results if not r.objective.required]

    target_badness = tuple(sorted((-r.margin for r in targets), reverse=True))
    if all(r.passed for r in gates):
        return (0, target_badness, tuple(sorted((_pref_badness(r) for r in prefs), reverse=True)))
    return (1, tuple(sorted((-r.margin for r in gates), reverse=True)), target_badness)


def _pref_badness(r: ObjectiveResult) -> float:
    """A satisfied `match` preference is simply satisfied (0); reduce/
    increase keep rewarding more headroom."""
    return max(0.0, -r.margin) if r.objective.kind == "match" else -r.margin


def _sig(x: float) -> float:
    return float(f"{x:.6g}")  # values span 1e-7 H to 1e2 mm - round to significant digits, not decimals


def objective_result_dict(r: ObjectiveResult) -> dict:
    return {
        "metric": r.objective.metric, "kind": r.objective.kind,
        "target_pct": r.objective.target_pct, "target_value": r.objective.target_value,
        "tolerance_pct": r.objective.tolerance_pct,
        "required": r.objective.required, "requirement": r.objective.describe(),
        "change_pct_by_distance": {str(d): round(c, 2) for d, c in (r.change_pct_by_distance or {}).items()},
        "worst_change_pct": round(r.change_pct, 2),
        "value": _sig(r.value), "baseline_value": _sig(r.baseline_value),
        "passed": r.passed, "margin": round(r.margin, 3),
    }


def tool_result(s: Scored, spec) -> dict:
    """The dict sent back to the model as the tool's result - its entire
    feedback signal. Self-describing (each objective carries its own
    requirement), so the GUI and history replay render it without needing
    the spec that produced it."""
    return {
        "valid": True,
        "goal_met": s.goal_met,
        "objectives": [objective_result_dict(r) for r in s.objective_results],
    }


def objective_summary(result: dict) -> str:
    """One line per candidate for logs/CLI - e.g. "M23 -1.2% ok, loop_esr
    -8.0% MISS". Works on a tool_result() dict so live and stored runs share it.
    An absolute target shows the value itself rather than a % change."""
    def shown(o):
        return f"{o['value']:g}" if o.get("target_value") is not None else f"{o['worst_change_pct']:+.1f}%"

    return ", ".join(
        f"{o['metric']} {shown(o)} {'ok' if o['passed'] else 'MISS'}" + ("" if o["required"] else " (pref)")
        for o in result.get("objectives", [])
    )
