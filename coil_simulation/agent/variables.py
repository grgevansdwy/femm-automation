"""Which CoilSet fields the LLM may propose changing, given a baseline
coilset and the constraints an interview gathered - replaces schema.py's
old hardcoded FREE_FIELDS constant. schema.py still owns the JSON-schema
*format* (the _num/_int/_thickness builders, coerce_delta); this file owns
*which fields are eligible* and why.
"""

import dataclasses

from agent.schema import _int, _num, _thickness, coerce_delta
from models.coilset import CoilSet

# The 24 pure winding-geometry fields today's hardcoded FREE_FIELDS always
# included, plus rc_ferrite_y_offset - see the asymmetry note below.
WINDING_FIELDS = [
    "loop_offset", "loop_r", "num_loop_turns", "num_parallel_loop_traces",
    "num_loop_layers", "loop_trace_spacing", "loop_turn_spacing",
    "loop_trace_width", "loop_trace_thickness",
    "tank_center_r", "num_tank_turns", "num_parallel_tank_traces",
    "num_tank_layers", "tank_trace_spacing", "tank_turn_spacing",
    "tank_trace_width", "tank_trace_thickness",
    "rc_radius", "num_rc_turns", "num_parallel_rc_traces",
    "num_rc_layers", "rc_trace_spacing", "rc_turn_spacing",
    "rc_trace_width", "rc_trace_thickness",
    "rc_ferrite_y_offset",  # unconditional read in engine.py - see FEATURE_FLAGS note below
]

# gate_field -> the fields it gates. Each group's fields (and the gate
# itself) are only offered as free when the baseline already has the flag
# on, or the user explicitly asks to enable it - never silently offered
# otherwise ("if the coilset doesn't have the variable, don't add it
# unless the user says so").
#
# rc_ferrite_y_offset is deliberately NOT listed under "rc_ferrite":
# engine.py's build_script() reads it unconditionally (outside the `if
# coilset.rc_ferrite_present:` guard) to position the RC-side stack origin,
# unlike tank_ferrite_y_offset, which only matters inside its own guard. It
# always has a real geometric effect regardless of the flag, so it lives in
# WINDING_FIELDS instead of here.
FEATURE_FLAGS = {
    "tank_ferrite": {
        "flag": "tank_ferrite_present",
        "fields": ["tank_ferrite_y_offset", "tank_ferrite_thickness", "tank_mu",
                   "tank_ferrite_outer_r", "tc_loss_angle"],
    },
    "rc_ferrite": {
        "flag": "rc_ferrite_present",
        "fields": ["rc_ferrite_thickness", "rc_mu", "rc_ferrite_outer_r", "rc_loss_angle"],
    },
    "tc_shield": {
        "flag": "tc_shield",
        "fields": ["tc_shield_y_distance", "tc_shield_radius", "tc_shield_thickness"],
    },
    "rc_shield": {
        "flag": "rc_shield",
        "fields": ["rc_shield_y_distance", "rc_shield_radius", "rc_shield_thickness"],
    },
    "backplane": {
        "flag": "backplane",
        "fields": ["backplane_y_distance"],
    },
    "rc_backplane": {
        "flag": "rc_backplane",
        "fields": ["rc_backplane_y_distance"],
    },
}
_FEATURE_FLAG_NAMES = {info["flag"] for info in FEATURE_FLAGS.values()}

# Ungated (always affect the solve) - excluded from the free set by
# deliberate convention, matching this project's original example objective
# of a fixed PCB thickness, not because they're structurally inactive.
CONVENTIONAL_OPT_IN_FIELDS = ["tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"]

_THICKNESS_FIELD_NAMES = {"loop_trace_thickness", "tank_trace_thickness", "rc_trace_thickness"}
_FIELD_TYPES = {f.name: f.type for f in dataclasses.fields(CoilSet)}
_FIELD_LABELS = {f.name: f.metadata.get("label", f.name) for f in dataclasses.fields(CoilSet)}


def _flag_schema(desc):
    return {"type": "integer", "enum": [0, 1], "description": desc}


def _schema_for(field_name: str) -> dict:
    """One field's JSON-schema entry, derived from CoilSet's own type
    annotation + label instead of hand-written per field."""
    if field_name in _FEATURE_FLAG_NAMES:
        return _flag_schema(f"{_FIELD_LABELS[field_name]}. 0 = off, 1 = on.")
    if field_name in _THICKNESS_FIELD_NAMES:
        return _thickness(f"{_FIELD_LABELS[field_name]}.")
    if _FIELD_TYPES[field_name] is int:
        return _int(f"{_FIELD_LABELS[field_name]}.", minimum=0)
    return _num(f"{_FIELD_LABELS[field_name]}.")


@dataclasses.dataclass
class VariableSpec:
    """What an interview gathered about which variables may move."""
    enabled_features: set[str] = dataclasses.field(default_factory=set)   # FEATURE_FLAGS keys explicitly turned on
    excluded_fields: set[str] = dataclasses.field(default_factory=set)    # fields the user said to keep fixed
    include_pcb_thickness: bool = False
    # field -> value the user said a variable must BE ("make rc trace
    # thickness 4oz") - applied to every candidate, so it's never searched
    # over and never offered as free. Comparisons stay against the
    # original, un-pinned baseline.
    fixed_values: dict = dataclasses.field(default_factory=dict)


def resolve_excluded(names) -> set[str]:
    """excluded_fields entries as real field names: a FEATURE_FLAGS key
    ("tank_ferrite") stands for its flag and all its fields. Raises
    ValueError on anything that's neither."""
    resolved = set()
    for name in names:
        if name in FEATURE_FLAGS:
            resolved |= {FEATURE_FLAGS[name]["flag"], *FEATURE_FLAGS[name]["fields"]}
        elif name in _FIELD_TYPES:
            resolved.add(name)
        else:
            raise ValueError(f"excluded_fields contains {name!r}, which is neither a CoilSet field nor a "
                             f"feature ({sorted(FEATURE_FLAGS)})")
    return resolved


def coerce_fixed_values(values: dict) -> dict:
    """Validates and type-coerces pinned values the same way a proposed
    delta is (int/float, thickness and 0/1-flag enums)."""
    unknown = [k for k in values if k not in _FIELD_TYPES or _FIELD_TYPES[k] not in (int, float)]
    if unknown:
        raise ValueError(f"fixed_values contains {unknown}, which aren't numeric CoilSet fields")
    return coerce_delta(values, {k: _schema_for(k) for k in values})


def is_feature_active(baseline: CoilSet, feature: str) -> bool:
    return bool(getattr(baseline, FEATURE_FLAGS[feature]["flag"]))


def free_fields_for(baseline: CoilSet, spec: VariableSpec | None = None) -> dict[str, dict]:
    """Which CoilSet fields the LLM may propose changing, given `baseline`
    and the constraints an interview gathered. Winding geometry is always
    free; a feature's fields (plus its own presence flag) are free only if
    the baseline already has that feature active, or `spec` explicitly
    turns it on. PCB thickness is free only if `spec` explicitly asks for
    it - excluded by convention, not gated by anything structural. Pinned
    fields (spec.fixed_values) are never free.
    """
    spec = spec or VariableSpec()
    names = list(WINDING_FIELDS)
    for feature, info in FEATURE_FLAGS.items():
        if is_feature_active(baseline, feature) or feature in spec.enabled_features:
            names.append(info["flag"])
            names.extend(info["fields"])
    if spec.include_pcb_thickness:
        names.extend(CONVENTIONAL_OPT_IN_FIELDS)
    names = [n for n in names if n not in spec.excluded_fields and n not in spec.fixed_values]

    return {name: _schema_for(name) for name in names}


def allowed_fields(baseline: CoilSet, spec: VariableSpec) -> set[str]:
    """Every field a candidate may legitimately differ from baseline in:
    the free ones the model proposes, plus the pinned ones the user set."""
    return set(free_fields_for(baseline, spec)) | set(spec.fixed_values)


def audit_changed_fields(baseline: CoilSet, candidate: CoilSet, allowed) -> dict:
    """Every field that differs between baseline and candidate, and which
    of those fall outside `allowed` (see allowed_fields()). coerce_delta()
    already rejects an out-of-scope key before the candidate is built, so
    unexpected_fields should always be empty - this makes that guarantee
    checked and visible instead of assumed, and gives the reports a diff
    over ALL fields (a diff filtered by the free set could never show an
    out-of-scope change)."""
    baseline_dict = dataclasses.asdict(baseline)
    candidate_dict = dataclasses.asdict(candidate)
    changed = {k: (v, candidate_dict[k]) for k, v in baseline_dict.items() if v != candidate_dict[k]}
    unexpected = {k: v for k, v in changed.items() if k not in allowed}
    return {"changed_fields": changed, "unexpected_fields": unexpected}
