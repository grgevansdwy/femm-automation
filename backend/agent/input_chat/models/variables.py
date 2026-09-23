"""Which CoilSet fields the LLM may propose changing, given a baseline
coilset and the constraints an interview gathered - replaces schema.py's
old hardcoded FREE_FIELDS constant. schema.py still owns the JSON-schema
*format* (the _num/_int/_thickness builders, coerce_delta); this file owns
*which fields are eligible* and why.

Two tiers:
- FREE_VARIABLES: base winding geometry - always free, no consent needed.
- CONSENT_GROUPS: everything else (ferrite, shields, backplanes, PCB
  thickness) - fixed unless the user EXPLICITLY asks for it in the
  interview (VariableSpec.enabled_groups). Whether the baseline already has
  a group active makes no difference: a design with ferrite fitted does
  NOT make ferrite free to change by itself - the search must not touch it
  unless told to. Each group is either a structural on/off toggle (a
  "flag" field the model may also flip, e.g. tank_ferrite_present) plus
  its own fields, or - PCB thickness, which has no toggle of its own,
  it's just always there - "flag": None.
"""
# In simple, what input variables is allowed to touch

import dataclasses

from agent.femm_agent.models.schema import _int, _num, _thickness, coerce_delta
from engine.models.coilset import CoilSet

# All input variables that are free to tune up
FREE_VARIABLES = [
    "loop_offset", "loop_r", "num_loop_turns", "num_parallel_loop_traces",
    "num_loop_layers", "loop_trace_spacing", "loop_turn_spacing",
    "loop_trace_width", "loop_trace_thickness",
    "tank_center_r", "num_tank_turns", "num_parallel_tank_traces",
    "num_tank_layers", "tank_trace_spacing", "tank_turn_spacing",
    "tank_trace_width", "tank_trace_thickness",
    "rc_radius", "num_rc_turns", "num_parallel_rc_traces",
    "num_rc_layers", "rc_trace_spacing", "rc_turn_spacing",
    "rc_trace_width", "rc_trace_thickness",
    "rc_ferrite_y_offset",  # unconditional read in engine/core.py - not actually ferrite-gated, see CONSENT_GROUPS
]

# Input variables that need user permission to edit
CONSENT_GROUPS = {
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
    "pcb_thickness": {
        "flag": None,  # no on/off toggle - the fields themselves always exist, just normally pinned
        "fields": ["tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"],
    },
}

# If variable getting consent, call this to get the fields
def _group_fields(group: str) -> list[str]:
    """A consent group's own field names, its toggle flag included if it has one."""
    info = CONSENT_GROUPS[group]
    return ([info["flag"]] if info["flag"] else []) + info["fields"]

# Set of every group's flag field name (skipping the flagless ones, e.g. pcb_thickness).
_CONSENT_GROUP_FLAG_NAMES = {info["flag"] for info in CONSENT_GROUPS.values() if info["flag"]}

# Thickness fields are not continuous - only 3 real copper weights exist.
_THICKNESS_FIELD_NAMES = {"loop_trace_thickness", "tank_trace_thickness", "rc_trace_thickness"}

# Dict of types and labels for each field in the coilset
_FIELD_TYPES = {f.name: f.type for f in dataclasses.fields(CoilSet)}
_FIELD_LABELS = {f.name: f.metadata.get("label", f.name) for f in dataclasses.fields(CoilSet)}

# Build binary schema 
def _flag_schema(desc):
    return {"type": "integer", "enum": [0, 1], "description": desc}

# Pulling the 
def _schema_for(field_name: str) -> dict:
    """One field's JSON-schema entry, derived from CoilSet's own type
    annotation + label instead of hand-written per field."""
    if field_name in _CONSENT_GROUP_FLAG_NAMES:
        return _flag_schema(f"{_FIELD_LABELS[field_name]}. 0 = off, 1 = on.")
    if field_name in _THICKNESS_FIELD_NAMES:
        return _thickness(f"{_FIELD_LABELS[field_name]}.")
    if _FIELD_TYPES[field_name] is int:
        return _int(f"{_FIELD_LABELS[field_name]}.", minimum=0)
    return _num(f"{_FIELD_LABELS[field_name]}.")


@dataclasses.dataclass
class VariableSpec:
    """What an interview gathered about which variables may move."""
    enabled_groups: set[str] = dataclasses.field(default_factory=set)  # CONSENT_GROUPS keys the user explicitly consented to
    excluded_fields: set[str] = dataclasses.field(default_factory=set)  # fields the user said to keep fixed
    # field -> value the user said a variable must BE ("make rc trace
    # thickness 4oz") - applied to every candidate, so it's never searched
    # over and never offered as free. Comparisons stay against the
    # original, un-pinned baseline.
    fixed_values: dict = dataclasses.field(default_factory=dict)

# expands a mix of raw field names and consent-group names into a flat set of actual field names to exclude, erroring on anything unrecognized.
def resolve_excluded(names) -> set[str]:
    """excluded_fields entries as real field names: a CONSENT_GROUPS key
    ("tank_ferrite") stands for its flag (if it has one) and all its
    fields. Raises ValueError on anything that's neither."""
    resolved = set()
    for name in names:
        if name in CONSENT_GROUPS:
            resolved |= set(_group_fields(name))
        elif name in _FIELD_TYPES:
            resolved.add(name)
        else:
            raise ValueError(f"excluded_fields contains {name!r}, which is neither a CoilSet field nor a "
                             f"consent group ({sorted(CONSENT_GROUPS)})")
    return resolved

# checks that pinned values are all numeric fields and converts them to the right type, the same way a proposed change would be.
def coerce_fixed_values(values: dict) -> dict:
    """Validates and type-coerces pinned values the same way a proposed
    delta is (int/float, thickness and 0/1-flag enums)."""
    unknown = [k for k in values if k not in _FIELD_TYPES or _FIELD_TYPES[k] not in (int, float)]
    if unknown:
        raise ValueError(f"fixed_values contains {unknown}, which aren't numeric CoilSet fields")
    return coerce_delta(values, {k: _schema_for(k) for k in values})

# reports whether a consent group is already physically active in the baseline design (for display only, doesn't unlock anything).
def is_group_present(baseline: CoilSet, group: str) -> bool:
    """Whether `group`'s structural flag is already on in `baseline` -
    informational only (interview_system_prompt() uses it to describe what
    the baseline physically has). Never grants freedom by itself - see
    free_fields_for(). True unconditionally for a flagless group
    (pcb_thickness): its fields always exist, there's no on/off state."""
    flag = CONSENT_GROUPS[group]["flag"]
    return bool(getattr(baseline, flag)) if flag else True

# computes the final list of fields the LLM is allowed to propose changing, given always-free fields plus any consent groups the user enabled, minus anything excluded or pinned.
def free_fields_for(baseline: CoilSet, spec: VariableSpec | None = None) -> dict[str, dict]:
    """Which CoilSet fields the LLM may propose changing: FREE_VARIABLES,
    always; a CONSENT_GROUPS group's fields only if the user explicitly put
    it in spec.enabled_groups - whether or not the baseline already has
    that group active makes no difference (see module docstring). Pinned
    fields (spec.fixed_values) are never free.
    """
    spec = spec or VariableSpec()
    names = list(FREE_VARIABLES)
    for group, info in CONSENT_GROUPS.items():
        if group in spec.enabled_groups:
            names.extend(_group_fields(group))
    names = [n for n in names if n not in spec.excluded_fields and n not in spec.fixed_values]

    return {name: _schema_for(name) for name in names}

# turns every field name a valid candidate could legitimately differ on, combining the free fields with the pinned ones.
def allowed_fields(baseline: CoilSet, spec: VariableSpec) -> set[str]:
    """Every field a candidate may legitimately differ from baseline in:
    the free ones the model proposes, plus the pinned ones the user set."""
    return set(free_fields_for(baseline, spec)) | set(spec.fixed_values)

# diffs a candidate against the baseline and flags any changed field that wasn't actually supposed to be allowed.
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
