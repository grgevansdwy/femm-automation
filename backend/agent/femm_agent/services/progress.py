"""Is the search still getting anywhere? Judged by code from the stored
results - arithmetic, never the model's opinion (and nothing here ever says
a target is impossible: a lever may simply not have been tried).

One number is tracked across rounds:

- before any design meets the goal: how far the closest design still is -
  the smallest `shortfall` (the most points any required objective is short)
  over every valid design so far;
- once one does: the best score among the passing designs (scorer.blended_score,
  or the favourite objective's points; for a goal with nothing required left
  to push, the same blend over its reduce/increase preferences).

A round "improves" when that number beats the last recorded reference by
more than settings.noise_points - about what the solver's mesh shifts a
result by - and the reference then moves. The count of rounds since the
last improvement restarts at the first pass and at the start of each
"continue" segment. Verdict: "improving" while that count is below 2;
otherwise "plateau" after a pass, "stalled" before one.
"""

import dataclasses
import json

from agent.femm_agent.services.scorer import blended_score, preference_score, shortfall

STILL_IMPROVING_BELOW = 2  # rounds without improvement before the verdict stops being "improving"
STALL_GRACE_ROUNDS = 2     # rounds the search gets after the stalled notice before it stops
NEAR_COPY_TOLERANCE = 0.02  # same fields changed, every value within 2% of an earlier design's


@dataclasses.dataclass
class Progress:
    verdict: str                  # "improving" | "stalled" | "plateau"
    rounds_since_improvement: int
    passed: bool                  # some design meets the goal
    reference: float | None       # the tracked number at the last improvement
    repeats: int = 0              # proposals identical to an earlier one
    near_copies: int = 0          # proposals within NEAR_COPY_TOLERANCE of an earlier one
    untried_fields: list = dataclasses.field(default_factory=list)  # free fields no proposal has changed


def can_improve_after_pass(spec) -> bool:
    """False for a goal with only `match` objectives: a match can't be improved
    once inside its band, so the first passing design is the result."""
    return any(o.kind != "match" for o in spec.objectives)


def _pass_score(result: dict, favourite) -> float | None:
    score = blended_score(result, favourite)
    return score if score is not None else preference_score(result)


def assess(tried, rounds, spec, free_fields=(), segment_start_round: int = 0) -> Progress:
    """The verdict after the rounds so far. `tried`: every state.Tried entry;
    `rounds`: the round numbers that count, in order (rounds in which the
    model never got to reply are left out); `free_fields`: the fields the
    model may change. Pure - same stored results, same verdict."""
    noise = spec.settings.noise_points
    by_round: dict[int, list] = {}
    for t in tried:
        # A design that met the goal but failed its fragility check is not a
        # pass and not progress: it is left out of the tracked number.
        if t.scored is not None and not t.check_failed:
            by_round.setdefault(t.round_num, []).append(t.result)

    reference, since, passed = None, 0, False
    closest, best_score = None, None
    segment_started = False
    for n in rounds:
        if n > segment_start_round and not segment_started:
            segment_started, since = True, 0
        first_pass = False
        for result in by_round.get(n, []):
            miss = shortfall(result)
            closest = miss if closest is None else min(closest, miss)
            if result.get("goal_met"):
                score = _pass_score(result, spec.favourite_metric)
                if score is not None and (best_score is None or score > best_score):
                    best_score = score
                if not passed:
                    passed = first_pass = True
        if first_pass:
            reference, since = best_score, 0  # the first pass is itself the improvement
        elif passed:
            if best_score is not None and reference is not None and best_score > reference + noise:
                reference, since = best_score, 0
            else:
                since += 1
        elif closest is not None and (reference is None or closest < reference - noise):
            reference, since = closest, 0
        else:
            since += 1

    if since < STILL_IMPROVING_BELOW:
        verdict = "improving"
    else:
        verdict = "plateau" if passed else "stalled"
    repeats, near_copies = _count_copies(tried)
    changed = {field for t in tried for field in t.delta}
    return Progress(verdict=verdict, rounds_since_improvement=since, passed=passed, reference=reference,
                    repeats=repeats, near_copies=near_copies,
                    untried_fields=[f for f in free_fields if f not in changed])


def _count_copies(tried) -> tuple[int, int]:
    """(identical proposals, near-copies) - each proposal compared with every
    earlier one, as "baseline + its changes"."""
    repeats = near_copies = 0
    seen: list[dict] = []
    for t in tried:
        delta = t.delta
        key = json.dumps(delta, sort_keys=True, default=str)
        if any(key == json.dumps(earlier, sort_keys=True, default=str) for earlier in seen):
            repeats += 1
        elif delta and any(_near(delta, earlier) for earlier in seen):
            near_copies += 1
        seen.append(delta)
    return repeats, near_copies


def _near(a: dict, b: dict) -> bool:
    if set(a) != set(b):
        return False
    for field, value in a.items():
        other = b[field]
        if not isinstance(value, (int, float)) or not isinstance(other, (int, float)):
            return False
        if abs(value - other) > NEAR_COPY_TOLERANCE * max(abs(value), abs(other)):
            return False
    return True


def stalled_notice(progress: Progress) -> str:
    """The one message code sends the model when the search has stalled before
    any pass. States what was measured and which fields are untouched -
    nothing about how many values a proposal should change."""
    fields = (f"Fields not tried yet: {', '.join(progress.untried_fields)}." if progress.untried_fields
              else "Every field you may change has been tried at least once.")
    return f"No improvement in {progress.rounds_since_improvement} rounds. {fields}"
