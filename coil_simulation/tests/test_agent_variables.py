# =============================================================================
# test_agent_variables.py
#
# Checks agent/variables.py's free_fields_for(): which CoilSet fields the
# LLM may propose changing, given a baseline and an interview's constraints.
# Needs no FEMM, no LLM:
#     python3 -m tests.test_agent_variables  (run from coil_simulation/)
#
# Builds its own baseline CoilSets via dataclasses.replace() off the
# 'base_coil' fixture (tests/conftest.py's autouse isolate_db seeds it) -
# same pattern as test_coilset_repository.py - rather than depending on
# Gen2_coil/Gen3 (real, but not present in the isolated test DB).
# =============================================================================
import dataclasses

from agent.variables import VariableSpec, WINDING_FIELDS, free_fields_for
from repositories.coilset_repository import get_coilset

_ALL_FEATURES_OFF = dict(
    tank_ferrite_present=0, rc_ferrite_present=0,
    backplane=0, rc_backplane=0, tc_shield=0, rc_shield=0,
)


def _baseline(**overrides):
    template = get_coilset('base_coil')
    return dataclasses.replace(template, **{**_ALL_FEATURES_OFF, **overrides})


def test_everything_off_yields_only_winding_fields():
    """Matches Gen2_coil's real profile: no ferrite, no shield, no
    backplane - none of those 22 fields should be offered, but
    rc_ferrite_y_offset (read unconditionally by engine.py) still is."""
    baseline = _baseline()
    fields = free_fields_for(baseline)
    assert set(fields) == set(WINDING_FIELDS)
    assert "rc_ferrite_y_offset" in fields
    assert "tank_ferrite_present" not in fields
    assert "tank_mu" not in fields
    assert "tc_shield_radius" not in fields
    assert "backplane_y_distance" not in fields
    print("  ok  an everything-off baseline offers only winding fields (+ rc_ferrite_y_offset)")


def test_active_feature_included_without_opt_in():
    """Matches Gen3/base_coil's real profile: ferrite + shield already
    present on the baseline - their fields should be free by default, no
    explicit enabled_features needed."""
    baseline = _baseline(tank_ferrite_present=1, rc_ferrite_present=1, tc_shield=1, rc_shield=1)
    fields = free_fields_for(baseline)
    for name in ["tank_ferrite_present", "tank_ferrite_y_offset", "tank_ferrite_thickness",
                 "tank_mu", "tank_ferrite_outer_r", "tc_loss_angle"]:
        assert name in fields, f"{name} should be free - tank_ferrite is active on the baseline"
    for name in ["rc_ferrite_present", "rc_ferrite_thickness", "rc_mu",
                 "rc_ferrite_outer_r", "rc_loss_angle"]:
        assert name in fields, f"{name} should be free - rc_ferrite is active on the baseline"
    for name in ["tc_shield", "tc_shield_y_distance", "tc_shield_radius", "tc_shield_thickness",
                 "rc_shield", "rc_shield_y_distance", "rc_shield_radius", "rc_shield_thickness"]:
        assert name in fields, f"{name} should be free - its shield is active on the baseline"
    # backplane/rc_backplane are still off on this baseline - still excluded
    assert "backplane_y_distance" not in fields
    assert "rc_backplane_y_distance" not in fields
    print("  ok  features already active on the baseline are offered with no explicit opt-in")


def test_explicit_opt_in_adds_exactly_that_feature():
    baseline = _baseline()  # everything off
    spec = VariableSpec(enabled_features={"tank_ferrite"})
    fields = free_fields_for(baseline, spec)

    expected_new = {"tank_ferrite_present", "tank_ferrite_y_offset", "tank_ferrite_thickness",
                     "tank_mu", "tank_ferrite_outer_r", "tc_loss_angle"}
    assert expected_new <= set(fields)
    assert set(fields) == set(WINDING_FIELDS) | expected_new
    print("  ok  explicitly enabling one feature adds exactly its fields, nothing else")


def test_pcb_thickness_excluded_by_default_included_on_request():
    baseline = _baseline()
    pcb_fields = {"tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"}

    default_fields = free_fields_for(baseline)
    assert not (pcb_fields & set(default_fields)), "PCB thickness should be fixed by default"

    opted_in = free_fields_for(baseline, VariableSpec(include_pcb_thickness=True))
    assert pcb_fields <= set(opted_in)
    print("  ok  PCB thickness is fixed unless explicitly requested free")


def test_excluded_fields_are_removed_even_if_otherwise_free():
    baseline = _baseline()
    spec = VariableSpec(excluded_fields={"loop_r", "num_tank_turns"})
    fields = free_fields_for(baseline, spec)
    assert "loop_r" not in fields
    assert "num_tank_turns" not in fields
    assert "rc_radius" in fields, "unrelated winding fields should be unaffected"
    print("  ok  explicitly excluded fields are removed from the free set")


def test_flag_field_schema_is_a_0_1_enum():
    baseline = _baseline()
    fields = free_fields_for(baseline, VariableSpec(enabled_features={"backplane"}))
    assert fields["backplane"]["enum"] == [0, 1]
    print("  ok  a feature's presence flag is offered as a 0/1 enum, not a free integer")


if __name__ == '__main__':
    print("checking agent/variables.py's free_fields_for()")
    test_everything_off_yields_only_winding_fields()
    test_active_feature_included_without_opt_in()
    test_explicit_opt_in_adds_exactly_that_feature()
    test_pcb_thickness_excluded_by_default_included_on_request()
    test_excluded_fields_are_removed_even_if_otherwise_free()
    test_flag_field_schema_is_a_0_1_enum()
    print("all agent/variables.py checks passed")
