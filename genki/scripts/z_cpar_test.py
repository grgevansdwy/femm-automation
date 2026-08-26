# /// script
# dependencies = [
#     "matplotlib",
#     "quantiphy",
#     "numpy",
#     "sympy",
# ]
# ///

import matplotlib.pyplot as plt
import numpy as np
import sympy as sm

import zz

print()
print()
print("== WARNING: There seems to be a bug in this code ==")
print()
print()

# F1 = 6.78e6

# imp = z_.Impedance(F1)
W = sm.symbols('omega', positive=True, real=True)
imp = zz.ImpedanceSymbolic(W)

R1, R2 = sm.symbols('R_1, R_2', positive=True, real=True)
L1, L2 = sm.symbols('L_1, L_2', positive=True, real=True)
M12 = sm.symbols('M_{12}', positive=True, real=True)
k12 = sm.symbols('k_{12}', positive=True, real=True)
# C1, C2 = sm.symbols('C_1, C_2', positive=True, real=True)
Cp1, Cp2 = sm.symbols('Cp_1, Cp_2', positive=True, real=True)
Z1, Z2 = sm.symbols('Z_1, Z_2', complex=True)
VS = sm.symbols('V_S', complex=True)
PARAMS_Z = {
    # Z1: R1 + imp.zC(C1) + z_.parallel(imp.zL(L1), imp.zC(Cp1)),
    # Z2: R2 + imp.zC(C2) + z_.parallel(imp.zL(L2), imp.zC(Cp2)),
    # Z1: z_.parallel(R1 + imp.zL(L1), imp.zC(Cp1)),
    # Z2: z_.parallel(R2 + imp.zL(L2), imp.zC(Cp2)),
    Z1: zz.parallel(imp.zL(L1), imp.zC(Cp1)),
    Z2: zz.parallel(imp.zL(L2), imp.zC(Cp2)),
    # Z1: R1 + imp.zC(C1) + imp.zL(L1),
    # Z2: R2 + imp.zC(C2) + imp.zL(L2),
}
Z = sm.Matrix([
    [Z1,          imp.zL(M12)],
    [imp.zL(M12), Z2,        ],
])
V = sm.Matrix([VS, 0])
I = Z.solve(V)

# x = I[1].simplify().subs(PARAMS_Z).simplify()
for loc in [0, 1]:
    # latex = sm.latex(I[loc].subs(PARAMS_Z))
    latex = sm.latex(I[loc])
    # latex = sm.latex(I[loc].subs(PARAMS_Z).simplify())
    # latex = sm.latex(I[loc].subs(PARAMS_Z).expand())
    fig, ax = plt.subplots(figsize=(6, 1.5))
    ax.axis("off")
    ax.set_title(f"I[{loc}]")
    ax.text(0.5, 0.5, rf"${latex}$", ha="center", va="center", fontsize=22)
    plt.tight_layout()
    plt.show(block=False)
plt.show()

# for param_set in [
#     {
#         L2: 6.9117e-6,
#         Cp2: 0.000001e-12,
#         k12: 0.370,
#     },
#     {
#         L2: 5.777e-6 * (5.724 / 5.797),
#         Cp2: 16.88e-12,
#         k12: 0.331 * (0.334 / 0.326),
#     },
#     {
#         L2: 6.9117e-6,
#         Cp2: 16.88e-12,
#         k12: 0.331 * (0.334 / 0.326),
#     },
#     {
#         L2: 5.777e-6 * (5.724 / 5.797),
#         Cp2: 0.000001e-12,
#         k12: 0.331 * (0.334 / 0.326),
#     },
#     {
#         L2: 5.777e-6 * (5.724 / 5.797),
#         Cp2: 16.88e-12,
#         k12: 0.370,
#     },
# ]:
#     for x, formatter in [
#         # (VS / I[0], lambda x_: f"Z: {imp.z_pretty(x_)} (mag={np.abs(x_):0.2f}, angle={np.angle(x_, deg=True):0.1f})"),
#         (I[0].subs({VS: 100}), lambda x_: f"I1: RMS={np.abs(x_)/np.sqrt(2):0.2f}, mag={np.abs(x_):0.2f}, angle={np.angle(x_, deg=True):0.2f}"),
#         (I[1].subs({VS: 100}), lambda x_: f"I2: RMS={np.abs(x_)/np.sqrt(2):0.2f}, mag={np.abs(x_):0.2f}, angle={np.angle(x_, deg=True):0.2f}"),
#     ]:
#         x_ = complex(x.simplify().subs(PARAMS_Z).subs({W: imp.w}).subs({
#             M12: k12 * sm.sqrt(L1 * L2),
#         }).subs({
#             L1: 0.279e-6,
#             R1: 1,
#             R2: 1,
#             C1: imp.resonantX(0.279e-6),
#             C2: imp.resonantX(6.9117e-6),
#             Cp1: 0.000001e-12,
#         }).subs(param_set).evalf())
#         print(formatter(x_))
#     print()
