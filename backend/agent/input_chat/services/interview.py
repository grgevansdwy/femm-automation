"""The interview phase: a chat conversation that gathers an objective from
the user and turns it into a finalized AgentSpec - the thing the optimize
phase (agent/femm_agent/services/optimize.py) actually searches against. Reuses the same
call_llm()/tool-calling shape as the optimize phase's propose_coilset loop,
just with different tools (finalize_spec, flag_off_topic) and a different
system prompt.

The handoff to the optimize phase is the AgentSpec itself - once
InterviewState.finalized is True, agent/services/runner.py's AgentRunner.submit()
takes state.spec and neither it nor optimize.py need to know a
conversation ever happened.
"""

import copy
import dataclasses
import json

from agent import config
from agent.services.llm_client import call_llm
from agent.input_chat.models.objective import DERIVED_QUANTITIES, KINDS, QUANTITY_GLOSSES, SIM_RESULT_METRICS, Objective, is_per_distance
from agent.femm_agent.services.scorer import design_values
from agent.input_chat.models.spec import AgentSpec
from agent.settings.settings import AgentSettings
from agent.settings.templating import render
from agent.input_chat.models.variables import (
    CONSENT_GROUPS, VariableSpec, coerce_fixed_values, free_fields_for, is_group_present, resolve_excluded,
)

_DEFAULT_MAX_DISTANCES = AgentSettings().max_distances

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
                "distances": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": _DEFAULT_MAX_DISTANCES,
                              "description": f"RC separation distances (mm) to evaluate at. At most "
                                              f"{_DEFAULT_MAX_DISTANCES} - each one is a full extra FEMM solve per "
                                              "candidate, so ask the user to narrow it down if they name more."},
                "enabled_groups": {
                    "type": "array", "items": {"type": "string", "enum": list(CONSENT_GROUPS)},
                    "description": "Structural details the user EXPLICITLY said the search may change - "
                                    "e.g. 'tank_ferrite' to let it adjust (or add/remove) tank ferrite, "
                                    "'pcb_thickness' to let board thickness move. Nothing here is free by "
                                    "default, even if the baseline already has it (e.g. ferrite fitted) - "
                                    "only include a group the user actually asked to open up.",
                },
                "excluded_fields": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Fields to keep at their baseline value even though they'd otherwise "
                                    "be free. A group name (e.g. 'tank_ferrite') keeps that whole group fixed.",
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


FLAG_OFF_TOPIC_TOOL = [{
    "type": "function",
    "function": {
        "name": "flag_off_topic",
        "description": (
            "Call this INSTEAD of replying normally, and instead of finalize_spec, when the "
            "user's latest message is not about this coil-design task (general chat, another "
            "topic, code, anything unrelated). Whatever you put in `reason` is never shown to "
            "the user - a fixed message is shown instead - so it's only for your own record."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "One short phrase: what the message was actually about."},
            },
            "required": [],
        },
    },
}]


def _interview_tools(max_distances: int) -> list[dict]:
    """The two tools offered on a normal interview turn: FINALIZE_SPEC_TOOL
    (distances cap set to `max_distances` - the constant above is only the
    default) plus FLAG_OFF_TOPIC_TOOL. Giving the model a dedicated way to
    signal "this isn't relevant" - instead of trusting it to decline
    gracefully in free text - is what makes the redirect message below
    code-controlled rather than whatever the model happens to write; see
    docs/agent-skills.md."""
    tool = copy.deepcopy(FINALIZE_SPEC_TOOL)
    distances = tool[0]["function"]["parameters"]["properties"]["distances"]
    distances["maxItems"] = max_distances
    distances["description"] = distances["description"].replace(str(_DEFAULT_MAX_DISTANCES), str(max_distances))
    return tool + FLAG_OFF_TOPIC_TOOL


def interview_system_prompt(baseline_config_id: str, baseline, settings: AgentSettings | None = None) -> str:
    """Scopes the model to FEMM-simulation objective-gathering only (a
    prompt-level guardrail, not a hard filter), and tells it exactly which
    structural details it must actively ask about before ever assuming
    they're open to change - the rule is strict and uniform: fixed unless
    the user explicitly says otherwise, whether or not the baseline
    already has that detail (ferrite fitted does NOT make ferrite free).
    """
    # Physical presence is purely descriptive here - it tells the model
    # what the design already has, never what it may change (that's
    # consent alone, via enabled_groups). pcb_thickness has no on/off flag
    # (its fields always exist), so it's mentioned separately, not in this
    # present/absent split.
    toggled = [g for g, info in CONSENT_GROUPS.items() if info["flag"]]
    present = [g for g in toggled if is_group_present(baseline, g)]
    absent = [g for g in toggled if g not in present]
    sim_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}" for m in SIM_RESULT_METRICS)
    design = design_values(baseline)
    derived_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}, baseline {design[m]:.4g}" for m in DERIVED_QUANTITIES)
    variable_lines = "\n".join(f"  {f}: {QUANTITY_GLOSSES[f]}, baseline {design[f]:g}"
                               for f in free_fields_for(baseline))
    settings = settings or AgentSettings()
    return render(settings.prompt("interview_system"), {
        "baseline_config_id": baseline_config_id,
        "present": present or "none of the optional structural details",
        "absent": absent or "none",
        "sim_lines": sim_lines,
        "derived_lines": derived_lines,
        "variable_lines": variable_lines,
        "default_distances": config.DISTANCES,
    })


@dataclasses.dataclass
class InterviewState:
    baseline_config_id: str
    transcript: list[dict] = dataclasses.field(default_factory=list)
    spec: AgentSpec | None = None
    finalized: bool = False
    error: str | None = None
    # The current turn's chain-of-thought, growing live while an LLM request
    # is in flight (planning here, or a follow-up - agent/femm_agent/services/followup.py reuses
    # this same InterviewState/field). Reset at the start of every turn; the
    # GUI only reads it while InterviewRunner.busy is True.
    live_reasoning: str = ""
    # The settings this conversation runs under (prompt wording, LLM limits) -
    # fixed when the conversation starts, and handed on to the AgentSpec it produces.
    settings: AgentSettings = dataclasses.field(default_factory=AgentSettings)


def _build_spec(args: dict, baseline_config_id: str, baseline, settings: AgentSettings | None = None) -> AgentSpec:
    """Raises ValueError on bad finalize_spec args - the caller turns that
    into a rejected tool result (same "validation error is feedback for the
    model, not a crash" pattern agent/femm_agent/models/schema.py's coerce_delta uses)."""
    settings = settings or AgentSettings()
    distances = [float(d) for d in args["distances"]]
    if not distances:
        raise ValueError("distances must be a non-empty list")
    if len(distances) > settings.max_distances:
        raise ValueError(f"distances: at most {settings.max_distances} allowed, got {len(distances)} - "
                          "ask the user which ones matter most")

    enabled_groups = set(args.get("enabled_groups") or [])
    unknown = enabled_groups - set(CONSENT_GROUPS)
    if unknown:
        raise ValueError(f"enabled_groups contains unknown group(s): {sorted(unknown)}")

    variable_spec = VariableSpec(
        enabled_groups=enabled_groups,
        excluded_fields=resolve_excluded(args.get("excluded_fields") or []),  # raises on a typo'd name
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
                             "enable its group / stop excluding it, or drop the objective")
        if o.target_value is None and not is_per_distance(o.metric) and design[o.metric] == 0:
            raise ValueError(f"{o.metric} is 0 on the baseline, so a % change is undefined - use target_value")

    return dataclasses.replace(
        AgentSpec.from_settings(settings, baseline_config_id=baseline_config_id),
        distances=distances,
        objective_text=args.get("objective_text", ""),
        variable_spec=variable_spec,
        objectives=objectives,
    )


def run_interview_turn(state: InterviewState, user_message: str | None = None, should_abort=None) -> None:
    """One exchange, mutated in place - the same in-place-mutation
    convention agent/femm_agent/models/state.py's AgentRunState uses, so a background thread
    can run this while a render thread polls `state`. `user_message=None`
    is valid for the very first call, letting the model open with its own
    clarifying question before the user has said anything."""
    from engine.repositories.coilset_repository import get_coilset  # deferred - resolves via agent/__init__.py's sys.path bootstrap
    baseline = get_coilset(state.baseline_config_id)

    if user_message is not None:
        state.transcript.append({"role": "user", "content": user_message})

    state.live_reasoning = ""
    system_message = {"role": "system", "content": interview_system_prompt(state.baseline_config_id, baseline, state.settings)}

    def _ask(tools):
        """One call_llm() round trip, wired the same way every time this
        function needs one: same system prompt + the transcript so far, same
        live-reasoning callback, same error handling. `tools=None` is how a
        reply is forced to be plain text - see the follow-up call below."""
        return call_llm(
            [system_message] + state.transcript, tools, settings=state.settings, should_abort=should_abort,
            on_update=lambda content, reasoning: setattr(state, "live_reasoning", reasoning or content),
        )

    def _toolless_followup() -> bool:
        """One more call_llm() round trip with NO tools offered - so the
        reply can only be plain text - used after both a flag_off_topic and
        a finalize_spec rejection. The model still has the whole
        conversation in view (including whatever it was asking before this
        turn started), so this reliably continues naturally instead of a
        fixed, context-blind line doing it. Returns False on a transport
        failure (state.error already set), True once the reply has landed."""
        try:
            state.transcript.append(_ask(None))
        except Exception as exc:
            state.error = str(exc)
            return False
        return True

    try:
        msg = _ask(_interview_tools(state.settings.max_distances))
    except Exception as exc:
        state.error = str(exc)
        return
    state.transcript.append(msg)

    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return  # a clarifying question (or other plain text) - caller reads state.transcript[-1]["content"]

    call = tool_calls[0]
    if call["function"]["name"] == "flag_off_topic":
        # The fixed redirect bubble (agent_panel.py's _message_display_text)
        # never shows the model's own words for THIS message, so an
        # off-topic message can't make it engage with the content directly.
        # But leaving it there is a dead end if something was already being
        # asked - the one extra toolless turn below lets the model pick
        # back up naturally (re-ask a pending question, etc.) instead of
        # the fixed line looking like the conversation just restarted.
        state.transcript.append({"role": "tool", "content": json.dumps({"valid": False, "off_topic": True})})
        _toolless_followup()
        return

    args = call["function"]["arguments"]
    try:
        state.spec = _build_spec(args, state.baseline_config_id, baseline, state.settings)
        state.finalized = True
        result = {"valid": True, "message": "Spec finalized."}
    except (ValueError, KeyError) as exc:
        result = {"valid": False, "error": str(exc)}
    state.transcript.append({"role": "tool", "content": json.dumps(result)})

    if result["valid"]:
        return

    # A rejection's error is a raw validation string, not something a person
    # should have to read - the same toolless follow-up turns it into an
    # actual question. No tools means the model cannot retry finalize_spec
    # itself here even if it wanted to, so this always ends with the user
    # being asked, never with a silent, unseen second attempt (see
    # docs/agent-skills.md).
    _toolless_followup()
