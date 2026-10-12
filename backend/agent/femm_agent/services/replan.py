"""A revised plan after a search: what the search keeps from before.

Once a search has ended the user can change the goal in the same chat
(agent/femm_agent/services/followup.py turns the request into a new plan,
code validates it, the user clicks Start Search again). Everything here is
plain code - what is remembered, what may still win and what starts over
are never the model's call:

- memory_mode(): every distance of the new plan was already in the plan it
  replaces -> the search keeps its memory; a distance was added -> it starts
  from the beginning (earlier designs were never simulated there).
- rebuild_memory(): the tried list, rebuilt from the run's stored rounds and
  scored against the plan now in force. A design the new plan does not allow
  (it changes a field that is no longer open, is outside a limit, or misses a
  pinned value) stays in the list as tried, but can never be the best design.
- plan_diff(): what the new plan changes, in words, for the person who has
  to confirm it.

The stored rounds are never rewritten: each keeps the result it got under
the plan it was run for. Re-scoring only changes what the search remembers.
"""

import dataclasses

from agent.femm_agent.models.schema import NEVER_EVALUATED, Rejection, coerce_delta
from agent.femm_agent.models.state import Tried
from agent.femm_agent.services import progress
from agent.femm_agent.services.scorer import evaluate_async, rank_key, score, tool_result, wait_for_jobs
from agent.input_chat.models.variables import (
    NUMERIC_FIELDS, allowed_fields, audit_changed_fields, free_fields_for, search_field_table,
)

KEPT, FRESH = "kept", "fresh"
# The code a design tried under an earlier plan carries in the model's memory
# when the plan now in force would not accept it. Not a schema.REJECTION_DETAIL
# code: no proposal is ever answered with it.
NOT_IN_PLAN = "not_in_current_plan"
_NOT_IN_PLAN_DETAIL = "Tried under an earlier plan. The current plan does not allow this design."


def memory_mode(old_distances, new_distances) -> str:
    """ "kept" when every distance of the new plan is in the plan it replaces
    (earlier designs already have a result at each one), "fresh" as soon as
    one is added. Compared with the plan being replaced, not with everything
    the run ever simulated: 3,5 -> 5 keeps the memory, 5 -> 3,5 afterwards
    starts over, because the designs tried at 5 alone have no 3 mm result."""
    old = {float(d) for d in old_distances}
    return KEPT if all(float(d) in old for d in new_distances) else FRESH


def design_problem(coilset, baseline, spec) -> Rejection | None:
    """Why `coilset` could not be proposed under `spec`, or None when it could -
    the checks optimize._prepare() makes on a new proposal (the field is open
    in the plan's Field Config table and not excluded, the value is inside
    its limit, the audit against the baseline), applied to a design that
    already exists, plus: every pinned value must be the pin."""
    variable_spec = spec.variable_spec
    table = search_field_table(spec.settings)
    changed = audit_changed_fields(baseline, coilset, allowed_fields(baseline, variable_spec, table))
    if changed["unexpected_fields"]:
        return Rejection("not_enabled", sorted(changed["unexpected_fields"])[0])
    for field, value in variable_spec.fixed_values.items():
        if getattr(coilset, field) != value:
            return Rejection("not_allowed_value", field, f"pinned to {value:g}")
    free_changes = {k: new for k, (_, new) in changed["changed_fields"].items() if k not in variable_spec.fixed_values}
    try:
        coerce_delta(free_changes, free_fields_for(baseline, variable_spec, table), NUMERIC_FIELDS)
    except Rejection as rejection:
        return rejection
    return None


def _not_in_plan(problem: Rejection) -> dict:
    """The memory entry of an earlier design the current plan does not allow,
    in the fixed rejection format: which field, and what the plan allows."""
    return {"valid": False, "error": NOT_IN_PLAN, "field": problem.field, "allowed": problem.allowed,
            "detail": _NOT_IN_PLAN_DETAIL}


def _same_design_now(candidate, baseline, spec) -> bool:
    """True when the current plan would build, from this stored proposal, the
    very design that was rejected back then - so the rejection (bad geometry,
    a failed solve) still holds and is worth remembering."""
    if not candidate.candidate_coilset:
        return False
    try:
        table = search_field_table(spec.settings)
        delta = coerce_delta(candidate.delta, free_fields_for(baseline, spec.variable_spec, table), NUMERIC_FIELDS)
        built = dataclasses.replace(baseline, **spec.variable_spec.fixed_values, **delta)
    except Exception:
        return False
    return dataclasses.asdict(built) == candidate.candidate_coilset and built != baseline


def segment_plans(state) -> dict[int, int]:
    """segment -> the plan it ran under, from the finished segments' budget
    entries (an entry written before plans were numbered is plan 1) and the
    live state for the segment in progress."""
    plans = {entry.get("segment"): entry.get("plan") or 1 for entry in state.budget_history}
    plans.setdefault(state.segment, state.plan)
    return plans


def rebuild_memory(state, job_runner) -> None:
    """Rebuilds state.tried, state.best and state.progress from state.rounds for the plan in force
    (state.spec): every stored design after state.memory_from_round, its
    solves requested again (cache hits - a miss is solved again, outside the
    budget) and scored against the current objectives. Nothing is trusted
    from the stored scores.

    Rounds run under the current plan come back exactly as the search left
    them. From rounds run under an earlier plan: a simulated design the
    current plan does not allow is kept as tried but unscored (it cannot be
    the best design); a rejected proposal is kept only when the current plan
    would build the same design again."""
    from engine.models.coilset import CoilSet

    spec, baseline = state.spec, state.baseline_coilset
    state.baseline_metrics = wait_for_jobs(evaluate_async(
        baseline, spec.distances, job_runner, config_id=spec.baseline_config_id, config_name=baseline.config_name))
    table = search_field_table(spec.settings)  # the plan's own copy of the Field Config table
    allowed = allowed_fields(baseline, spec.variable_spec, table)
    plans = segment_plans(state)
    state.tried, state.best = [], None

    labelled = 0  # simulated designs seen so far, in stored order
    for round_state in state.rounds:
        same_plan = plans.get(round_state.segment, 1) == state.plan
        for candidate in round_state.candidates:
            if candidate.valid:
                # A run saved before designs had labels gets them here, in stored order -
                # the same numbers the search itself would have given. Every simulated
                # design of the run counts, in memory or not: a label is never reused.
                labelled += 1
                candidate.label = candidate.label or f"C{labelled}"
            if round_state.round_num <= state.memory_from_round:
                continue
            if not candidate.valid:
                # A proposal turned away unseen (over a limit) was never part of the memory.
                never_evaluated = (candidate.error_code in NEVER_EVALUATED
                                   or (candidate.error or "").startswith("not simulated"))  # rows from before the codes
                if not never_evaluated and (same_plan or _same_design_now(candidate, baseline, spec)):
                    state.tried.append(Tried(round_state.round_num, candidate.delta, None,
                                             candidate.rejection or {"valid": False, "error": candidate.error or ""},
                                             round_state.segment, base_label=candidate.base_label, step=candidate.step))
                continue
            coilset = CoilSet(**candidate.candidate_coilset)
            delta = candidate.delta
            if not same_plan:
                problem = design_problem(coilset, baseline, spec)
                if problem is not None:
                    # No label: a design the plan does not allow cannot be a proposal's `base`.
                    state.tried.append(Tried(round_state.round_num, candidate.delta, None, _not_in_plan(problem),
                                             round_state.segment))
                    continue
                changed = audit_changed_fields(baseline, coilset, allowed)["changed_fields"]
                # "baseline + the listed changes", as the model is told: pins are not part of a proposal.
                delta = {k: new for k, (_, new) in changed.items() if k not in spec.variable_spec.fixed_values}
            scored = score(coilset, wait_for_jobs(evaluate_async(coilset, spec.distances, job_runner)),
                           baseline, state.baseline_metrics, spec)
            scored.changed_fields = audit_changed_fields(baseline, coilset, allowed)["changed_fields"]
            result = tool_result(scored, spec)
            if same_plan:
                candidate.tool_result = result  # the round's own record, re-derived rather than trusted
            state.tried.append(Tried(round_state.round_num, delta, scored, result, round_state.segment,
                                     label=candidate.label, base_label=candidate.base_label, step=candidate.step))

    simulated = [t.scored for t in state.tried if t.scored is not None]
    state.best = min(simulated, key=lambda s: rank_key(s, spec.favourite_metric)) if simulated else None

    state.progress = progress.assess(
        state.tried, [r.round_num for r in state.rounds if not r.no_reply], spec,
        free_fields_for(baseline, spec.variable_spec, table), state.segment_start_round) if state.rounds else None


def adopt_plan(state, spec) -> str:
    """Makes `spec` the plan in force on `state` and says how the memory
    carries over ("kept" or "fresh"). Does not rebuild anything - call
    rebuild_memory() next (it needs the solver cache, so it runs off the
    GUI thread)."""
    mode = memory_mode(state.spec.distances, spec.distances)
    state.plan += 1
    if mode == FRESH:
        state.memory_from_round = len(state.rounds)
    state.spec = spec
    state.segment_memory = mode
    return mode


def _objective_text(o) -> str:
    return f"{o.metric}: {'required' if o.required else 'preferred'}, {o.describe()}"


def plan_diff(old, new) -> list[str]:
    """What `new` changes from `old`, one plain line per change, for the plan
    card the user confirms. Empty when the two plans ask for the same thing."""
    lines = []
    old_objectives = {o.metric: o for o in old.objectives}
    new_objectives = {o.metric: o for o in new.objectives}
    for metric, o in new_objectives.items():
        if metric not in old_objectives:
            lines.append(f"Added objective - {_objective_text(o)}")
        elif o != old_objectives[metric]:
            lines.append(f"Changed objective - {_objective_text(o)} (was: {_objective_text(old_objectives[metric])})")
    for metric, o in old_objectives.items():
        if metric not in new_objectives:
            lines.append(f"Removed objective - {_objective_text(o)}")
    if old.favourite_metric != new.favourite_metric:
        lines.append(f"Favourite: {new.favourite_metric or 'none'} (was: {old.favourite_metric or 'none'})")

    old_d, new_d = [float(d) for d in old.distances], [float(d) for d in new.distances]
    if set(old_d) != set(new_d):
        added = [d for d in new_d if d not in old_d]
        removed = [d for d in old_d if d not in new_d]
        parts = ([f"added {', '.join(f'{d:g}' for d in added)} mm"] if added else []) + \
                ([f"removed {', '.join(f'{d:g}' for d in removed)} mm"] if removed else [])
        lines.append(f"Distances: {'; '.join(parts)}")

    old_v, new_v = old.variable_spec, new.variable_spec
    for field in sorted(set(old_v.fixed_values) | set(new_v.fixed_values)):
        before, after = old_v.fixed_values.get(field), new_v.fixed_values.get(field)
        if before == after:
            continue
        if before is None:
            lines.append(f"Pinned {field} = {after:g}")
        elif after is None:
            lines.append(f"No longer pinned: {field} (was {before:g})")
        else:
            lines.append(f"Pinned {field} = {after:g} (was {before:g})")
    newly_fixed = sorted(set(new_v.excluded_fields) - set(old_v.excluded_fields))
    released = sorted(set(old_v.excluded_fields) - set(new_v.excluded_fields))
    if newly_fixed:
        lines.append(f"Now kept fixed: {', '.join(newly_fixed)}")
    if released:
        lines.append(f"No longer kept fixed: {', '.join(released)}")
    # The Field Config table each plan carries (the user's Settings at the time).
    old_table, new_table = search_field_table(old.settings), search_field_table(new.settings)
    for field in sorted(set(old_table) | set(new_table)):
        before, after = old_table.get(field), new_table.get(field)
        if before == after or before is None or after is None:
            continue
        if before.enabled != after.enabled:
            lines.append(f"Field Config: {field} is now {'open' if after.enabled else 'closed'}")
        elif after.enabled:
            limits = " to ".join("no limit" if v is None else f"{v:g}" for v in (after.min, after.max))
            lines.append(f"Field Config: {field} limits are now {limits}")
    return lines


def memory_line(old, new, tried=()) -> str:
    """One sentence for the plan card: what the next search starts from."""
    if memory_mode(old.distances, new.distances) == FRESH:
        return ("A distance was added, so this search starts from the beginning: the designs tried so far were "
                "never simulated there and cannot be compared.")
    simulated = sum(1 for t in tried if t.scored is not None)
    return f"Continues from the {simulated} design(s) already simulated, re-scored against this plan."
