"""The interview phase: a chat conversation that gathers an objective from
the user and turns it into a finalized AgentSpec - the thing the optimize
phase (agent/optimize.py) actually searches against. Reuses the same
call_llm()/tool-calling shape as the optimize phase's propose_coilset loop,
just with a different tool (finalize_spec) and a different system prompt.

The handoff to the optimize phase is the AgentSpec itself - once
InterviewState.finalized is True, agent/runner.py's AgentRunner.submit()
takes state.spec and neither it nor optimize.py need to know a
conversation ever happened.
"""

import dataclasses
import json

from agent.config import MAX_DISTANCES
from agent.llm_client import call_llm
from agent.objective import DERIVED_QUANTITIES, KINDS, QUANTITY_GLOSSES, SIM_RESULT_METRICS, Objective, is_per_distance
from agent.scorer import design_values
from agent.spec import AgentSpec
from agent.variables import (
    FEATURE_FLAGS, VariableSpec, coerce_fixed_values, free_fields_for, is_feature_active, resolve_excluded,
)

FINALIZE_SPEC_TOOL = [{
    "type": "function",
    "function": {
        "name": "finalize_spec",
        "description": (
            "Call this once, and only once, when you have enough to start the search: a clear "
            "objective, at least one distance, and an explicit answer (or defaulted silence, per "
            "the stated default rule) on which variables must stay fixed. Do not call this while "
            "you still need to ask a clarifying question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "objective_text": {"type": "string", "description": "The user's objective, in their own terms."},
                "distances": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": MAX_DISTANCES,
                              "description": f"RC separation distances (mm) to evaluate at. At most "
                                              f"{MAX_DISTANCES} - each one is a full extra FEMM solve per "
                                              "candidate, so ask the user to narrow it down if they name more."},
                "enabled_features": {
                    "type": "array", "items": {"type": "string", "enum": list(FEATURE_FLAGS)},
                    "description": "Structural features to explicitly turn on for the search, "
                                    "beyond whatever's already active on the baseline (e.g. "
                                    "'tank_ferrite' if the user asked to explore adding ferrite "
                                    "that the baseline doesn't have).",
                },
                "excluded_fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Fields to keep at their baseline value even though they'd otherwise "
                                    "be free. A feature name (e.g. 'tank_ferrite') keeps that whole feature fixed.",
                },
                "include_pcb_thickness": {
                    "type": "boolean",
                    "description": "True only if the user explicitly said PCB thickness may change.",
                },
                "fixed_values": {
                    "type": "object", "additionalProperties": {"type": "number"},
                    "description": "Design variables the user said must BE a specific value "
                                    "({field: value}, e.g. {\"rc_trace_thickness\": 0.14}) - applied to every "
                                    "candidate and never searched over.",
                },
                "objectives": {
                    "type": "array", "minItems": 1,
                    "description": "Exactly what the user asked for, one entry per quantity - and "
                                    "nothing they didn't ask for. At least one must be required.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {
                                "type": "string", "enum": list(QUANTITY_GLOSSES),
                                "description": "The quantity: a simulated result, an outer diameter, or a "
                                                "design variable that is free in this search.",
                            },
                            "kind": {"type": "string", "enum": list(KINDS)},
                            "tolerance_pct": {
                                "type": "number", "minimum": 0,
                                "description": "match: allowed +/- band (%) around the target. reduce/increase "
                                                "(relative): the minimum % drop/rise required - 0 means only "
                                                "'no higher'/'no lower' than baseline. With target_value, "
                                                "reduce/increase are plain bounds: use 0.",
                            },
                            "target_pct": {
                                "type": "number",
                                "description": "Relative match only: the % change vs baseline to hit (e.g. -10 "
                                                "for '10% lower'). Omit or 0 to hold the baseline value.",
                            },
                            "target_value": {
                                "type": "number",
                                "description": "An absolute value in the quantity's own units (H, Ohm, mm, ...) "
                                                "instead of a % of baseline. match: stay within +/-tolerance_pct "
                                                "of it; reduce: at most it; increase: at least it.",
                            },
                            "required": {
                                "type": "boolean",
                                "description": "true (default) = must pass for the goal to be met; "
                                                "false = a preference that only breaks ties.",
                            },
                        },
                        "required": ["metric", "kind", "tolerance_pct"],
                    },
                },
            },
            "required": ["objective_text", "distances", "objectives"],
        },
    },
}]


def interview_system_prompt(baseline_config_id: str, baseline) -> str:
    """Scopes the model to FEMM-simulation objective-gathering only (a
    prompt-level guardrail, not a hard filter), and tells it exactly which
    structural features it must actively ask about before ever assuming
    they're available - the default rule is baseline-active-fields-stay-
    free, everything-inactive-stays-fixed unless the user says otherwise.
    """
    active = [f for f in FEATURE_FLAGS if is_feature_active(baseline, f)]
    inactive = [f for f in FEATURE_FLAGS if f not in active]
    sim_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}" for m in SIM_RESULT_METRICS)
    design = design_values(baseline)
    derived_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}, baseline {design[m]:.4g}" for m in DERIVED_QUANTITIES)
    variable_lines = "\n".join(f"  {f}: {QUANTITY_GLOSSES[f]}, baseline {design[f]:g}"
                               for f in free_fields_for(baseline))
    return f"""You are gathering the objective for a wireless power transfer coilset
optimization search. Only discuss this coilset's FEMM simulation objective -
politely decline anything unrelated (other topics, general chat, code, etc.)
and steer back to the task.

Baseline coilset: {baseline_config_id}.
Structural features currently ACTIVE on this baseline (already free to
change unless the user says to keep them fixed): {active or "none"}.
Structural features currently INACTIVE on this baseline (excluded from the
search - the model cannot touch them - unless the user EXPLICITLY asks to
enable one): {inactive or "none"}.
PCB thickness (loop/tank/rc) is always fixed unless the user explicitly
says it may change.
The objective is a list of entries, each over ONE quantity:
Simulated results (solved at every distance - the worst distance decides):
{sim_lines}
Outer diameters (pure geometry, no distance):
{derived_lines}
Design variables free on this baseline by default (no distance) - a
feature the user enables adds its own fields, and a field they exclude or
pin drops out:
{variable_lines}
Coil naming, so you pick the right quantity - never guess the index:
  loop and tank are the transmit side (TC), RC is the receive coil.
  "tank-to-RC", "TX-RX", "TC-RC", or the power-transfer link means M23/k23.
Each entry has a kind, relative to the baseline value by default:
  match    - stay within +/-tolerance_pct of target_pct (0 = hold baseline;
             e.g. target_pct=-10 for "10% lower", with a band around it)
  reduce   - drop by AT LEAST tolerance_pct (0 = just no higher than baseline)
  increase - rise by AT LEAST tolerance_pct
or, with target_value, relative to an absolute value in the quantity's own
units (H, Ohm, mm, ...): match = within +/-tolerance_pct of it, reduce = at
most it, increase = at least it.
Each entry is either required (must pass for the goal to be met) or a
preference (required=false - only breaks ties between candidates).
If the user says a design variable must BE a value ("use 4oz copper on the
RC", "make it 5 tank turns"), that's not an objective - put it in
fixed_values, which applies it to every candidate and takes it out of the
search.
Include exactly what the user asked for and nothing else - there is no
hidden default objective and no built-in tie-breaker. If a phrase like
"10% lower" could mean either "at least 10% lower" (reduce) or "about 10%
lower" (match with a target), ask which, stating the band you'd otherwise
use.

Always reply concisely - one or two short sentences, never a long
explanation or a numbered list of questions. The user already stated their
objective and any requirements in their own words in their first message -
work with what they actually gave you.

For anything they left unstated, don't silently guess AND don't dump a
list of questions either: ask ONE short, natural question at a time,
stating the default you'd otherwise use right in the question, e.g. "Any
particular distances you'd like, or should I default to a single
representative separation?" or "tank_ferrite isn't on this baseline, so
I'll leave it fixed unless you want it added - correct?". Wait for their
answer (or them saying "that's fine" / "use the default"), then move to
the next thing you still need, one question per turn. Once you have
enough - after confirming defaults for anything that mattered, not before
the user has had a chance to weigh in - call finalize_spec. Don't ask
about something the user's own message already answered.

The user sees your finalize_spec call rendered as a plan they must
explicitly confirm (a "Start Search" button) before anything runs, and can
send another message to revise it if it's wrong - so it's fine to finalize
as soon as you've confirmed the things that matter, rather than exhaustively
covering every field."""


@dataclasses.dataclass
class InterviewState:
    baseline_config_id: str
    transcript: list[dict] = dataclasses.field(default_factory=list)
    spec: AgentSpec | None = None
    finalized: bool = False
    error: str | None = None


def _build_spec(args: dict, baseline_config_id: str, baseline) -> AgentSpec:
    """Raises ValueError on bad finalize_spec args - the caller turns that
    into a rejected tool result (same "validation error is feedback for the
    model, not a crash" pattern agent/schema.py's coerce_delta uses)."""
    distances = [float(d) for d in args["distances"]]
    if not distances:
        raise ValueError("distances must be a non-empty list")
    if len(distances) > MAX_DISTANCES:
        raise ValueError(f"distances: at most {MAX_DISTANCES} allowed, got {len(distances)} - "
                          "ask the user which ones matter most")

    enabled_features = set(args.get("enabled_features") or [])
    unknown = enabled_features - set(FEATURE_FLAGS)
    if unknown:
        raise ValueError(f"enabled_features contains unknown feature(s): {sorted(unknown)}")

    variable_spec = VariableSpec(
        enabled_features=enabled_features,
        excluded_fields=resolve_excluded(args.get("excluded_fields") or []),  # raises on a typo'd name
        include_pcb_thickness=bool(args.get("include_pcb_thickness", False)),
        fixed_values=coerce_fixed_values(dict(args.get("fixed_values") or {})),
    )
    # An interview that excludes/pins every free field would leave the
    # optimize phase with nothing to propose.
    free_fields = free_fields_for(baseline, variable_spec)
    if not free_fields:
        raise ValueError("excluded_fields/fixed_values leave no free fields to search over")

    # No fallback to AgentSpec's default objectives here: an interview that
    # never mentioned M or ESR must not quietly get an M23 gate and an ESR
    # requirement anyway. Objective() itself rejects unknown quantities/kinds.
    objectives = [
        Objective(
            metric=entry["metric"], kind=entry["kind"],
            tolerance_pct=float(entry["tolerance_pct"]),
            required=bool(entry.get("required", True)),
            target_pct=float(entry.get("target_pct") or 0.0),
            target_value=None if entry.get("target_value") is None else float(entry["target_value"]),
        )
        for entry in args["objectives"]
    ]
    if not any(o.required for o in objectives):
        raise ValueError("objectives needs at least one required entry - with only preferences, "
                         "the very first valid candidate would already meet the goal")
    if len({o.metric for o in objectives}) != len(objectives):
        raise ValueError("objectives lists the same quantity more than once - use one entry per quantity")
    design = design_values(baseline)
    for o in objectives:
        if o.metric in variable_spec.fixed_values:
            raise ValueError(f"{o.metric} is pinned by fixed_values, so it can't also be an objective")
        if not is_per_distance(o.metric) and o.metric not in DERIVED_QUANTITIES and o.metric not in free_fields:
            raise ValueError(f"{o.metric} isn't free in this search, so no candidate could ever change it - "
                             "enable its feature / stop excluding it, or drop the objective")
        if o.target_value is None and not is_per_distance(o.metric) and design[o.metric] == 0:
            raise ValueError(f"{o.metric} is 0 on the baseline, so a % change is undefined - use target_value")

    return dataclasses.replace(
        AgentSpec(baseline_config_id=baseline_config_id),
        distances=distances,
        objective_text=args.get("objective_text", ""),
        variable_spec=variable_spec,
        objectives=objectives,
    )


def run_interview_turn(state: InterviewState, user_message: str | None = None) -> None:
    """One exchange, mutated in place - the same in-place-mutation
    convention agent/state.py's AgentRunState uses, so a background thread
    can run this while a render thread polls `state`. `user_message=None`
    is valid for the very first call, letting the model open with its own
    clarifying question before the user has said anything."""
    from repositories.coilset_repository import get_coilset  # deferred - resolves via agent/__init__.py's sys.path bootstrap
    baseline = get_coilset(state.baseline_config_id)

    if user_message is not None:
        state.transcript.append({"role": "user", "content": user_message})

    system_message = {"role": "system", "content": interview_system_prompt(state.baseline_config_id, baseline)}
    send_messages = [system_message] + state.transcript

    try:
        msg = call_llm(send_messages, FINALIZE_SPEC_TOOL)
    except Exception as exc:
        state.error = str(exc)
        return
    state.transcript.append(msg)

    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return  # a clarifying question (or other plain text) - caller reads state.transcript[-1]["content"]

    args = tool_calls[0]["function"]["arguments"]
    try:
        state.spec = _build_spec(args, state.baseline_config_id, baseline)
        state.finalized = True
        result = {"valid": True, "message": "Spec finalized."}
    except (ValueError, KeyError) as exc:
        result = {"valid": False, "error": str(exc)}
    state.transcript.append({"role": "tool", "content": json.dumps(result)})
