# =============================================================================
# sim_constants_repository.py
#
# Reads the raw physics inputs for a run: frequency, target currents, coil
# resistances, matching-network values, the RG8x cable model, the PA's output
# impedance. Kept as plain scalars in JSON - the values sim_constants.py
# derives from them (omega, Vs, Z_pa as a complex number, ZL_eq, ...) can't
# be represented in JSON and are computed in Python instead.
# =============================================================================
import json
from pathlib import Path

SIM_CONSTANTS_PATH = Path(__file__).resolve().parents[1] / 'data' / 'sim_constants.json'


def load():
    return json.loads(SIM_CONSTANTS_PATH.read_text())
