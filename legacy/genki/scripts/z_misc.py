# /// script
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "quantiphy",
#     "sympy",
# ]
# ///

import sys

import matplotlib.pyplot as plt
import numpy as np
import sympy as sm

import zz

F1 = 6.78e6

imp = zz.Impedance(F1)

def c_q2():
    ciss, coss, crss, cgd, cgs, cds = sm.symbols('ciss, coss, crss, cgd, cgs, cds')
    sol = sm.solve([
        sm.Eq(ciss, cgd + cgs),
        sm.Eq(coss, cds + cgd),
        sm.Eq(crss, cgd),
    ], [cds, cgd, cgs])
    sol = {k: complex(v.subs({
        ciss: 500e-12,
        coss: 7.5e-12,
        crss: 3.8e-12,
        # ciss: 550e-12,
        # coss: 50e-12,
        # crss: 18e-12,
    }).evalf()) for k, v in sol.items()}
    z = zz.parallel(imp.zC(sol[cds]), imp.zC(sol[cgd]) + imp.zC(sol[cgs]))
    ceff = sm.symbols('ceff')
    val = float(sm.solve([sm.Eq(z, 1 / (sm.I * imp.w * ceff))], ceff)[ceff].evalf())
    print(q(val))
# c_q2()

def clamp_circuit_trip_vds():
    # Have to include the 10pF as this is an A/C signal
    a1 = zz.parallel(imp.zC(10e-12), 1.05e3)
    a2 = 17.4e3
    b1 = zz.parallel(imp.zC(560e-12), a1 + a2)
    b2 = imp.zC(56e-12)
    scale = abs(a1 / (a1 + a2) * b1 / (b1 + b2))

    trip_voltage = 10e3 / (10e3 + 10e3) * 5
    print(trip_voltage  / scale)
# clamp_circuit_trip_vds()

def harmonic_filter_at_fundamental():
    a = imp.zL(344e-9) + imp.zC(220e-12 + 180e-12)
    print(imp.z_pretty(a))
    a = imp.zL(307e-9) + imp.zC(100e-12 + 100e-12)
    print(imp.z_pretty(a))
# harmonic_filter_at_fundamental()

def match_components():
    for z in [
        imp.zC(1380e-12),
        imp.zL((185e-9*2 + 38e-9)/2),
        zz.parallel(imp.zC(220e-12), imp.zC(220e-12)),
    ]:
        print(z)
# match_components()

def match_symbolic():
    # w = sm.symbols('omega')
    # def zL(x):
    #     return sm.I * w * x
    # def zC(x):
    #     return 1/(sm.I * w * x)
    # parallel_ferds = False
    # CMatch = sm.symbols('C_{M}')
    # LMatch = sm.symbols('L_{M}')
    # C_FERD = sm.symbols('C_{D}')
    # R_LoadAC = sm.symbols('R_{L}')
    # z_FERD = zC(C_FERD)
    # z_load = z_.parallel(z_FERD, z_FERD, z_FERD, z_FERD, R_LoadAC) if parallel_ferds else z_.parallel(z_FERD, z_FERD, R_LoadAC)
    # z = z_.parallel(*[
    #     zC(CMatch),
    #     zL(LMatch/2) + z_load + zL(LMatch/2),
    # ])

    Z_CMatch = sm.symbols('C')
    Z_LMatch = sm.symbols('L')
    Z_FERDS = sm.symbols('F')
    R_LoadAC = sm.symbols('R')
    z = zz.parallel(*[
        Z_CMatch,
        Z_LMatch + zz.parallel(Z_FERDS, R_LoadAC)
    ])
    z = z.together()
    # z = z.expand()
    plt.text(0.1, 0.5, f"${sm.latex(z)}$", fontsize=20)
    plt.axis('off')
    plt.show()
# match_symbolic()

def oc1kwp():
    # cable = imp.zL(10e-9)
    parasitic = imp.zL(50e-9)
    # z = imp.zL(10e-9) + z_.parallel(
    z = z_.parallel(
        imp.zC(56e-12) + 5 + 5 + imp.zC(56e-12),
        imp.zC(1340e-12),
        imp.zL(370e-9) + parasitic,
    )
    # z = z_.parallel(z, imp.zC(1e-9))
    z = z_.parallel(z, imp.zC(56e-12))
    print(imp.z_pretty(z))
# oc1kwp()

# def rg8x():
#     z = z_.parallel(
#         imp.zC(71e-12),
#         imp.zL(120e-9),
#     )
#     print(imp.z_pretty(z))
# rg8x()

# breakpoint()

# def test1():
#     # cable = imp.zL(10e-9)
#     # cable = 0
#     # parasitic = imp.zL(10e-9)
#     # z = imp.zL(10e-9) + z_.parallel(
#     z = z_.parallel(
#         imp.zC(1.2e-9),
#         imp.zL(10e-9),
#     )
#     breakpoint()
#     print(imp.z_pretty(z))
# test1()

def ocload():
    zs = [
        (4, 83 + imp.zC(0.8e-9)),
        (10, 35 + imp.zC(1.8e-9)),
        (30, 11.5 + imp.zC(2.2e-9)),
        (60, 6.7 + imp.zC(2.3e-9)),
    ]
    for rload_dc, z in zs:
        print(f"== RLoadDC: {rload_dc} Ohms ==")
        print(f"Original OC load: {imp.z_pretty(z)}")
        for pF_parallel in [56, 100, 200, 500, 1200]:
            z = z_.parallel(imp.zC(pF_parallel * 1e-12), z)
            print(f"{pF_parallel}pF parallel:", imp.z_pretty(z))
        print()
ocload()
