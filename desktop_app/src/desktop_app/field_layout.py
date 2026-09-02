"""Section grouping for the CoilSet edit form.

Labels and field order come straight from CoilSet's own field metadata
(coil_simulation/models/coilset.py's `_label()`/`table_fields()`) - this
module only adds the presentation-level grouping into sections, since the
model doesn't (and shouldn't) know about UI layout.
"""

from dataclasses import fields as dataclass_fields

FIELD_GROUPS: dict[str, list[str]] = {
    "General": ["config_name"],
    "Loop": [
        "loop_offset",
        "loop_r",
        "num_loop_turns",
        "num_parallel_loop_traces",
        "num_loop_layers",
        "loop_trace_spacing",
        "loop_turn_spacing",
        "loop_trace_width",
        "loop_trace_thickness",
        "loop_PCB_thickness",
    ],
    "Tank": [
        "tank_center_r",
        "num_tank_turns",
        "num_parallel_tank_traces",
        "num_tank_layers",
        "tank_trace_spacing",
        "tank_turn_spacing",
        "tank_trace_width",
        "tank_trace_thickness",
        "tank_PCB_thickness",
    ],
    "RC": [
        "rc_radius",
        "num_rc_turns",
        "num_parallel_rc_traces",
        "num_rc_layers",
        "rc_trace_spacing",
        "rc_turn_spacing",
        "rc_trace_width",
        "rc_trace_thickness",
        "rc_PCB_thickness",
    ],
    "Ferrite": [
        "tank_ferrite_present",
        "tank_ferrite_y_offset",
        "tank_ferrite_thickness",
        "tank_mu",
        "tank_ferrite_outer_r",
        "rc_ferrite_present",
        "rc_ferrite_y_offset",
        "rc_ferrite_thickness",
        "rc_mu",
        "rc_ferrite_outer_r",
    ],
    "Shield & backplane": [
        "backplane",
        "backplane_y_distance",
        "rc_backplane",
        "rc_backplane_y_distance",
        "tc_shield",
        "tc_shield_y_distance",
        "tc_shield_radius",
        "tc_shield_thickness",
        "rc_shield",
        "rc_shield_y_distance",
        "rc_shield_radius",
        "rc_shield_thickness",
    ],
}

# "Presence" fields are declared `int` on CoilSet but are really booleans -
# render these as a checkbox instead of a numeric input.
PRESENCE_FIELDS = frozenset({
    "tank_ferrite_present",
    "rc_ferrite_present",
    "backplane",
    "rc_backplane",
    "tc_shield",
    "rc_shield",
})

# The 2 fields edited via a JSON textarea in the webapp; desktop_app renders
# them as plain numeric fields instead since there's no form-string round trip
# to justify a JSON escape hatch here. Unlike every other field, CoilSet
# doesn't give these a `_label()` (see models/coilset.py), so they need their
# own labels here.
ADVANCED_FIELDS = ("rc_loss_angle", "tc_loss_angle")
ADVANCED_FIELD_LABELS = {
    "rc_loss_angle": "RC Loss Angle",
    "tc_loss_angle": "TC Loss Angle",
}

_ALL_FIELDS = [name for names in FIELD_GROUPS.values() for name in names] + list(ADVANCED_FIELDS)


def field_types() -> dict[str, type]:
    """{field_name: python_type}, cross-checked against the real CoilSet so a
    renamed/added/removed field fails loudly instead of silently vanishing
    from the form."""
    from models.coilset import CoilSet

    types = {f.name: f.type for f in dataclass_fields(CoilSet)}
    declared = set(_ALL_FIELDS)
    actual = set(types)
    if declared != actual:
        raise RuntimeError(
            "field_layout is out of sync with CoilSet: "
            f"missing={sorted(actual - declared)} extra={sorted(declared - actual)}"
        )
    return types


def field_labels() -> dict[str, str]:
    """{field_name: display_label} for every field in FIELD_GROUPS/ADVANCED_FIELDS -
    CoilSet's own metadata for the grouped fields, plus ADVANCED_FIELD_LABELS
    for the 2 fields CoilSet doesn't label itself."""
    from models.coilset import CoilSet

    labels = {f.name: f.metadata["label"] for f in dataclass_fields(CoilSet) if "label" in f.metadata}
    labels.update(ADVANCED_FIELD_LABELS)
    return labels
