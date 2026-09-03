# =============================================================================
# circuit_model.py                            extracted from compare_coilsets.py
#
# The 3-mesh circuit solve on top of each FEMM inductance extraction:
# Loop (L1, PA-driven) -> Tank (L2, free resonator) -> RC (L3, receiver).
#
# Pure arithmetic - no FEMM, no matplotlib, no module state. The only part of the
# simulation runnable without FEMM, which test_circuit_model.py exercises.
#
# Impedances arrive as arguments rather than being read from sim_constants: the
# archived runs in results/ used DIFFERENT load constants than sim_constants holds
# today, so a function reaching for the current ZL_eq could not reproduce them.
#
# Currents returned are PEAK amps unless the name ends in _RMS.
# =============================================================================
import numpy as np


def mesh_impedances(omega, L_loop, L_tank, L_rc, C1, C2, C3,
                    Z_pa, R_loop, R_tank, R_rc,
                    Z_cable_TC, Z_cable_RC, Z_load, num_tank_turns):
    """Series impedance of each of the three meshes at the operating frequency.

    Z1: PA output + tuning cap + loop L + loop ESR + transmit coax.
    Z2: free-floating series LC; a dummy 1 for tank-less configs so the tank
        terms in the current equations collapse instead of blowing up.
    Z3: RC cap + RC L + RC ESR + receive coax + Z_load (network + rectified load).
    """
    Z1 = Z_pa + 1/(1j*omega*C1) + 1j*omega*L_loop + R_loop + Z_cable_TC

    if (num_tank_turns > 0):
        Z2 = 1 / (1j * omega * C2) + 1j * omega * L_tank + R_tank
    else:
        Z2 = 1

    Z3 = 1/(1j*omega*C3) + 1j*omega*L_rc + R_rc + Z_cable_RC + Z_load #Assuming R_load is actually the abs. of Z_eq of load and matching network -- no rectifier

    return Z1, Z2, Z3


def solve_mesh_currents(Vs, omega, M12, M13, M23, Z1, Z2, Z3):
    """Cramer's rule on the impedance matrix: current_denominator is the
    determinant, each current its cofactor times Vs. The omega^2/omega^3 terms
    are mutual-coupling cross terms. All three results are PEAK amp magnitudes.
    """
    current_denominator = (((M12**2)*Z3*(omega**2))-(2j*M12*M13*M23*(omega**3))+((M13**2)*Z2*(omega**2))+((M23**2)*Z1*(omega**2))+(Z1*Z2*Z3))
    iloop = np.abs((Vs*(((M23**2)*(omega**2))+(Z2*Z3)))/current_denominator)
    itank = np.abs((Vs*((-1j*M12*Z3*omega)-(M13*M23*omega**2)))/current_denominator)
    irc = np.abs(Vs*(((-M12*M23*omega**2)-(1j*M13*Z2*omega))/current_denominator))

    return iloop, itank, irc


def power_accounting(iloop, itank, irc, Z1, Z2, Z3, Vpa,
                     RL, RL_ac, Z_ml, Z_mc, pa_efficiency=.85):
    """Power flow at one operating point.

    Vrect : DC bus voltage the OC matching network delivers into RL.
    PBatt : delivered DC power.
    Ploss_*: real power per mesh, RMS current into Re(Z). Ploss_rc subtracts
             PBatt because Z_load (inside Z3) already contains the load.
    Ipa   : back-solved from total power and assumed PA efficiency - derived,
            not simulated.
    """
    Vrect = np.abs((np.abs(irc)*np.sqrt(2*RL_ac*RL))/np.abs((2*(RL_ac+2*Z_ml)*((1/(RL_ac + 2*Z_ml))+(1/Z_mc)))))
    PBatt = (Vrect**2)/RL
    iloop_RMS = iloop / np.sqrt(2)
    itank_RMS = itank / np.sqrt(2)
    irc_RMS = irc / np.sqrt(2)
    Ploss_loop = ((iloop_RMS**2)*Z1).real
    Ploss_tank = ((itank_RMS**2)*Z2).real
    Ploss_rc = ((irc_RMS**2)*Z3).real - PBatt
    Total_Power = PBatt + Ploss_loop + Ploss_tank + Ploss_rc
    Ipa = (Total_Power/pa_efficiency) / (Vpa * 2*np.sqrt(2)/np.pi)

    return Vrect, PBatt, Ploss_loop, Ploss_tank, Ploss_rc, Total_Power, Ipa


def normalized_currents(omega, M12, M13, M23, Z2, Z3):
    """Loop and tank currents normalised to the RC current - the shape of the
    link, independent of drive level. The caller turns these into mag_normal,
    both a figure of merit and the inverse plot line width.
    """
    Itank_normalized = (1j*M12*Z3 + M13*M23*omega) / (M12*M23*omega + 1j*M13*Z2) #I_Tank/I_RC
    Iloop_normalized = (-((M23**2)*(omega**2)) - Z2*Z3) / (omega*(M12*M23*omega + 1j*M13*Z2)) #I_Loop/I_RC
    abs_Itank = np.abs(Itank_normalized)
    abs_Iloop = np.abs(Iloop_normalized)

    return abs_Iloop, abs_Itank
