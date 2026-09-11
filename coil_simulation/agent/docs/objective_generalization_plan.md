# Generalizing the Agent's Objective/Evaluator Architecture

## Context

The AI-Agent GUI panel (Milestones 1-5, see `gui_agent_plan.md`) is built
and working. This is a design for a follow-on gap found through actually
using it: a real user asked to match `L_loop` 10% lower while keeping
`L_rc` within 2%, with no interest in ESR or mutual inductance at all -
and today's architecture cannot express that.

Tracing it through the real code confirms it's a genuine gap, not a
misunderstanding. `AgentSpec`'s objective is a **fixed formula** (an
M-metric tolerance gate, plus an unconditional all-three-coil
ESR-improvement requirement) with only its *numbers* configurable, not its
*shape*. `_build_spec()` (`agent/interview.py`) silently re-imposes the
default M23 gate even when a user says they don't care about mutual
inductance at all - it can't distinguish "not mentioned" from "explicitly
none." And `goal_met`/`rank_key()` in `scorer.py` hard-require and rank by
ESR on all three coils, with no way to turn that off.

Separately, the user asked whether the evaluator verifies structural
consistency - e.g. that a candidate didn't touch ferrite when the baseline
has none. That's partly already true: `agent/variables.py`'s
`free_fields_for()`/`agent/schema.py`'s `coerce_delta()` already *prevent*
this at proposal time (a field not in `free_fields` can't even appear in a
delta). But there's no explicit *verification* step that confirms and
reports it - and a real, concrete blind spot was found in today's
diff-display code that makes the gap non-hypothetical: both
`orchestrator.py`'s final report and `agent_panel.py`'s comparison view
build their "what changed" diff by filtering *by* `free_fields` before
comparing baseline to candidate - if a field ever changed *outside*
`free_fields`, it wouldn't show up as a discrepancy, it would just
silently not appear. That's a filter, not a check.

This plan replaces the fixed M-gate/ESR-threshold shape with a generic,
arbitrary-length list of objectives over any of the 12 real physical
quantities a FEMM solve produces, plus an explicit structural-consistency
audit. It was produced after an independent design-review pass that
pressure-tested an initial draft and found/fixed a real scale-normalization
bug in the ranking formula before it ever reached code (see §2).

**Status: implemented (2026-09-11)**, with the deviations listed under
§Implementation notes at the end - read those before trusting any detail
of §1-§7 below.

## Design

### 1. `Objective` - a new, minimal vocabulary (`coil_simulation/agent/objective.py`, new file)

All 12 fields a FEMM solve actually produces (confirmed directly against
`coil_simulation/models/result.py`) become reachable, not just M12/M13/M23
and the three `*_esr` fields:

```python
SIM_RESULT_METRICS = (
    "L_loop", "L_tank", "L_rc",
    "M12", "M13", "M23",
    "loop_esr", "tank_esr", "rc_esr",
    "k12", "k13", "k23",
)
# M/k metrics couple two coils, so they intrinsically vary with RC
# separation (today's M-gate already checks "at every distance" - not
# negotiable). L/ESR are single-coil properties, essentially distance-
# independent (today's code already uses one representative distance for
# them). This split is physical, not a matter of taste - so it's automatic,
# not an LLM-settable field (exposing it as a choice adds a failure mode -
# the model picking "representative distance" for an M metric and getting
# a misleadingly easy pass - for no benefit anyone asked for).
PER_DISTANCE_METRICS = frozenset({"M12", "M13", "M23", "k12", "k13", "k23"})

KINDS = ("match", "reduce", "increase")
# A 4th "maintain" kind is redundant: it's exactly kind="reduce",
# tolerance_pct=0.0, required=False (today's fixed "reduce or maintain
# diameter/L, not required" tie-breakers are already this shape).

@dataclasses.dataclass(frozen=True)
class Objective:
    metric: str            # one of SIM_RESULT_METRICS
    kind: str               # one of KINDS
    tolerance_pct: float    # >= 0; 0 only makes sense for required=False
    required: bool = True   # True = hard gate (blocks goal_met); False = ranking-only tie-break
```

**Scope call, made deliberately, not silently**: `tank_diam`/`rc_diam`
(today's other hardcoded tie-breakers) are *not* `SimResult` fields -
they're derived from geometry (`scorer.coil_diameters()`), with no
"baseline value to diff against" the same way a solve output has. They
stay as the fixed, non-configurable final tie-break they already are
today, outside the generalized `Objective` model. Folding them in is
possible later; nobody has asked for it yet.

### 2. Margin-based ranking, with the scale-normalization bug fixed before it shipped

A draft that ranked by raw percentage-point margins was wrong: a candidate
missing a 5%-tolerance M-gate by 0.5pp and one missing a 50%-tolerance
ESR-reduction target by 0.5pp aren't equally bad - the first overshot its
tolerance's own width by 10%, the second fell short by only 1%. Fixed by
normalizing margin as a **fraction of `tolerance_pct`**:

```python
def margin(kind: str, actual_pct: float, tolerance_pct: float) -> float:
    """>=0 passes. Headroom as a FRACTION of tolerance_pct, so objectives
    at very different natural scales compare fairly in rank_key()."""
    if kind == "match":
        raw = tolerance_pct - abs(actual_pct)
    elif kind == "reduce":
        raw = -actual_pct - tolerance_pct
    elif kind == "increase":
        raw = actual_pct - tolerance_pct
    else:
        raise ValueError(f"unknown objective kind: {kind!r}")
    return raw / tolerance_pct if tolerance_pct else raw
```
Verified against the worked example above: normalized margins are -0.10
(the M23 case) vs -0.01 (the ESR case) - correctly ranks the ESR case as
"almost passing" instead of treating both as equally bad.

`rank_key()` (`coil_simulation/agent/scorer.py`) generalizes to:
```python
def rank_key(s: Scored):
    req_badness = sorted((-r.margin for r in s.objective_results if r.objective.required), reverse=True)
    opt_badness = sorted((-r.margin for r in s.objective_results if not r.objective.required), reverse=True)
    passing = not req_badness or req_badness[0] <= 0
    return (0 if passing else 1, tuple(req_badness), tuple(opt_badness),
            s.tank_diam_delta_pct, s.rc_diam_delta_pct, s.tank_L_delta_pct, s.rc_L_delta_pct)
```
Worst-badness-first generalizes today's `max(loop,tank,rc)` "worst of
three ESR" rule to an arbitrary-length required-objective list.

**Real behavior change, flagged explicitly**: today, M23's own margin
never affects ranking once `primary_pass` is true (it's a separate binary
gate). Under this scheme, M23 becomes just another required objective and
*can* become the binding worst-link, changing tie-break order vs. today's
exact behavior. This is more principled (a required objective sitting at
the edge of its tolerance is real risk worth ranking down) - noted here as
a conscious tradeoff, not something to discover after the fact.

### 3. `finalize_spec` schema + `_build_spec()` (`coil_simulation/agent/interview.py`)

Replace `primary_metrics`/`min_esr_improvement_pct` with:
```python
"objectives": {
    "type": "array", "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": list(SIM_RESULT_METRICS)},
            "kind": {"type": "string", "enum": list(KINDS)},
            "tolerance_pct": {"type": "number", "minimum": 0},
            "required": {"type": "boolean"},  # defaults to true if omitted
        },
        "required": ["metric", "kind", "tolerance_pct"],
    },
},
```
`"required"` at the top level becomes `["objective_text", "distances", "objectives"]`
- no more implicit fallback to a hidden default; the interview must
always gather at least one explicit objective. `_build_spec()` parses/
validates each entry (unknown metric/kind -> `ValueError`, same fail-fast-
with-`ValueError` pattern already used for `distances`/`excluded_fields`
a few lines above it today) and builds `list[Objective]`.

### 4. Structural-consistency audit (`coil_simulation/agent/variables.py` + `optimize.py`)

```python
# agent/variables.py, new function
def audit_changed_fields(baseline, candidate, free_fields: dict) -> dict:
    """Defense in depth: coerce_delta() already rejects any out-of-
    free_fields key in the proposed delta before dataclasses.replace()
    ever runs, so unexpected_fields should always be empty here - this
    makes that guarantee auditable/visible instead of merely assumed."""
    baseline_dict = dataclasses.asdict(baseline)
    candidate_dict = dataclasses.asdict(candidate)
    changed = {k: (baseline_dict[k], candidate_dict[k]) for k in baseline_dict if baseline_dict[k] != candidate_dict[k]}
    unexpected = {k: v for k, v in changed.items() if k not in free_fields}
    return {"changed_fields": changed, "unexpected_fields": unexpected}
```

Called from `agent/optimize.py`'s per-candidate loop, right after
`dataclasses.replace()` and *before* `evaluate()` - a caught violation
skips a real FEMM solve entirely (free performance upside) and reuses the
loop's existing `except Exception` rejection path (same shape as bad
geometry already uses), no new error-handling machinery:
```python
candidate = dataclasses.replace(baseline, **delta)
audit = audit_changed_fields(baseline, candidate, free_fields)
if audit["unexpected_fields"]:
    raise ValueError(f"candidate changed field(s) outside the allowed set: {sorted(audit['unexpected_fields'])}")
scored = evaluate(candidate, baseline_metrics, baseline_diam, spec, job_runner)
scored.changed_fields = audit["changed_fields"]
```
Not echoed into the per-round `tool_result()` (the model already has its
own delta in context - re-stating it is noise, and a real violation
already surfaces via the same rejected-tool-result path bad geometry
uses). Surfaced instead in the **human-facing** final report: `Scored`
gains a `changed_fields` field, and `orchestrator.py`'s CLI printout /
`agent_panel.py`'s `_draw_comparison()` both call `audit_changed_fields()`
directly instead of their current unfiltered-by-nothing diff comprehension,
with a visible confirmation line ("Structural check: no out-of-scope
fields changed") rather than silence.

### 5. Prompt changes

`agent/prompts.py`'s `system_prompt()`: replace the fixed "PRIMARY: match
M23..." text and the "reduce ESR on ALL THREE coils" paragraph with a
generic per-objective renderer (metric, kind-as-verb, tolerance,
per-distance-or-representative scope, REQUIRED/PREFERRED tag). Replace the
"whichever coil's ESR improved least is your bottleneck" guidance with
metric-agnostic guidance: check each round's per-objective `passed`/
`margin` in the tool result; the one with the most negative margin is the
current bottleneck, whatever metric it is - more actionable than today's
text, not less.

`agent/interview.py`'s `interview_system_prompt()`: replace the current
M-metrics-only capability note with the full 12-metric vocabulary (a
one-line physical gloss each), explain `match`/`reduce`/`increase` and
`required` vs. preference, and state that per-distance-vs-representative
handling is automatic so the model doesn't invent a wrong explanation when
relaying the plan back to the user.

### 6. Downstream call sites (grep-confirmed complete list, no other hits repo-wide)

- `gui/src/gui/panels/agent_panel.py`: `_plan_summary_lines()` (loop over
  `spec.objectives` instead of the two removed fields), `_draw_comparison()`
  (loop over `best.objective_results` instead of the hardcoded loop/tank/rc
  ESR line; diff table replaced by `audit_changed_fields()`'s output, with
  an `unexpected_fields` warning row), `_round_row_text()` (`tr['primary_pass']`
  no longer exists - use `tr['goal_met']`).
- `coil_simulation/agent/orchestrator.py`: per-round printout and the
  final report's summary line + diff, same shape of change.
- `gui/src/gui/panels/agent_history.py` and the DB layer
  (`ai_run_repository.py`/`db.py`) need **no changes** - confirmed
  `agent_history.py`'s only `"goal_met"` reference is an unrelated run-
  status enum string, and the DB stores `AgentSpec`/`Scored` as opaque
  `json.dumps()` blobs read back as plain dicts, never reconstructed into
  live dataclasses - no migration needed, old runs' stored JSON just stays
  as historical record in its old shape.

### 7. Backward-compatible default

`AgentSpec()` with no arguments (`orchestrator.py`'s CLI wrapper) must keep
behaving exactly as it does today:
```python
# agent/spec.py
def _default_objectives() -> list[Objective]:
    """Reconstructs today's exact hardcoded default - match PRIMARY_M
    within M_TOLERANCE_PCT, cut ESR on all three coils by at least
    MIN_ESR_IMPROVEMENT_PCT each."""
    return [
        Objective(metric=PRIMARY_M, kind="match", tolerance_pct=M_TOLERANCE_PCT, required=True),
        Objective(metric="loop_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT, required=True),
        Objective(metric="tank_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT, required=True),
        Objective(metric="rc_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT, required=True),
    ]
```
Lives in `spec.py` (which already imports `agent.config`), not
`config.py` - `config.py`'s own docstring states "zero dependencies,
everything else in this package imports from here"; importing
`agent.objective` into it would invert that. `config.py` keeps owning the
raw numeric constants; `spec.py` assembles them into `Objective` instances.
`AgentSpec.objectives: list = dataclasses.field(default_factory=_default_objectives)`
replaces `primary_metrics`/`min_esr_improvement_pct` as fields.

## Build order (staged, verified against the real LLM server + real FEMM solves at each step, same convention as `gui_agent_plan.md`)

1. **`agent/objective.py`** (new, pure) - `Objective`, `SIM_RESULT_METRICS`,
   `PER_DISTANCE_METRICS`, `margin()`. Verify by hand against the worked
   scenarios in §2 - no FEMM/LLM needed, fast.
2. **`scorer.py` + `spec.py` + `variables.py` (`audit_changed_fields`) +
   `optimize.py` wiring.** Verify: run `orchestrator.py`'s CLI end-to-end
   against the live LLM server with `AgentSpec()`'s default (no interview
   yet) - confirms the default-objectives reconstruction reproduces
   exactly today's pre-change search behavior before touching the
   interview surface at all.
3. **`interview.py` (`FINALIZE_SPEC_TOOL`, `_build_spec()`) + `prompts.py`.**
   Verify: a real interview against the live LLM server reproducing the
   motivating example - "L_loop 10% lower, L_rc within 2%, don't care about
   ESR or M" - end to end through `finalize_spec` into a real
   `optimize_loop` with real FEMM solves, confirming `goal_met`/ranking
   behave sensibly on a shape today's code cannot express at all.
4. **`orchestrator.py` CLI reporting + `agent_panel.py`.** Verify via the
   `run` skill - a real GUI session, a non-default objective set entered
   through the chat panel, confirming the plan summary / live comparison /
   diff table render correctly for both a passing and a deliberately-
   failing objective.
5. **Regression sweep**: `coil_simulation/tests/test_agent_variables.py`,
   `gui/tests/test_agent_panel.py`, `coil_simulation/tests/test_ai_run_repository.py`,
   plus a final repo-wide grep for `primary_metrics|min_esr_improvement_pct|
   primary_pass|m_errors_pct` confirming zero remaining code references
   (existing `.md` run logs under `coil_simulation/agent/docs/` keep
   mentioning the old shape as historical record - not touched).

## Critical files

- `coil_simulation/agent/objective.py` (new) - the vocabulary every other
  file in this plan builds on.
- `coil_simulation/agent/scorer.py`, `spec.py`, `variables.py`,
  `optimize.py` - the evaluator core being generalized.
- `coil_simulation/agent/interview.py`, `prompts.py` - how the LLM
  expresses and is told about the new vocabulary.
- `gui/src/gui/panels/agent_panel.py`, `coil_simulation/agent/orchestrator.py` -
  the only downstream display code that breaks (grep-confirmed complete list).
- `coil_simulation/models/result.py` - the authoritative 12-field list
  this whole plan is scoped to (confirmed, not assumed).

## Implementation notes (deviations from the design above)

Found in review before building; each one changes a detail of §1-§7.

1. **`match` takes a `target_pct`** (default 0 = hold baseline). The
   motivating example ("L_loop 10% lower") is a target with a band, which
   `reduce` can't express (`reduce 10` also passes at -40%). The interview
   prompt now asks "at least X% lower or about X% lower?" when the phrase is
   ambiguous.
2. **Every objective is checked at every distance, worst case wins.**
   `PER_DISTANCE_METRICS` was dropped: `SimResult`'s L values are measured
   with the RC coil and ferrite present at that distance, so they *do* move
   with separation, and every distance is solved anyway, so the stricter
   check is free. This is the one change to the default spec's behavior:
   ESR must now pass at every distance, not just the middle one.
3. **Ranking keeps `match` objectives as gates, not something to push
   further** (`scorer.rank_key`). Tier 0 = every required match holds,
   ordered by required reduce/increase margins (worst first), then
   preferences, then the fixed diameter/self-L tie-breakers. Tier 1 =
   ordered by how far outside the band(s). This avoids §2's flagged change
   (M23 headroom competing with ESR) and reproduces the old ordering exactly
   for `AgentSpec()`'s defaults - verified deterministically on 300 random
   candidates in `tests/test_agent_objective.py`, not by a live LLM run.
4. **Margin scale floor**: margins divide by `max(tolerance_pct, 1.0)`, not
   `tolerance_pct` with a raw fallback at 0 (that mixed units in one sort key).
5. **Validation** (`Objective.__post_init__`, `_build_spec`): a required
   `match` needs tolerance > 0; at least one objective must be required
   (otherwise the first valid candidate meets the goal and the run stops at
   round 1); one entry per metric.
6. **`tool_result()` is self-describing**: an `objectives` list, each entry
   carrying its own requirement text, per-distance change, pass/fail and
   margin. The GUI card renders from that with no spec needed.
7. **§6's call-site list was incomplete.** Also changed:
   `gui/src/gui/panels/agent_candidate_card.py` (`_metric_rows`, which also
   renders history replays - old runs keep a `_legacy_metric_rows` path)
   and `optimize.py`'s per-round history line. `agent_history.py` needed
   only a docstring update.

### Follow-up: no built-in assumptions (2026-09-11)

8. **Objectives can name any quantity**, not just the 12 solve outputs:
   any design variable that is free in the run (a CoilSet field) and the
   derived `loop/tank/rc_outer_diameter`. These have no distance.
   `_build_spec` rejects an objective on a field that isn't free, since no
   candidate could ever change it.
9. **Absolute targets**: `target_value` (in the quantity's own units)
   replaces the baseline-relative target. `match` = within
   ±tolerance_pct of it, `reduce` = at most it, `increase` = at least it.
10. **"Make y = z" is a pin, not an objective**: `VariableSpec.fixed_values`
    is applied to every candidate and removed from the free set.
    Comparisons stay against the original baseline, and the audit counts
    pinned fields as allowed.
11. **No built-in tie-breakers.** The scorer's fixed "smaller tank/RC
    diameter, lower tank/RC self-L" tie-break is gone, because it would pull
    against a user asking for more L. `AgentSpec()`'s CLI default keeps those
    as explicit preference objectives. An interview gets only what the user
    asked for.
12. **The optimize prompt's physics reference is computed per run.** It
    says what each lever does to each quantity, in both directions, with no
    "main levers" and no M23/ESR framing. Coil radii, the distance sweep,
    allowed thicknesses, ferrite/metal notes (only when those fields are
    free) and each objective's baseline value are all read from the actual
    baseline and spec.
13. `excluded_fields` is now validated. A feature name there (e.g.
    `tank_ferrite`) excludes that whole feature, and anything else unknown
    is an error. Before this, bad names were silently ignored.
