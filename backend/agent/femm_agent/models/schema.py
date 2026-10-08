"""The JSON-schema format for a free field (_num/_int/_thickness), and how
we validate what comes back (coerce_delta). Which fields are actually free
for a given baseline is agent/input_chat/models/variables.py's job (free_fields_for()) - this
file only knows how to describe/validate a field once told it's eligible.
"""

import math

_THICKNESS_ENUM = [0.035, 0.07, 0.14]  # 1oz / 2oz / 4oz, mm

def _num(desc, minimum=0.0, maximum=None):
    return _with_maximum({"type": "number", "description": desc, "minimum": minimum}, maximum)

def _int(desc, minimum=1, maximum=None):
    return _with_maximum({"type": "integer", "description": desc, "minimum": minimum}, maximum)

def _with_maximum(schema, maximum):
    if maximum is not None:
        schema["maximum"] = maximum
    return schema

def _thickness(desc, allowed=None):
    allowed = list(_THICKNESS_ENUM if allowed is None else allowed)
    listed = ", ".join(f"{t:g} ({round(t / 0.035)}oz)" for t in allowed)
    return {"type": "number", "enum": allowed, "description": f"{desc} Must be one of {listed}."}


def limit_text(schema: dict) -> str:
    """A field's allowed values in plain words - "1 to 8", "at least 0",
    "one of 0.035, 0.07, 0.14" - from its schema entry. The one wording used by
    a rejection, both prompts and the plan screen."""
    allowed = schema.get("enum")
    if allowed is not None:
        return "one of " + ", ".join(f"{v:g}" for v in allowed)
    low, high = schema.get("minimum"), schema.get("maximum")
    if low is not None and high is not None:
        return f"{low:g} to {high:g}"
    if high is not None:
        return f"at most {high:g}"
    if low is not None:
        return f"at least {low:g}"
    return "any value"


# Every way a proposal can be turned down, and the one fixed sentence the model
# is given for it. Nothing else explains a rejection to the model: no exception
# text, no file path (the full exception goes to the run log).
REJECTION_DETAIL = {
    "over_candidate_limit": "Not simulated: over the limit of candidates per round. Propose it again next round if it is still worth trying.",
    "unknown_tool": "The only tool is propose_coilset.",
    "bad_arguments": "The arguments must be a JSON object of field values.",
    "unknown_field": "This is not a field of the design.",
    "not_enabled": "This field is not open to change in this search.",
    "wrong_type": "The value has the wrong type for this field.",
    "out_of_range": "The value is outside the allowed range for this field.",
    "not_allowed_value": "The value is not one of the allowed values for this field.",
    "unknown_base": "No design with this label exists.",
    "no_change": "This changes nothing from the starting design. Change at least one field.",
    "invalid_geometry": "The geometry is not valid.",
    "simulation_limit": "Not simulated: the search has reached its simulation limit.",
    "time_limit": "Not simulated: the search has reached its time limit.",
    "search_stopped": "Not simulated: the search was stopped.",
    "solver_failed": "The solver could not solve this design.",
    "solver_timeout": "The solver did not finish this design within its time limit.",
}

# Rejections of a proposal that was never looked at - turned away by a limit, not
# by anything wrong with it. Not remembered as tried: it may be proposed again.
NEVER_EVALUATED = frozenset({"over_candidate_limit", "simulation_limit", "time_limit", "search_stopped"})


class Rejection(ValueError):
    """One proposal (or pinned value) turned down, in the fixed format the
    model is sent: a code from REJECTION_DETAIL, the field it is about, that
    field's allowed values, and one sentence. A ValueError, so code that
    already reports a bad value by catching ValueError keeps working."""

    def __init__(self, code: str, field: str | None = None, allowed: str | None = None,
                 detail: str | None = None) -> None:
        self.code, self.field, self.allowed = code, field, allowed
        self.detail = detail or REJECTION_DETAIL[code]
        super().__init__(self.text())

    def text(self) -> str:
        """The same rejection as one line for a person (GUI, History)."""
        where = f"{self.field}: " if self.field else ""
        allowed = f" Allowed: {self.allowed}." if self.allowed else ""
        return f"{where}{self.detail}{allowed}"

    def as_result(self) -> dict:
        return {"valid": False, "error": self.code, "field": self.field, "allowed": self.allowed,
                "detail": self.detail}


def coerce_delta(delta: dict, free_fields: dict, known_fields=()) -> dict:
    """Checks proposed values against this run's free fields and returns them
    as real ints/floats. Some models return numeric tool args as JSON strings
    (e.g. '5' instead of 5) - dataclasses.replace() would accept that silently,
    then a later arithmetic op fails deep inside simulate(). The schema's
    minimum/maximum/enum are only advisory to the model; this is the check
    that counts. Raises Rejection on the first bad value.

    `free_fields` is this run's computed set (agent/input_chat/models/variables.py's
    free_fields_for()), not a fixed whitelist. `known_fields` are the names
    that exist at all, so a real field that is closed in this run
    (not_enabled) is told apart from a made-up one (unknown_field)."""
    coerced = {}
    for key, value in delta.items():
        schema = free_fields.get(key)
        if schema is None:
            raise Rejection("not_enabled" if key in known_fields else "unknown_field", key)
        allowed_text = limit_text(schema)
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = math.nan
        whole = schema["type"] == "integer"
        if isinstance(value, bool) or not math.isfinite(number) or (whole and number != int(number)):
            raise Rejection("wrong_type", key, allowed_text,
                            f"The value must be a {'whole number' if whole else 'number'}.")
        coerced_value = int(number) if whole else number
        allowed = schema.get("enum")
        if allowed is not None and coerced_value not in allowed:
            raise Rejection("not_allowed_value", key, allowed_text)
        low, high = schema.get("minimum"), schema.get("maximum")
        if (low is not None and coerced_value < low) or (high is not None and coerced_value > high):
            raise Rejection("out_of_range", key, allowed_text)
        coerced[key] = coerced_value
    return coerced


# Not a CoilSet field - a short human-readable "why" riding along with each
# proposal. optimize.py pops it off the arguments before coerce_delta(),
# which would otherwise reject it as an unknown field.
RATIONALE_KEY = "rationale"

# Not a CoilSet field either - the label of an earlier simulated design ("C7")
# this proposal starts from, instead of the baseline. optimize.py pops it off
# and resolves it; an unknown label is rejected there, not by the schema (the
# schema is built once per run, and labels are added every round).
BASE_KEY = "base"

# The name of the one tool the search model is offered (build_tools()) - optimize.py
# rejects a call to anything else.
PROPOSE_TOOL_NAME = "propose_coilset"


def build_tools(free_fields: dict, baseline_config_id: str) -> list[dict]:
    """The only action the model can take: propose a delta shaped like
    free_fields (plus a one-line rationale and an optional starting design,
    `base`). additionalProperties:
    False rejects any field not in that set. Built per-run, not once at
    import time, since free_fields is computed dynamically per baseline
    (agent/input_chat/models/variables.py)."""
    properties = dict(free_fields)
    properties[RATIONALE_KEY] = {
        "type": "string",
        "description": (
            "Your reasoning, summarized in 1-2 sentences max: what the previous result told you "
            "(if there was one) and why this change should move the objective. Plain words, no lists."
        ),
    }
    properties[BASE_KEY] = {
        "type": "string",
        "description": (
            "Optional. The label of an earlier simulated design to start from, e.g. \"C7\". The fields "
            "you give are then the only changes from that design. Omit it to start from the baseline."
        ),
    }
    return [{
        "type": "function",
        "function": {
            "name": PROPOSE_TOOL_NAME,
            "description": (
                f"Propose one candidate coilset as a delta from a starting design: the {baseline_config_id} "
                "baseline, or the earlier design named in `base`. "
                "Call it several times in one reply to try several candidates at once - they're "
                "simulated together, and each call gets its own result. "
                "Only include fields you want to change from the starting design - anything omitted "
                "keeps the starting design's value. You'll get back the design's label, each objective's "
                "value and % change vs. the ORIGINAL baseline (at every distance, for simulated "
                "quantities) with pass/fail and margin, and whether it meets the goal."
            ),
            # rationale is required, not optional: optional, the model sometimes
            # skipped it (seen live in a round-4 card), leaving that round
            # with no one-line explanation at all.
            "parameters": {
                "type": "object", "properties": properties,
                "required": [RATIONALE_KEY], "additionalProperties": False,
            },
        },
    }]
