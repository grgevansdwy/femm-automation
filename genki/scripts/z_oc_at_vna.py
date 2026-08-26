# /// script
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "quantiphy",
#     "sympy",
# ]
# ///

import matplotlib.pyplot as plt
import numpy as np

import zz

F1 = 6.78e6

imp = zz.Impedance(F1)
zC, zL = imp.zC, imp.zL
v_FERD = 1
z_FERD = zC({
    # Measured directly with VNA at 6.78 MHz. Uses 10Vpp aka 3.5Vrms.
    # 1: 20000e-12,

    # Measuring v_FERD with scope during VNA-at-SMA shows 400mVpp, aka 0.14Vrms.
    # So should be some decently large factor times the above measurement.
    # Totally hand-waving, but this does seem to match experimental results...
    1: 40000e-12,

    # From datasheet, at 1 MHz.
    # 1: 5100e-12,
    # 2: 1300e-12,
    # 3: 930e-12,
    # 10: 600e-12,
    # 80: 220e-12
}[v_FERD])

def z_fullbridge(Zleg, Zmiddle):
    # Use the Delta-Wye transform: https://en.wikipedia.org/wiki/Y-Δ_transform
    #
    # == INPUT NETWORK ==
    #   o-----------+
    #               |
    #       +-------+-------+
    #       |               |
    #      +-+             +-+
    #      |L|             |L|
    #      +-+             +-+
    #       |               |
    #       |  +---------+  |
    #       +--| Zmiddle |--+
    #       |  +---------+  |
    #       |               |
    #      +-+             +-+
    #      |L|             |L|
    #      +-+             +-+
    #       |               |
    #       +-------+-------+
    #               |
    #   o-----------+
    #
    # == OUTPUT NETWORK ==
    #   o-----------+-----------------+
    #               |                 |
    #       +-------+-------+         |
    #       |               |         |
    #      +-+             +-+        |
    #      |L|             |b|        |
    #      +-+             +-+        |
    #       |               |         |
    #       +-------+-------+        +-+
    #               |                |a|
    #       +-------+-------+        +-+
    #       |               |         |
    #      +-+             +-+        |
    #      |L|             |c|        |
    #      +-+             +-+        |
    #       |               |         |
    #       +-------+-------+         |
    #               |                 |
    #   o-----------+-----------------+
    Z1 = Zmiddle
    Z2 = Zleg
    Z3 = Zleg
    Za = Z2 + Z3 + (Z2 * Z3) / Z1
    Zb = Z1 + Z3 + (Z1 * Z3) / Z2
    Zc = Z1 + Z2 + (Z1 * Z2) / Z3
    return zz.parallel(zz.parallel(Zleg, Zb) + zz.parallel(Zleg, Zc), Za)

def oc1k(L_parasitic=0e-9, L_each_match_inductor=185e-9, with_rf_snubs=True, short_rectifier=False):
    if (not short_rectifier):
        raise Exception("The non-shorted-rectifier code doesn't work well yet.")
    z_rf_snub = (5.1 + zC(68e-12)) if with_rf_snubs else 1e12
    z_rect_snub = (5.1 + zC(68e-12))
    z_choke = zL(8 / imp.w)
    z_tvs = zC({1: 10000e-12}[round(v_FERD * 2 / np.pi)])
    z_dc_load = zz.parallel(zC(10e-9) + zC(10e-9), zC(0.47e-6 * 6), z_choke + zz.parallel(z_tvs, zC(100e-6), 45.2e3))
    z_rectifier = 0 if short_rectifier else z_fullbridge(zz.parallel(z_rect_snub, z_FERD), z_dc_load)
    z = zz.parallel(*[
        z_rf_snub + z_rf_snub,
        zC(330e-12 + 390e-12 + 330e-12 + 330e-12), # 1380pF
        zL(L_parasitic) + zL(L_each_match_inductor) + z_rectifier + zL(L_each_match_inductor),
    ])
    return imp.z_pretty(z)

def oc1k_wp(internal_coax_cm=15, Cmatch=1380e-12, L_parasitic=0e-9, L_each_match_inductor=185e-9,
            with_rf_snubs=True, short_rectifier=False):
    if (not short_rectifier):
        raise Exception("The non-shorted-rectifier code doesn't work well yet.")
    # TODO: Include AC_Div circuit
    z_internal_coax = zz.parallel(zL(135e-9 * internal_coax_cm / 61), zC(53e-12 * internal_coax_cm / 61))
    z_rf_snub = (5.0 + zC(56e-12)) if with_rf_snubs else 1e12
    z_rect_snub = (5.0 + zC(68e-12))
    z_dc_load = zz.parallel(zC(330e-9 * 16), zC(1e-9 * 4))
    z_rectifier = 0 if short_rectifier else z_fullbridge(zz.parallel(z_rect_snub, z_FERD), z_dc_load)
    z = z_internal_coax + zz.parallel(*[
        z_rf_snub + z_rf_snub,
        zC(Cmatch),
        zL(L_parasitic) + zL(L_each_match_inductor) + z_rectifier + zL(L_each_match_inductor),
    ])
    return imp.z_pretty(z)

print(f"OC1k-ST: Expect 56pF, get {oc1k(short_rectifier=True, with_rf_snubs=True, L_parasitic=36e-9)}")
print(f"OC1k-ST, no snubs: Expect 22pF, get {oc1k(short_rectifier=True, with_rf_snubs=False, L_parasitic=36e-9)}")

print(f"OC1k-WP (Lid On), 1380pF: Expect 100pF, get {oc1k_wp(short_rectifier=True, with_rf_snubs=True, Cmatch=1380e-12, L_parasitic=51e-9)}")
print(f"OC1k-WP (Lid On), 1380pF, no snubs: Expect 71pF, get {oc1k_wp(short_rectifier=True, with_rf_snubs=False, Cmatch=1380e-12, L_parasitic=51e-9)}")

print(f"OC1k-WP (Lid On), 1340pF: Expect 59pF, get {oc1k_wp(short_rectifier=True, with_rf_snubs=True, Cmatch=1340e-12, L_parasitic=51e-9)}")
print(f"OC1k-WP (Lid On), 1340pF, no snubs: Expect 31pF, get {oc1k_wp(short_rectifier=True, with_rf_snubs=False, Cmatch=1340e-12, L_parasitic=51e-9)}")
