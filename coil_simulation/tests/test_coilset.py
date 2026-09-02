# =============================================================================
# test_coilset.py
#
# Guards the removal of the 4 circuit-solve/tuning-only fields (coax_cable,
# loop_tune_f, tank_tune_f, rc_tune_f) from CoilSet. Needs no FEMM:
#     python3 -m tests.test_coilset  (run from coil_simulation/)
# =============================================================================
import dataclasses

from models.coilset import CoilSet

REMOVED_FIELDS = {'coax_cable', 'loop_tune_f', 'tank_tune_f', 'rc_tune_f'}


def test_removed_fields_are_gone():
    field_names = {f.name for f in dataclasses.fields(CoilSet)}
    present = field_names & REMOVED_FIELDS
    assert not present, f"circuit-solve-only fields still on CoilSet: {present}"
    print("  ok  coax_cable/loop_tune_f/tank_tune_f/rc_tune_f are gone from CoilSet")


def test_field_count():
    field_names = {f.name for f in dataclasses.fields(CoilSet)}
    assert len(field_names) == 53, len(field_names)
    print(f"  ok  CoilSet has {len(field_names)} fields (57 - 4 removed)")


if __name__ == '__main__':
    print("checking CoilSet")
    test_removed_fields_are_gone()
    test_field_count()
    print("all coilset checks passed")
