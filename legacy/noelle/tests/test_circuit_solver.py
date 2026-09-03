# =============================================================================
# test_circuit_solver.py
#
# Pins circuit_solver against results/Gen2_baseline/gen2_baseline_log.txt.txt.
# Needs no FEMM and no ltspiceautomator. Run from noelle/:
#     python3 -m tests.test_circuit_solver
#
# Z_load is hardcoded, not imported: this baseline predates the current
# matching-network constants. Back-solving its logged Z3 gives
# 43.027-8.728j, while today's sim_constants give 94.174+31.333j - asserting
# against today's ZL_eq fails (mag_normal 3.1169 vs a logged 2.9358).
#
# The logs record the full CoilSet but not one global constant, so each archived
# run's operating point is only recoverable by back-solving. That is the
# "results are write-only" problem in docs/REFACTOR_NOTES.md.
#
# NOT PINNED: solve_mesh_currents and power_accounting - iloop/itank/irc, Vrect
# and PBatt are never logged, only drawn into the SVG. Same root cause.
# =============================================================================
import math

from services import circuit_solver

omega = 2 * math.pi * 6.78e6

# --- constants the archived run shares with sim_constants today -------------
Z_pa = 0.102210554024676 + 26.0367108593328j
R_loop = .2
R_tank = 2
R_rc = .5
Z_cable_TC = .12 + (1j * omega * 152e-9)   # RG213; the Gen2 CoilSet has coax_cable=213
Z_cable_RC = .22 + (1j * omega * 139e-9)   # RG8x

# --- the archived run's load impedance, back-solved from its logged Z3 ------
Z_LOAD_ARCHIVED = 43.02691026713202 - 8.727595384035817j

# --- values read straight out of the log ------------------------------------
C1 = 2.445839548621124e-09
C2 = 9.949611099287972e-11
C3 = 2.944991457932163e-10
NUM_TANK_TURNS = 5

# index 0 of the sweep = 5 mm
L_LOOP_5MM = 219.5419347428e-9
L_TANK_5MM = 5.521978239796e-6
L_RC_5MM = 1.817903894108e-6
M12_5MM = 6.867555449963978e-07
M13_5MM = 2.9942263569382067e-07
M23_5MM = 1.9372036508597484e-06

# last index of the sweep = 100 mm
L_LOOP_100MM = 220.6660955101e-9
L_TANK_100MM = 5.59259249102e-6
L_RC_100MM = 1.835075919139e-6

# the log prints Z1/Z2/Z3 after the sweep, so they are the LAST distance's
LOGGED_Z1 = 0.422210554024676 + 32.314688963909354j
LOGGED_Z2 = 2 + 2.3137873953334065j
LOGGED_MAG_NORMAL = 2.9357818822806854


def _impedances(L_loop, L_tank, L_rc):
    return circuit_solver.mesh_impedances(
        omega, L_loop, L_tank, L_rc, C1, C2, C3,
        Z_pa, R_loop, R_tank, R_rc,
        Z_cable_TC, Z_cable_RC, Z_LOAD_ARCHIVED, NUM_TANK_TURNS)


def assert_close(name, got, want, rel_tol=1e-9):
    ok = math.isclose(complex(got).real, complex(want).real, rel_tol=rel_tol, abs_tol=1e-12) \
        and math.isclose(complex(got).imag, complex(want).imag, rel_tol=rel_tol, abs_tol=1e-12)
    if not ok:
        raise AssertionError(f"{name}\n  got  {got}\n  want {want}")
    print(f"  ok  {name} = {got}")


def test_mesh_impedances_at_100mm():
    """Z1 and Z2 must reproduce the logged values exactly.

    Validates the TC-side constants moved into sim_constants.py - Z_pa, R_loop,
    Z_cable_TC and R_tank all feed these. Z3 is not asserted: it depends on the
    load impedance, the one thing this baseline disagrees with sim_constants on.
    """
    Z1, Z2, _Z3 = _impedances(L_LOOP_100MM, L_TANK_100MM, L_RC_100MM)
    assert_close("Z1 @ 100mm", Z1, LOGGED_Z1)
    assert_close("Z2 @ 100mm", Z2, LOGGED_Z2)


def test_normalized_currents_at_5mm():
    """mag_normal at the closest distance must reproduce the logged value.

    Exercises mesh_impedances and normalized_currents together, Z3 path included,
    since Z_LOAD_ARCHIVED restores this run's operating point.
    """
    _Z1, Z2, Z3 = _impedances(L_LOOP_5MM, L_TANK_5MM, L_RC_5MM)
    abs_Iloop, abs_Itank = circuit_solver.normalized_currents(
        omega, M12_5MM, M13_5MM, M23_5MM, Z2, Z3)
    mag_normal = math.sqrt(abs_Itank ** 2 + abs_Iloop ** 2)
    assert_close("mag_normal @ 5mm", mag_normal, LOGGED_MAG_NORMAL)


def test_tankless_config_gets_dummy_z2():
    """num_tank_turns == 0 must collapse Z2 to the dummy 1, not divide by C2."""
    _Z1, Z2, _Z3 = circuit_solver.mesh_impedances(
        omega, L_LOOP_5MM, 0, L_RC_5MM, C1, C2, C3,
        Z_pa, R_loop, R_tank, R_rc,
        Z_cable_TC, Z_cable_RC, Z_LOAD_ARCHIVED, 0)
    assert_close("Z2 (tank-less)", Z2, 1)


if __name__ == '__main__':
    print("pinning circuit_solver against results/Gen2_baseline")
    test_mesh_impedances_at_100mm()
    test_normalized_currents_at_5mm()
    test_tankless_config_gets_dummy_z2()
    print("all pins passed")
