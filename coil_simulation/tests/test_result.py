# =============================================================================
# test_result.py
#
# Checks the SimResult output model: one CoilSet's L/M/ESR at one distance.
# Needs no FEMM:
#     python3 -m tests.test_result          (run from coil_simulation/)
# =============================================================================
import dataclasses
import json

from models.result import SimResult


def make_result(config_name='Gen3', distance=5.0):
    return SimResult(
        config_name=config_name, distance=distance,
        L_loop=219.54e-9, L_tank=5.5219e-6, L_rc=1.8179e-6,
        M12=6.8675e-07, M13=2.9942e-07, M23=1.9372e-06,
        loop_esr=0.19, tank_esr=1.836, rc_esr=0.512,
        k12=0.6237, k13=0.4739, k23=0.6114,
    )


def test_all_fields_present():
    r = make_result()
    fields = {f.name for f in dataclasses.fields(r)}
    assert fields == {
        'config_name', 'distance',
        'L_loop', 'L_tank', 'L_rc',
        'M12', 'M13', 'M23',
        'loop_esr', 'tank_esr', 'rc_esr',
        'k12', 'k13', 'k23',
    }, fields
    print("  ok  SimResult holds exactly the L/M/ESR/k fields")


def test_frozen():
    r = make_result()
    try:
        r.L_loop = 0
    except Exception:
        print("  ok  SimResult is immutable")
        return
    raise AssertionError("SimResult should be frozen")


def test_json_round_trip():
    r = make_result()
    payload = json.loads(json.dumps(dataclasses.asdict(r)))
    r2 = SimResult(**payload)
    assert r == r2, (r, r2)
    print("  ok  SimResult round-trips through dataclasses.asdict -> JSON -> back")


if __name__ == '__main__':
    print("checking the results output model")
    test_all_fields_present()
    test_frozen()
    test_json_round_trip()
    print("all results checks passed")
