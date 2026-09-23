# =============================================================================
# test_agent_variables.py
#
# Checks agent/input_chat/models/variables.py's free_fields_for(): which CoilSet fields the
# LLM may propose changing, given a baseline and an interview's constraints.
# Needs no FEMM, no LLM:
#     python3 -m agent.input_chat.tests.test_agent_variables  (run from backend/)
#
# Builds its own baseline CoilSets via dataclasses.replace() off the
# 'base_coil' fixture (backend/conftest.py's autouse isolate_db seeds it) -
# same pattern as test_coilset_repository.py - rather than depending on
# Gen2_coil/Gen3 (real, but not present in the isolated test DB).
# =============================================================================
import dataclasses

from agent.input_chat.models.variables import CONSENT_GROUPS, FREE_VARIABLES, VariableSpec, free_fields_for, is_group_present
from engine.repositories.coilset_repository import get_coilset

_ALL_FEATURES_OFF = dict(
    tank_ferrite_present=0, rc_ferrite_present=0,
    backplane=0, rc_backplane=0, tc_shield=0, rc_shield=0,
)


def _baseline(**overrides):
    template = get_coilset('base_coil')
    return dataclasses.replace(template, **{**_ALL_FEATURES_OFF, **overrides})


def test_everything_off_yields_only_free_variables():
    """Matches Gen2_coil's real profile: no ferrite, no shield, no
    backplane - none of those 22 fields should be offered, but
    rc_ferrite_y_offset (read unconditionally by engine/core.py) still is."""
    baseline = _baseline()
    fields = free_fields_for(baseline)
    assert set(fields) == set(FREE_VARIABLES)
    assert "rc_ferrite_y_offset" in fields
    assert "tank_ferrite_present" not in fields
    assert "tank_mu" not in fields
    assert "tc_shield_radius" not in fields
    assert "backplane_y_distance" not in fields
    print("  ok  an everything-off baseline offers only FREE_VARIABLES (+ rc_ferrite_y_offset)")


def test_a_group_already_present_on_the_baseline_still_needs_explicit_consent():
    """The rule is strict and uniform: presence on the baseline never grants
    freedom by itself - only spec.enabled_groups does. A baseline with
    ferrite/shield already fitted (Gen3/base_coil's real profile) still
    offers NONE of those fields until the user explicitly consents."""
    baseline = _baseline(tank_ferrite_present=1, rc_ferrite_present=1, tc_shield=1, rc_shield=1)
    fields = free_fields_for(baseline)  # no VariableSpec - nothing consented
    assert set(fields) == set(FREE_VARIABLES), "presence alone must not free anything"
    for name in ["tank_ferrite_present", "tank_ferrite_y_offset", "rc_ferrite_present",
                 "tc_shield", "tc_shield_radius", "rc_shield", "rc_shield_radius"]:
        assert name not in fields, f"{name} must stay fixed - active on the baseline is not consent"

    # Consenting to a group frees it, whether or not the baseline already has it.
    fields = free_fields_for(baseline, VariableSpec(enabled_groups={"tank_ferrite", "rc_shield"}))
    for name in ["tank_ferrite_present", "tank_ferrite_y_offset", "tank_ferrite_thickness",
                 "tank_mu", "tank_ferrite_outer_r", "tc_loss_angle"]:
        assert name in fields, f"{name} should be free - the user consented to tank_ferrite"
    for name in ["rc_shield", "rc_shield_y_distance", "rc_shield_radius", "rc_shield_thickness"]:
        assert name in fields, f"{name} should be free - the user consented to rc_shield"
    # rc_ferrite and tc_shield are still present but NOT consented to - still fixed.
    assert "rc_ferrite_present" not in fields
    assert "tc_shield" not in fields
    print("  ok  a group being present on the baseline never substitutes for explicit consent")


def test_is_group_present_is_purely_informational():
    """is_group_present() reports physical presence only - it plays no part
    in free_fields_for()'s gating (checked above); it's read by
    interview_system_prompt() to describe the baseline."""
    baseline = _baseline(tank_ferrite_present=1)
    assert is_group_present(baseline, "tank_ferrite") is True
    assert is_group_present(baseline, "rc_ferrite") is False
    # A flagless group (pcb_thickness) always exists - no absent state.
    assert is_group_present(baseline, "pcb_thickness") is True


def test_explicit_consent_adds_exactly_that_group():
    baseline = _baseline()  # everything off
    spec = VariableSpec(enabled_groups={"tank_ferrite"})
    fields = free_fields_for(baseline, spec)

    expected_new = {"tank_ferrite_present", "tank_ferrite_y_offset", "tank_ferrite_thickness",
                     "tank_mu", "tank_ferrite_outer_r", "tc_loss_angle"}
    assert expected_new <= set(fields)
    assert set(fields) == set(FREE_VARIABLES) | expected_new
    print("  ok  explicitly consenting to one group adds exactly its fields, nothing else")


def test_pcb_thickness_is_a_consent_group_like_any_other():
    """PCB thickness used to be a separate include_pcb_thickness bool
    (CONVENTIONAL_OPT_IN_FIELDS) - now it's CONSENT_GROUPS["pcb_thickness"],
    gated the exact same way as ferrite/shield/backplane."""
    baseline = _baseline()
    pcb_fields = {"tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"}
    assert set(CONSENT_GROUPS["pcb_thickness"]["fields"]) == pcb_fields
    assert CONSENT_GROUPS["pcb_thickness"]["flag"] is None, "no on/off toggle - the fields always exist"

    default_fields = free_fields_for(baseline)
    assert not (pcb_fields & set(default_fields)), "PCB thickness should be fixed by default"

    opted_in = free_fields_for(baseline, VariableSpec(enabled_groups={"pcb_thickness"}))
    assert pcb_fields <= set(opted_in)
    print("  ok  PCB thickness is fixed unless explicitly consented to, same as any other group")


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
    fields = free_fields_for(baseline, VariableSpec(enabled_groups={"backplane"}))
    assert fields["backplane"]["enum"] == [0, 1]
    print("  ok  a group's presence flag is offered as a 0/1 enum, not a free integer")


def test_unknown_group_in_enabled_groups_is_silently_ignored():
    """free_fields_for() only trusts real CONSENT_GROUPS keys - matches the
    old FEATURE_FLAGS behavior (validation of the raw name, e.g. a typo'd
    finalize_spec arg, is interview._build_spec()'s job, not this one's)."""
    baseline = _baseline()
    fields = free_fields_for(baseline, VariableSpec(enabled_groups={"not_a_real_group"}))
    assert set(fields) == set(FREE_VARIABLES)


if __name__ == '__main__':
    print("checking agent/input_chat/models/variables.py's free_fields_for()")
    test_everything_off_yields_only_free_variables()
    test_a_group_already_present_on_the_baseline_still_needs_explicit_consent()
    test_is_group_present_is_purely_informational()
    test_explicit_consent_adds_exactly_that_group()
    test_pcb_thickness_is_a_consent_group_like_any_other()
    test_excluded_fields_are_removed_even_if_otherwise_free()
    test_flag_field_schema_is_a_0_1_enum()
    test_unknown_group_in_enabled_groups_is_silently_ignored()
    print("all agent/input_chat/models/variables.py checks passed")
