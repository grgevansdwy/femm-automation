"""The JSON-schema format for a free field (_num/_int/_thickness), and how
we validate what comes back (coerce_delta). Which fields are actually free
for a given baseline is agent/input_chat/models/variables.py's job (free_fields_for()) - this
file only knows how to describe/validate a field once told it's eligible.
"""

_THICKNESS_ENUM = [0.035, 0.07, 0.14]  # 1oz / 2oz / 4oz, mm

def _num(desc, minimum=0.01):
    return {"type": "number", "description": desc, "minimum": minimum}

def _int(desc, minimum=1):
    return {"type": "integer", "description": desc, "minimum": minimum}

def _thickness(desc):
    return {"type": "number", "enum": _THICKNESS_ENUM, "description": desc + " Must be one of 0.035 (1oz), 0.07 (2oz), 0.14 (4oz)."}


def coerce_delta(delta: dict, free_fields: dict) -> dict:
    """Some models return numeric tool args as JSON strings (e.g. '5' instead
    of 5) - dataclasses.replace() would accept that silently, then a later
    arithmetic op (e.g. num_turns - 1) fails with an opaque TypeError deep
    inside simulate(). Coerce here instead, so a bad value fails fast with a
    clear message the model can actually act on. `free_fields` is this run's
    computed set (agent/input_chat/models/variables.py's free_fields_for()), not a fixed
    whitelist - a field eligible in one run may not be in another."""
    coerced = {}
    for key, value in delta.items():
        schema = free_fields.get(key)
        if schema is None:
            raise ValueError(f"'{key}' is not a valid field")
        target_type = int if schema["type"] == "integer" else float
        try:
            coerced_value = target_type(value)
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a {schema['type']}, got {value!r}") from None
        # The JSON schema's "enum" (trace thickness's 3 standard copper
        # weights, or a feature flag's 0/1) is only advisory to the model -
        # nothing enforces it server-side without this check.
        allowed = schema.get("enum")
        if allowed is not None and coerced_value not in allowed:
            raise ValueError(f"'{key}' must be one of {allowed}, got {coerced_value!r}")
        coerced[key] = coerced_value
    return coerced


# Not a CoilSet field - a short human-readable "why" riding along with each
# proposal. optimize.py pops it off the arguments before coerce_delta(),
# which would otherwise reject it as an unknown field.
RATIONALE_KEY = "rationale"


def build_tools(free_fields: dict, baseline_config_id: str) -> list[dict]:
    """The only action the model can take: propose a delta shaped like
    free_fields (plus an optional one-line rationale). additionalProperties:
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
    return [{
        "type": "function",
        "function": {
            "name": "propose_coilset",
            "description": (
                f"Propose one candidate coilset as a delta from the {baseline_config_id} baseline. "
                "Call it several times in one reply to try several candidates at once - they're "
                "simulated together, and each call gets its own result. "
                "Only include fields you want to change from baseline - anything omitted keeps its "
                "baseline value. You'll get back each objective's value and % change vs. baseline "
                "(at every distance, for simulated quantities) with pass/fail and margin, and "
                "whether it meets the goal."
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
