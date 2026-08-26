# /// script
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "quantiphy",
#     "sympy",
# ]
# ///

# Measuring resonant frequency of a coil using another coil.

import matplotlib.pyplot as plt
import numpy as np
import sympy as sm

import zz

plt.rcParams['figure.figsize'] = (12, 7)
plt.rcParams['lines.markersize'] = 2
plt.rcParams['lines.linestyle'] = '-'
plt.rcParams['lines.marker'] = 'o'

C1, C2 = sm.symbols('C_1, C_2', positive=True, real=True)
Cp1, Cp2 = sm.symbols('Cp_1, Cp_2', positive=True, real=True)
L1, L2 = sm.symbols('L_1, L_2', positive=True, real=True)
M12 = sm.symbols('M_{12}', positive=True, real=True)
R1, R2 = sm.symbols('R_1, R_2', positive=True, real=True)
VS = sm.symbols('V_S', complex=True)
W = sm.symbols('omega', positive=True, real=True)
Z1, Z2 = sm.symbols('Z_1, Z_2', complex=True)
k12 = sm.symbols('k_{12}', positive=True, real=True)

imp_sym = zz.ImpedanceSymbolic(W)

Z_SYMBOLS = {
    Z1: imp_sym.zC(C1) + zz.parallel(R1 + imp_sym.zL(L1), imp_sym.zC(Cp1)),
    Z2: imp_sym.zC(C2) + zz.parallel(R2 + imp_sym.zL(L2), imp_sym.zC(Cp2)),
}
Z = sm.Matrix([
    [Z1,          imp_sym.zL(M12)],
    [imp_sym.zL(M12), Z2,        ],
])
V = sm.Matrix([VS, 0])
I = Z.solve(V)

MEASUREMENTS_SELF = {
    L1: 0.267e-6,
    L2: 5.704e-6,
    Cp1: 89e-12,
    Cp2: 16.88e-12,
    R1: 0.109,
    R2: 2.158,
}

def c_resonant(f, l, cp):
    """Calculate the resonant capacitor for the (l || cp) coil at @f."""
    imp_f = zz.Impedance(f=f)
    l_eff = imp_f.reactance_to_l_or_c(zz.parallel(imp_f.zL(l), imp_f.zC(cp)), keep_sign=False)
    return imp_f.resonantX(l_eff)

TUNING_CAPS = {
    C1: c_resonant(6.9e6, MEASUREMENTS_SELF[L1], MEASUREMENTS_SELF[Cp1]),
    C2: c_resonant(6.72e6, MEASUREMENTS_SELF[L2], MEASUREMENTS_SELF[Cp2]),
}
print(TUNING_CAPS)

for title, val in [
    # ("Coil 1 Alone", Z1),
    ("ZIn", VS / I[0]),
]:
    val = val.simplify().subs(Z_SYMBOLS).subs(MEASUREMENTS_SELF).subs(TUNING_CAPS)
    for M12_ in [
        1e-12,
        1e-9,
        10e-9,
        50e-9,
        75e-9,
        100e-9,
        417e-9,
        1e-6,
        1e-3,
    ]:
        f = np.linspace(6e6, 8e6, 1000)
        val_ = sm.lambdify(W, val.subs({M12: M12_}), 'numpy')
        plt_ = np.imag(val_(f * 2 * np.pi))
        plt_ /= np.max(plt_)
        plt.plot(f / 1e6, plt_, label=f"M12={zz.qpretty(M12_)}")
    plt.title(title)
    plt.xlabel("f [MHz]")
    plt.ylabel("Ohms (Normalized)")
    plt.grid(linestyle='--', linewidth=0.5, alpha=0.7)
    plt.legend()
    plt.show()
