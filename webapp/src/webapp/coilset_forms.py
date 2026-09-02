"""Field metadata and form<->CoilSet marshalling shared by the config routes.

Field names/groups below are enumerated explicitly (not introspected from
CoilSet) so the templates have stable groups and labels to render. They are
grounded in coil_simulation/models/coilset.py's CoilSet dataclass (51
required fields + 2 defaulted fields: rc_loss_angle, tc_loss_angle - 53
total). `_field_types()` cross-checks this list against the real CoilSet the
first time it's needed, so a mismatch (e.g. a field renamed) fails loudly
instead of silently producing a broken form.
"""

from dataclasses import fields as dataclass_fields
from functools import lru_cache
from typing import Any

# The 2 fields edited via the advanced/JSON section instead of a labeled input.
ADVANCED_FIELDS = ("rc_loss_angle", "tc_loss_angle")

DEFAULT_ADVANCED_VALUES = {"rc_loss_angle": 0.0125, "tc_loss_angle": 0.0125}

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

_ALL_GROUPED_FIELDS = [name for names in FIELD_GROUPS.values() for name in names]


@lru_cache(maxsize=1)
def _field_types() -> dict[str, type]:
    from models.coilset import CoilSet

    types = {f.name: f.type for f in dataclass_fields(CoilSet)}
    declared = set(_ALL_GROUPED_FIELDS) | set(ADVANCED_FIELDS)
    actual = set(types)
    if declared != actual:
        raise RuntimeError(
            "coilset_forms field groups are out of sync with CoilSet: "
            f"missing={sorted(actual - declared)} extra={sorted(declared - actual)}"
        )
    return types


def coilset_to_form_values(coilset: Any) -> dict[str, Any]:
    return {f.name: getattr(coilset, f.name) for f in dataclass_fields(coilset)}


def form_to_coilset_kwargs(form_values: dict[str, Any], advanced_json: dict[str, Any]) -> dict[str, Any]:
    """Casts raw (string) grouped-field values plus the parsed advanced-JSON
    dict to CoilSet's declared field types.

    Raises ValueError on a missing/bad field; callers should catch and
    surface str(exc) to the user rather than a raw 500.
    """
    field_types = _field_types()
    kwargs: dict[str, Any] = {}
    for name in _ALL_GROUPED_FIELDS:
        raw = form_values.get(name)
        if raw is None or raw == "":
            raise ValueError(f"missing required field: {name}")
        field_type = field_types[name]
        kwargs[name] = raw if field_type is str else field_type(raw)
    for name in ADVANCED_FIELDS:
        if name in advanced_json:
            kwargs[name] = float(advanced_json[name])
    return kwargs


def build_coilset(form_values: dict[str, Any], advanced_json: dict[str, Any]) -> Any:
    from models.coilset import CoilSet

    return CoilSet(**form_to_coilset_kwargs(form_values, advanced_json))
