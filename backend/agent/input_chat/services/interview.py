"""The interview phase: a chat conversation that gathers an objective from
the user and turns it into a finalized AgentSpec - the thing the optimize
phase (agent/femm_agent/services/optimize.py) actually searches against. Reuses the same
call_llm()/tool-calling shape as the optimize phase's propose_coilset loop,
just with different tools (finalize_spec, flag_off_topic) and a different
system prompt.

The handoff to the optimize phase is the AgentSpec itself - once
InterviewState.finalized is True, agent/femm_agent/services/runner.py's AgentRunner.submit()
takes state.spec and neither it nor optimize.py need to know a
conversation ever happened.
"""

import copy
import dataclasses
import json

from agent import config
from agent.services.llm_client import call_llm
from agent.input_chat.models.objective import (
    DERIVED_QUANTITIES, KINDS, NOT_OBJECTIVE_METRICS, QUANTITY_GLOSSES, SIM_RESULT_METRICS, Objective, is_per_distance,
)
from agent.femm_agent.services.scorer import design_values
from agent.input_chat.models.spec import AgentSpec
from agent.settings.settings import AgentSettings
from agent.settings.templating import render
from agent.femm_agent.models.schema import limit_text
from agent.input_chat.models.variables import (
    NUMERIC_FIELDS, VariableSpec, check_field_changes, coerce_fixed_values, free_fields_for, limit_problems,
    resolve_excluded, search_field_table,
)

_DEFAULT_MAX_DISTANCES = AgentSettings().max_distances
# The RC separation a search may simulate - the same range agent_cli.py accepts.
MIN_DISTANCE_MM, MAX_DISTANCE_MM = 0.0, 200.0

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
                                "type": "string", "enum": [q for q in QUANTITY_GLOSSES if q not in NOT_OBJECTIVE_METRICS],
                                "description": "The quantity: a simulated result, an outer diameter, or a "
                                                "design variable that is free in this search. Coupling "
                                                "coefficients (k12/k13/k23) aren't offered here - they're "
                                                "derived from M and L, not independently controllable; set "
                                                "the objective on the underlying M (and/or L) instead.",
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
                "favourite_metric": {
                    "type": "string",
                    "description": "Only when the user said ONE required reduce/increase objective should be "
                                    "pushed as far as possible: that objective's metric. Designs that meet the "
                                    "goal are then compared on it alone. Omit it otherwise - they are compared "
                                    "on all required reduce/increase objectives together.",
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


PROPOSE_FIELDS_TOOL_NAME = "propose_search_fields_change"

PROPOSE_SEARCH_FIELDS_TOOL = [{
    "type": "function",
    "function": {
        "name": PROPOSE_FIELDS_TOOL_NAME,
        "description": (
            "Propose a change to the Search fields table in Settings - which design fields a search "
            "may change, and each one's minimum and maximum. Call this INSTEAD of finalize_spec when "
            "the user wants the search to change a field that is closed, or when a limit does not fit "
            "what they asked for. This only SHOWS the proposal to the user with an Apply button: you "
            "cannot apply it, and nothing changes unless they click Apply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": list(NUMERIC_FIELDS)},
                            "enabled": {"type": "boolean", "description": "true = the search may change this field."},
                            "min": {"type": ["number", "null"], "description": "Lowest allowed value, in the field's own unit; null = no minimum."},
                            "max": {"type": ["number", "null"], "description": "Highest allowed value, in the field's own unit; null = no maximum."},
                        },
                        "required": ["field"],
                    },
                },
                "reason": {"type": "string", "description": "One short sentence the user reads: why this change is needed."},
            },
            "required": ["changes", "reason"],
        },
    },
}]


def _interview_tools(max_distances: int) -> list[dict]:
    """The tools offered on a normal interview turn: FINALIZE_SPEC_TOOL
    (distances cap set to `max_distances` - the constant above is only the
    default), FLAG_OFF_TOPIC_TOOL and PROPOSE_SEARCH_FIELDS_TOOL. Giving the model a dedicated way to
    signal "this isn't relevant" - instead of trusting it to decline
    gracefully in free text - is what makes the redirect message below
    code-controlled rather than whatever the model happens to write; see
    docs/agent-skills.md."""
    return finalize_spec_tool(max_distances) + FLAG_OFF_TOPIC_TOOL + PROPOSE_SEARCH_FIELDS_TOOL


def finalize_spec_tool(max_distances: int) -> list[dict]:
    """FINALIZE_SPEC_TOOL with its distances cap set to `max_distances`. The
    follow-up chat offers it too once a search has ended, so the user can
    change the goal there (agent/femm_agent/services/followup.py)."""
    tool = copy.deepcopy(FINALIZE_SPEC_TOOL)
    distances = tool[0]["function"]["parameters"]["properties"]["distances"]
    distances["maxItems"] = max_distances
    distances["description"] = distances["description"].replace(str(_DEFAULT_MAX_DISTANCES), str(max_distances))
    return tool


def quantity_lines(baseline, settings: AgentSettings) -> dict:
    """What a prompt needs before the model can write a plan for `baseline`
    under `settings`' Search fields table: the simulated quantities, the outer
    diameters, the open design variables with their baseline values and
    limits, and the names of the closed ones. Used by the interview prompt
    and, once a search has ended, by the follow-up prompt."""
    sim_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}" for m in SIM_RESULT_METRICS)
    design = design_values(baseline)
    derived_lines = "\n".join(f"  {m}: {QUANTITY_GLOSSES[m]}, baseline {design[m]:.4g}" for m in DERIVED_QUANTITIES)
    def _variable_line(name, schema):
        # Each open field with what it may be set to: a range, or a discrete field's
        # exact values (e.g. trace thickness) - "must be one of" is the wording the
        # prompt's discrete-field rule refers to.
        allowed = f"must be one of {schema['enum']}" if schema.get("enum") else f"allowed: {limit_text(schema)}"
        return f"  {name}: {QUANTITY_GLOSSES[name]}, baseline {design[name]:g} - {allowed}"

    open_fields = free_fields_for(baseline, None, search_field_table(settings))
    variable_lines = "\n".join(_variable_line(f, s) for f, s in open_fields.items())
    return {"sim_lines": sim_lines, "derived_lines": derived_lines, "variable_lines": variable_lines,
            "closed_fields": ", ".join(n for n in NUMERIC_FIELDS if n not in open_fields) or "none"}


def interview_system_prompt(baseline_config_id: str, baseline, settings: AgentSettings | None = None) -> str:
    """Scopes the model to FEMM-simulation objective-gathering only (a
    prompt-level guardrail, not a hard filter), and tells it which fields the
    search may change and within what limits - the Search fields table in
    force for this conversation (`settings`). The model reads the table; it
    cannot change it (propose_search_fields_change only shows a proposal).
    """
    settings = settings or AgentSettings()
    return render(settings.prompt("interview_system"), {
        "baseline_config_id": baseline_config_id,
        **quantity_lines(baseline, settings),
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
    # A change to the Search fields table the model proposed and the user has
    # not yet applied or dismissed: {"changes": [{field, enabled?, min?, max?}],
    # "reason": str}. Only shown - applying it is the user's click
    # (agent.settings.settings.apply_search_field_changes), never this module's.
    pending_field_change: dict | None = None


def _build_spec(args: dict, baseline_config_id: str, baseline, settings: AgentSettings | None = None) -> AgentSpec:
    """Raises ValueError on bad finalize_spec args - the caller turns that
    into a rejected tool result (same "validation error is feedback for the
    model, not a crash" pattern agent/femm_agent/models/schema.py's coerce_delta uses)."""
    settings = settings or AgentSettings()
    distances = list(dict.fromkeys(float(d) for d in args["distances"]))  # duplicates dropped, order kept
    if not distances:
        raise ValueError("distances must be a non-empty list")
    out_of_range = [d for d in distances if not MIN_DISTANCE_MM < d <= MAX_DISTANCE_MM]  # also catches NaN
    if out_of_range:
        raise ValueError(f"distances must be above {MIN_DISTANCE_MM:g} and at most {MAX_DISTANCE_MM:g} mm, "
                         f"got {out_of_range}")
    if len(distances) > settings.max_distances:
        raise ValueError(f"distances: at most {settings.max_distances} allowed, got {len(distances)} - "
                          "ask the user which ones matter most")

    # Which fields are open, and their limits, come from the Search fields table
    # alone - nothing in the model's arguments can open a field or widen a limit.
    table = search_field_table(settings)
    variable_spec = VariableSpec(
        excluded_fields=resolve_excluded(args.get("excluded_fields") or []),  # raises on a typo'd name
        fixed_values=coerce_fixed_values(dict(args.get("fixed_values") or {}), table),  # raises outside a limit
    )
    # An interview that excludes/pins every free field would leave the
    # optimize phase with nothing to propose.
    free_fields = free_fields_for(baseline, variable_spec, table)
    if not free_fields:
        raise ValueError("excluded_fields/fixed_values leave no free fields to search over")
    problems = limit_problems(baseline, variable_spec, table)
    if problems:
        raise ValueError("; ".join(problems) + " - the search cannot start from a baseline outside a limit. "
                         "Propose a change to that limit, or exclude the field.")

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
                             "propose enabling it in Search fields / stop excluding it, or drop the objective")
        if o.target_value is None and not is_per_distance(o.metric) and design[o.metric] == 0:
            raise ValueError(f"{o.metric} is 0 on the baseline, so a % change is undefined - use target_value")

    favourite = args.get("favourite_metric") or None
    pushable = [o.metric for o in objectives if o.required and o.kind != "match"]
    if favourite is not None and favourite not in pushable:
        raise ValueError(f"favourite_metric {favourite!r} must be one of the required reduce/increase "
                         f"objectives ({pushable or 'this plan has none'}) - or leave it out")

    return dataclasses.replace(
        AgentSpec.from_settings(settings, baseline_config_id=baseline_config_id),
        favourite_metric=favourite,
        distances=distances,
        objective_text=args.get("objective_text", ""),
        variable_spec=variable_spec,
        objectives=objectives,
    )


def plan_problems(spec: AgentSpec, baseline) -> list[str]:
    """Why an already-built plan can't start under the Search fields table its
    settings now carry - the same checks _build_spec() makes, re-run when the
    table is copied into the plan again at Start Search (it may have been
    edited in Settings since the plan was proposed). Empty list = it can start."""
    table = search_field_table(spec.settings)
    vs = spec.variable_spec
    problems = []
    try:
        coerce_fixed_values(dict(vs.fixed_values), table)
    except ValueError as exc:
        problems.append(f"Pinned value - {exc}")
    free_fields = free_fields_for(baseline, vs, table)
    if not free_fields:
        problems.append("No field is left open for the search to change")
    problems += limit_problems(baseline, vs, table)
    problems += [f"{o.metric} has an objective but is closed in Search fields"
                 for o in spec.objectives
                 if not is_per_distance(o.metric) and o.metric not in DERIVED_QUANTITIES and o.metric not in free_fields]
    return problems


def spec_arguments(spec: AgentSpec) -> dict:
    """`spec` written back as finalize_spec arguments - what the model is shown
    as the plan in force when the user asks to change it, so it resends the
    plan with only that change instead of rewriting it from memory."""
    args = {
        "objective_text": spec.objective_text,
        "distances": list(spec.distances),
        "objectives": [],
    }
    for o in spec.objectives:
        entry = {"metric": o.metric, "kind": o.kind, "tolerance_pct": o.tolerance_pct, "required": o.required}
        if o.target_pct:
            entry["target_pct"] = o.target_pct
        if o.target_value is not None:
            entry["target_value"] = o.target_value
        args["objectives"].append(entry)
    variable_spec = spec.variable_spec
    if variable_spec.excluded_fields:
        args["excluded_fields"] = sorted(variable_spec.excluded_fields)
    if variable_spec.fixed_values:
        args["fixed_values"] = dict(variable_spec.fixed_values)
    if spec.favourite_metric:
        args["favourite_metric"] = spec.favourite_metric
    return args


def accept_finalize_call(function: dict, baseline_config_id: str, baseline, settings: AgentSettings,
                         log=None, where: str = "run_interview_turn") -> tuple[AgentSpec | None, dict]:
    """Checks one finalize_spec call: (the plan, an accepting tool result) or
    (None, a rejecting one). Never raises - whatever shape the model's
    arguments have (a string where a list belongs raises TypeError, a
    missing key KeyError, ...), it is a rejected plan the user gets asked
    about, never a dead turn. Shared by the interview and by the follow-up
    chat, so a plan is held to the same rules whenever it is proposed."""
    args = function.get("arguments")
    try:
        if not isinstance(args, dict):
            raise ValueError(f"finalize_spec's arguments must be a JSON object, got {type(args).__name__}")
        return _build_spec(args, baseline_config_id, baseline, settings), {"valid": True, "message": "Spec finalized."}
    except Exception as exc:
        if log is not None and not isinstance(exc, ValueError):
            log.warning(where, "finalize_spec arguments could not be read", exc)
        return None, {"valid": False,
                      "error": f"{type(exc).__name__}: {exc}" if not isinstance(exc, ValueError) else str(exc)}


def accept_field_change_call(function: dict) -> tuple[dict | None, dict]:
    """Checks one propose_search_fields_change call: (the proposal to show the
    user, an accepting tool result) or (None, a rejecting one). Nothing is
    written anywhere: the Search fields table changes only on the user's own
    click (agent.settings.settings.apply_search_field_changes)."""
    args = function.get("arguments")
    try:
        changes = check_field_changes(args.get("changes") if isinstance(args, dict) else None)
        return ({"changes": changes, "reason": str(args.get("reason") or "")},
                {"valid": True, "message": "Shown to the user. Nothing has changed - the table changes only "
                                           "if they click Apply."})
    except (ValueError, KeyError, TypeError) as exc:
        return None, {"valid": False, "error": str(exc)}


def run_interview_turn(state: InterviewState, user_message: str | None = None, should_abort=None,
                       log=None) -> None:
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
            [system_message] + state.transcript, tools, settings=state.settings, should_abort=should_abort, log=log,
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
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        function = {}
    if function.get("name") == "flag_off_topic":
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

    if function.get("name") == PROPOSE_FIELDS_TOOL_NAME:
        # Shown to the user as a card with Apply / Dismiss (agent_panel.py).
        proposal, result = accept_field_change_call(function)
        if proposal is not None:
            state.pending_field_change = proposal
        state.transcript.append({"role": "tool", "content": json.dumps(result)})
        _toolless_followup()
        return

    spec, result = accept_finalize_call(function, state.baseline_config_id, baseline, state.settings, log)
    if spec is not None:
        state.spec = spec
        state.finalized = True
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
