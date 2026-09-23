# =============================================================================
# test_result.py
#
# Checks the SimResult output model: one CoilSet's L/M/ESR at one distance.
# Needs no FEMM:
#     python3 -m engine.tests.test_result          (run from backend/)
# =============================================================================
import dataclasses
import json

from engine.models.result import SimResult


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


def test_extract_result_returns_plain_python_floats():
    """A numpy.float64 k (from np.sqrt) turned objective checks into
    numpy.bool_, and the AI search crashed serializing its tool result."""
    from engine.core import extract_result
    from engine.repositories.coilset_repository import get_coilset

    raw = {'L_loop': complex(1.9e-7, 0.1), 'M_tank_loop': complex(6e-7), 'M_rc_loop': complex(1.3e-7),
           'R_loop': complex(0.0168), 'L_tank': complex(4.6e-6), 'M_rc_tank': complex(6.9e-7),
           'R_tank': complex(0.427), 'L_rc': complex(1.6e-6), 'R_rc': complex(0.163)}
    result = extract_result(raw, get_coilset('base_coil'), distance=5.0)
    for field in dataclasses.fields(result):
        if field.name != 'config_name':
            value = getattr(result, field.name)
            assert type(value) is float, f"{field.name} is {type(value).__module__}.{type(value).__name__}"
    json.dumps(dataclasses.asdict(result))
    print("  ok  extract_result() returns plain floats")
