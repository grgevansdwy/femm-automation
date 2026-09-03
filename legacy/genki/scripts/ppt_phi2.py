# /// script
# dependencies = [
#     "numpy",
#     "quantiphy",
#     "sympy",
# ]
# ///

import sympy as sm
import quantiphy

import zz

imp = zz.Impedance(6.78e6)

# Reference:
#
# L. Gu and J. Rivas-Davila
# "1.7 kW 6.78 MHz Wireless Power Transfer with Air-Core Coils at 95.7% DC-DC Efficiency"
# https://ieeexplore.ieee.org/document/9458037/

f_s, k = sm.symbols('f_s, k')
L_TC, L_RC, R_TC, R_RC = sm.symbols('L_TC, L_RC, R_TC, R_RC')
V_Rect, P_Rect = sm.symbols('V_Rect, P_Rect')

R_LoadDC = V_Rect**2 / P_Rect
R_LoadAC = R_LoadDC * 8 / sm.pi**2
V_LoadAC = sm.sqrt(2 * P_Rect * R_LoadAC)

w_s = 2 * sm.pi * f_s
gamma = R_LoadAC / (w_s * L_RC)
Q_TC = w_s * L_TC / R_TC
Q_RC = w_s * L_TC / R_RC

# Optimal SS compensation
C_TC_comp = 1 / (w_s**2 * L_TC)
C_RC_comp = 1 / (w_s**2 * L_RC)

opt_wpt_link_efficiency = sm.Eq(
    gamma,
    1 / Q_RC * sm.sqrt(1 + k**2 * Q_TC * Q_RC)
)

Q = sm.sqrt(Q_TC * Q_RC)
eta_max = k**2 * Q**2 / (1 + sm.sqrt(1 + k**2 * Q**2))**2
G_v = gamma / k * sm.sqrt(L_RC / L_TC)

INPUT_BASE = {
    f_s: 6.78e6,
    L_TC: 6.25e-6,
    # P_Rect: 1650,
    P_Rect: 1000,
    # R_TC: (w_s*L_TC / 700).subs({L_TC: 6.25e-6, f_s: 6.78e6}).evalf(),
    # R_RC: (w_s*L_TC / 700).subs({L_TC: 6.25e-6, f_s: 6.78e6}).evalf(),
    R_TC: 1,
    R_RC: 1,
}
INPUT_BASE

# Given above constraints, including target power, solve for L_RC given a desired
# VRect at a desired coupling coefficient. Also require optimal link efficiency.
L_RC_ = sm.solve(opt_wpt_link_efficiency, L_RC)[0].subs(INPUT_BASE).subs({
    # k: 0.28,
    k: 1/2 * (0.314 + 0.48),
    # V_Rect: 400,
    V_Rect: 85,
}).evalf()
print(f"TC: {imp.z_pretty(INPUT_BASE[R_TC] + imp.zL(float(INPUT_BASE[L_TC])))}")
print(f"RC: {imp.z_pretty(INPUT_BASE[R_RC] + imp.zL(float(L_RC_)))}")

# Given this static setup, just vary k and see what VRect will be while
# maintaining target power.
sols_VRect = sm.solve(opt_wpt_link_efficiency, V_Rect)
for k_ in [0.314, 1/2 * (0.314 + 0.48), 0.48]:
    extra_inputs = {
        k: k_,
        L_RC: L_RC_,
    }
    vrect_opt_ = sols_VRect[1].subs(INPUT_BASE).subs(extra_inputs).evalf()
    eta_max_ = eta_max.subs(INPUT_BASE).subs(extra_inputs).evalf()
    G_v_ = G_v.subs(INPUT_BASE).subs(extra_inputs).subs({
        V_Rect: vrect_opt_,
    }).evalf()
    v_loadac_ = V_LoadAC.subs(INPUT_BASE).subs(extra_inputs).subs({
        V_Rect: vrect_opt_,
    }).evalf()
    v_primaryac_ = v_loadac_ / G_v_
    v_ds_ = (v_primaryac_ / (4 / sm.pi)).evalf() # first harmonic -> square
    v_input_ = v_ds_ / 2 # Approximation
    p_input_ = INPUT_BASE[P_Rect] / eta_max_
    i_input_ = p_input_ / v_input_
    r_loaddc_ = vrect_opt_**2 / INPUT_BASE[P_Rect]
    eta_actual_ = INPUT_BASE[P_Rect] / (v_input_ * i_input_)
    efficiency_discrepancy = abs(eta_actual_ - eta_max_)
    assert(efficiency_discrepancy < 0.001)
    print(f"k={k_:0.2f}, eta_max={eta_max_:0.2f}, v_in={v_input_:0.1f}, i_in={i_input_:0.1f}, v_ds={v_ds_:0.1f}, vrect={vrect_opt_:0.1f}, r_loaddc={r_loaddc_:0.1f}")
