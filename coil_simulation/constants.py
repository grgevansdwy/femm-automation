# =============================================================================
# constants.py
#
# Everything fixed for a whole run - the test rig minus the coil. Per-coil
# geometry lives in CoilSet. Matplotlib-free so a GUI can read it without
# pyplot.
#
# Raw scalar inputs live in data/constants.json (read via
# repositories/constants_repository.py); this module computes omega from
# freq, the one derived value still needed.
# =============================================================================
import numpy as np

from repositories import constants_repository

_raw = constants_repository.load()

# Operating point: 6.78 MHz ISM band. omega feeds every impedance below.
freq = _raw['freq']
omega = 2*np.pi*freq

# FEMM group tags, so a coil and its ferrite/shield/backplane move as one unit.
loop_group = _raw['loop_group']
tank_group = _raw['tank_group']
rc_group = _raw['rc_group']

# Probe currents for the FEMM extraction; the value is arbitrary and cancels,
# it only has to be non-zero to mark which coil is driven.
loop_current_target = _raw['loop_current_target']
tank_coil_current_target = _raw['tank_coil_current_target']
rc_test_current_target = _raw['rc_test_current_target']

# Metal detuning plane, a stand-in for a table top or device chassis.
backplane_length = _raw['backplane_length']
backplane_thickness = _raw['backplane_thickness']
