# =============================================================================
# constant.py                                 extracted from compare_coilsets.py
#
# Everything fixed for a whole run - the test rig minus the coil. Per-coil
# geometry lives in CoilSet. Matplotlib-free so a GUI can read it without pyplot.
# =============================================================================
import numpy as np

# Operating point: 6.78 MHz ISM band. omega feeds every impedance below.
freq = 6.78e6
omega = 2*np.pi*freq

# FEMM group tags, so a coil and its ferrite/shield/backplane move as one unit.
loop_group = 1
tank_group = 2
rc_group = 3

# Probe currents for the FEMM extraction; the value is arbitrary and cancels,
# it only has to be non-zero to mark which coil is driven.
tank_coil_current_target = 10 #A
loop_current_target = 10 #A
rc_test_current_target = 12 #A

# Coil series resistance, measured on hardware rather than simulated.
R_tank = 2 #Ohms
R_loop = .2 #Ohms
R_rc = .5 #Ohms

# Receive-side open-circuit (OC) matching network: series L, shunt C.
L_match_OC = 185e-9 #H
C_match_OC = 1380e-12 #F

# RC feed cable as R + jwL of an RG8x run.
Z_cable_RC = .22 + (1j*omega*139e-9) #RG8x

# PA side: Z_pa is the output impedance the loop sees, Vs the square wave's
# fundamental (4/pi * Vpa).
# GOTCHA: Vpa is fixed here on the active "OLD WAY" path, which is why the VPA
# chart is flat - only the commented-out LTspice MPP block recomputes it.
Z_pa = 0.102210554024676 + 26.0367108593328j
Vpa = 200 #V
Vs = Vpa * (4/np.pi) #First Harmonic Approximation

# Rectifier + battery: RL is the DC load, RL_ac that load reflected through the
# full-bridge rectifier (8/pi^2), ZL_eq the single impedance the RC mesh sees.
RL = 3 #Ohms, 1000W @ 85V Vrect
RL_ac = RL * 8/(np.pi**2)
Z_ml = (1j*omega*L_match_OC)
Z_mc = 1/(1j*omega*C_match_OC)
ZL_eq = 1/((1/Z_mc)+(1/((2*Z_ml)+(RL_ac))))

# Metal detuning plane, a stand-in for a table top or device chassis.
backplane_length = 400 #mm
backplane_thickness = 5 #mm
