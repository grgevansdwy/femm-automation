# =============================================================================
# sim_constants.py                                       replaces config.py
#
# Everything fixed for a whole run - the test rig minus the coil. Per-coil
# geometry lives in CoilSet. Matplotlib-free so a GUI can read it without
# pyplot.
#
# Raw scalar inputs live in data/sim_constants.json (read via
# repositories/sim_constants_repository.py); this module computes the values
# that JSON can't hold directly - Z_pa as a complex number, and the
# omega-dependent/formula-derived values below.
# =============================================================================
import numpy as np

from repositories import sim_constants_repository

_raw = sim_constants_repository.load()

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

# Coil series resistance, measured on hardware rather than simulated.
R_loop = _raw['R_loop']
R_tank = _raw['R_tank']
R_rc = _raw['R_rc']

# Receive-side open-circuit (OC) matching network: series L, shunt C.
L_match_OC = _raw['L_match_OC']
C_match_OC = _raw['C_match_OC']

# RC feed cable as R + jwL of an RG8x run.
Z_cable_RC = _raw['R_cable_RC'] + (1j*omega*_raw['L_cable_RC'])

# PA side: Z_pa is the output impedance the loop sees, Vs the square wave's
# fundamental (4/pi * Vpa).
# GOTCHA: Vpa is fixed here on the active "OLD WAY" path, which is why the VPA
# chart is flat - only the commented-out LTspice MPP block recomputes it.
Z_pa = _raw['Z_pa']['real'] + _raw['Z_pa']['imag']*1j
Vpa = _raw['Vpa']
Vs = Vpa * (4/np.pi)  # First Harmonic Approximation

# Rectifier + battery: RL is the DC load, RL_ac that load reflected through the
# full-bridge rectifier (8/pi^2), ZL_eq the single impedance the RC mesh sees.
RL = _raw['RL']
RL_ac = RL * 8/(np.pi**2)
Z_ml = (1j*omega*L_match_OC)
Z_mc = 1/(1j*omega*C_match_OC)
ZL_eq = 1/((1/Z_mc)+(1/((2*Z_ml)+(RL_ac))))

# Metal detuning plane, a stand-in for a table top or device chassis.
backplane_length = _raw['backplane_length']
backplane_thickness = _raw['backplane_thickness']
