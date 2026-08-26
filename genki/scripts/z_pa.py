# /// script
# dependencies = [
#     "coloredlogs",
#     "matplotlib",
#     "numpy",
#     "quantiphy",
#     "pysmithchart",
#     "sympy",
# ]
# ///

import argparse
import dataclasses
import enum
import logging
import os
import textwrap

import coloredlogs
import matplotlib.pyplot as plt
import numpy as np
import pysmithchart
import sympy as sm

import zz

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(
    logger=log,
    fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO, reconfigure=False)

plt.rcParams['figure.figsize'] = (12, 7)
plt.rcParams['lines.markersize'] = 2
plt.rcParams['lines.linestyle'] = '-'
plt.rcParams['lines.marker'] = 'o'
SEQ_CMAPS = ["Blues", "Reds", "Greens", "Purples", "Oranges"]

R1, R2, R3 = sm.symbols('R_1, R_2, R_3', positive=True, real=True)
L1, L2, L3 = sm.symbols('L_1, L_2, L_3', positive=True, real=True)
M12, M13, M23 = sm.symbols('M_{12}, M_{13}, M_{23}', positive=True, real=True)
k12, k13, k23 = sm.symbols('k_{12}, k_{13}, k_{23}', positive=True, real=True)
C1, C2, C3 = sm.symbols('C_1, C_2, C_3', positive=True, real=True)
Z1, Z2, Z3 = sm.symbols('Z_1, Z_2, Z_3', complex=True)
W = sm.symbols('omega', positive=True, real=True)
VS = sm.symbols('V_S', complex=True)

ciss, coss, crss, cgd, cgs, cds = sm.symbols('ciss, coss, crss, cgd, cgs, cds')

F1 = 6.78e6
DEFAULT_VDS_RMS = 200
DIST_MM_ARR = np.array([15, 20, 25, 30, 35, 40, 45, 50])
RLOAD_DC_ARR = np.array([4, 7, 10, 20, 30, 40, 75])

def float_equal(x, y):
    return np.isclose(x, y, rtol=1e-12, atol=0)

def basically_equal(x, y):
    if type(x) is float and type(y) is float:
        return float_equal(x, y)
    else:
        return x == y

class ZNode(enum.Enum):
    ZDCDC = 1
    ZDS = 2
    ZCablesCoilsOC = 3
    ZOC = 4

class ZFormat(enum.Enum):
    Mag = 1
    Phase = 2
    Real = 3
    Imag = 4
    L_positive_nH_else_C_negative_nF = 5
    Both = 6
    Smith = 7

    def convert(self, imp, x):
        return {
            ZFormat.Mag: np.abs(x),
            ZFormat.Phase: np.angle(x, deg=True),
            ZFormat.Real: np.real(x),
            ZFormat.Imag: np.imag(x),
            ZFormat.L_positive_nH_else_C_negative_nF: imp.reactance_to_l_or_c(x) * 1e9,
            ZFormat.Both: (np.real(x), imp.reactance_to_l_or_c(x) * 1e9),
            ZFormat.Smith: x,
        }[self]

@dataclasses.dataclass
class ZPaBase:
    def __init__(self, harmonic, vds_rms):
        self.imp = zz.Impedance(100e3 if harmonic == 0 else (F1 * harmonic))
        self.vds_rms = vds_rms
        self.CDS_GS66516T_ = self.CDS_GS66516T()
        log.info(f"CDS_GS66516T at Avg(VDS)={self.vds_rms} is {self.imp.z_pretty(self.imp.zC(self.CDS_GS66516T_))}")
        self.CDS_STGD6M65DF2_ = self.CDS_STGD6M65DF2()
        log.info(f"CDS_STGD6M65DF2 at Avg(VDS)={self.vds_rms} is {self.imp.z_pretty(self.imp.zC(self.CDS_STGD6M65DF2_))}")
        self.z_TRChoke_ = self.z_TRChoke()
        self.z_cables_coils_symbolic = self._z_cables_coils_symbolic()

    def _z_cables_coils_symbolic(self):
        PARAMS_Z = {
            Z1: R1 + sm.I*W*L1 + 1/(sm.I*W*C1),
            Z2: R2 + sm.I*W*L2 + 1/(sm.I*W*C2),
            Z3: R3 + sm.I*W*L3 + 1/(sm.I*W*C3),
        }
        Z = sm.Matrix([
            [Z1,         sm.I*W*M12, sm.I*W*M13],
            [sm.I*W*M12, Z2,         sm.I*W*M23],
            [sm.I*W*M13, sm.I*W*M23, Z3        ],
        ])
        V = sm.Matrix([VS, 0, 0])
        I = Z.solve(V)
        return (VS / I[0]).simplify().subs(PARAMS_Z).subs({W: self.imp.w}).subs({
            M12: k12 * sm.sqrt(L1 * L2),
            M13: k13 * sm.sqrt(L1 * L3),
            M23: k23 * sm.sqrt(L2 * L3),
        }).simplify()

    def _device_cds(self, datasheet_cap):
        sol = sm.solve([
            sm.Eq(ciss, cgd + cgs),
            sm.Eq(coss, cds + cgd),
            sm.Eq(crss, cgd),
        ], [cds, cgd, cgs])
        sol = {k: complex(v.subs(datasheet_cap).evalf()) for k, v in sol.items()}
        z = zz.parallel(self.imp.zC(sol[cds]), self.imp.zC(sol[cgd]) + self.imp.zC(sol[cgs]))
        ceq = sm.symbols('ceq')
        return float(sm.solve([sm.Eq(z, 1 / (sm.I * self.imp.w * ceq))], ceq)[ceq].evalf())

    def CDS_GS66516T(self):
        # Source: https://www.infineon.com/part/GS66516T-TR
        return self._device_cds({
            0: {
                ciss: 600e-12,
                coss: 1000e-12,
                crss: 140e-12,
            },
            1: {
                ciss: 580e-12,
                coss: 990e-12,
                crss: 120e-12,
            },
            100: {
                ciss: 510e-12,
                coss: 395e-12,
                crss: 6.5e-12,
            },
            180: {
                ciss: 520e-12,
                coss: 260e-12,
                crss: 6.1e-12,
            },
            200: {
                ciss: 525e-12,
                coss: 250e-12,
                crss: 6e-12,
            },
        }[self.vds_rms])

    def CDS_STGD6M65DF2(self):
        # Source: https://www.st.com/content/ccc/resource/technical/document/datasheet/group0/d3/7a/5d/01/5a/4b/4f/e6/DM00250120/files/DM00250120.pdf/jcr:content/translations/en.DM00250120.pdf
        return self._device_cds({
            0: {
                ciss: 810e-12,
                coss: 510e-12,
                crss: 340e-12,
            },
            1: {
                ciss: 570e-12,
                coss: 140e-12,
                crss: 55e-12,
            },
            200: {
                ciss: 510e-12,
                coss: 13e-12,
                crss: 4.4e-12,
            }
        }[self.vds_rms])

    def z_TRChoke(self):
        # Source: Arena (TR1K Rev K)
        return zz.parallel(self.imp.zL(47e-6), self.imp.zC(12e-12)) + zz.parallel(*[
            self.imp.zL(5e-9 / 8) + self.imp.zC(0.47e-6 * 8),
            self.imp.zL(5e-9 / 2) + self.imp.zC(33e-6 * 2),
        ])

@dataclasses.dataclass
class ZPaConfig:
    ## Source: Arena (TR1K PCBA Rev G)
    #C_Parallel_: float = 200e-12
    #C_Series_: float = ((47e-12 * 11) + 23e-12)
    #L_Series_: float = 1240e-9

    # Source: "EE-324 Reduce/remove L2 in TR1K PA"
    C_Parallel_: float = 36e-12 + (47e-12 * 2)
    C_Series_: float = (180e-12 * 9) + 220e-12 + (100e-12 * 2)
    L_Series_: float = 510e-9

    # Source: "EE-377 Measure-TR-PA-board-parasitics"
    L_TRParasitic_: float = 50e-9

    # Source: Arena (Gen 2 coils)
    CTCLoop_: float = 2310e-12
    CTCTank_: float = 101e-12
    CRC_: float = 318e-12

    # Source: "L_residual - Targets"
    L_RCCoax_: float = 139e-9

    # Source: Arena (OC1K Rev E)
    C_OCMatch_: float = (330e-12 * 3 + 390e-12)
    L_OCParasitic_: float = 38e-9
    L_OCEachMatchL_: float = 185e-9
    oc_rf_snubbers: bool = True
    oc_parallel_ferds: bool = False

    # Source: FERD40H100S Datasheet. Assumes reverse-bias of 85 V.
    C_SingleFERDOffState_: float = 210e-12

def common_and_unique(cfgs):
    dicts = [dataclasses.asdict(cfg) for _, cfg in cfgs]
    common = {
        k: dicts[0][k]
        for k in dicts[0]
        if all(basically_equal(d[k], dicts[0][k]) for d in dicts[1:])
    }
    diff_keys = {k for d in dicts for k in d} - common.keys()
    common_str = ", ".join(f"{k}={zz.qpretty(v)}" for k, v in common.items())
    return f"Common: {common_str}", diff_keys

class ZPa:

    def __init__(self, base, cfg, znode, zformat):
        self.base = base
        self.cfg = cfg
        self.znode = znode
        self.zformat = zformat

    def zds_nochoke(self, zload):
        # Source: Arena (TR1K Rev K)
        zC, zL = self.base.imp.zC, self.base.imp.zL
        return zz.parallel(*[
            zC(self.base.CDS_GS66516T_),
            47 + zC(self.base.CDS_STGD6M65DF2_),
            zC(self.cfg.C_Parallel_),
            zC(56e-12) + zz.parallel(zC(560e-12), zC(30e-12)),
            zL(334e-9) + zz.parallel(zC(220e-12), zC(180e-12)), # Note: Schematic says 344nH but Arena/Drawing says 334nH.
            zC(self.cfg.C_Series_) + 140e-3 + zL(self.cfg.L_Series_) + zL(self.cfg.L_TRParasitic_) + zz.parallel(*[
                zL(307e-9) + zC(100e-12 * 2),
                zL(160e-9) + zz.parallel(*[
                    zC(220e-12),
                    zC(220e-12),
                    zC(220e-12),
                    zC(180e-12),
                    zC(100e-12),
                    zC(220e-12),
                    zz.parallel(zL(125e-9), zC(100e-12 + 180e-12)) + zz.parallel(*[
                        zC(220e-12),
                        zC(56e-12) + zz.parallel(22, 22),
                        zload,
                    ]),
                ])
            ])
        ])

    def zds(self, zload):
        return zz.parallel(*[
            self.base.z_TRChoke_,
            self.zds_nochoke(zload),
        ])

    def zdcdc(self, zload):
        # Source: Arena (TR1K Rev K)
        zC, zL = self.base.imp.zC, self.base.imp.zL
        return zz.parallel(*[
            zL(5e-9 / 2) + zC(33e-6 * 2),
            zL(5e-9 / 8) + zC(0.47e-6 * 8),
            zz.parallel(zL(47e-6), zC(12e-12)) + self.zds_nochoke(zload),
        ])

    def vna_ntype_into_tr(self, label):
        # Source: Arena (TR1K Rev K)
        assert(self.base.vds_rms == 1)
        zC, zL = self.base.imp.zC, self.base.imp.zL
        z = zz.parallel(*[
            zC(56e-12) + zz.parallel(22, 22),
            zC(220e-12),
            zz.parallel(zL(125e-9), zC(100e-12 + 180e-12)) + zz.parallel(*[
                zC(220e-12),
                zC(100e-12),
                zC(180e-12),
                zC(220e-12),
                zC(220e-12),
                zC(220e-12),
                zL(160e-9) + zz.parallel(*[
                    zL(307e-9) + zC(100e-12 * 2),
                    zL(self.cfg.L_TRParasitic_) + 140e-3 + zL(self.cfg.L_Series_) + zC(self.cfg.C_Series_) + zz.parallel(*[
                        zL(334e-9) + zz.parallel(zC(220e-12), zC(180e-12)), # Note: Schematic says 344nH but Arena/Drawing says 334nH.
                        zC(56e-12) + zz.parallel(zC(560e-12), zC(30e-12)),
                        zC(self.cfg.C_Parallel_),
                        self.base.z_TRChoke_,
                        47 + zC(self.base.CDS_STGD6M65DF2_),
                        zC(self.base.CDS_GS66516T_),
                    ])
                ])
            ])
        ])
        print(label)
        print(self.base.imp.z_pretty(z))
        print()

    def z_cables_coils_oc(self, z_oc, dist_mm):
        # Sources:
        # - "EE-354 Use-VNA-to-measure-L-M-for-coils" Gen 2 coils
        # - "Coil and Cable Measurements" Google Sheet
        GEN2_COIL_PARAMS = {
            L1: 219e-9,
            L2: 5.573e-6,
            L3: 1.671e-6,
            C1: self.cfg.CTCLoop_,
            C2: self.cfg.CTCTank_,
            C3: self.cfg.CRC_,
            R1: 28e-3 + 117e-3 + self.base.imp.zL(160e-9),
            R2: 1.85,
            R3: 389e-3 + 220e-3 + self.base.imp.zL(self.cfg.L_RCCoax_) + z_oc,
            k12: 0.672,
            k13: {10: 0.4182, 15: 0.3549, 20: 0.3192, 25: 0.2783, 30: 0.2443, 35: 0.2152, 40: 0.1882, 45: 0.1713, 50: 0.1487}[dist_mm],
            k23: {10: 0.5431, 15: 0.4777, 20: 0.4200, 25: 0.3708, 30: 0.3233, 35: 0.2886, 40: 0.2552, 45: 0.2270, 50: 0.2031}[dist_mm],
        }
        return complex(self.base.z_cables_coils_symbolic.subs(GEN2_COIL_PARAMS).evalf())

    def z_OC1K(self, rload_dc):
        # Source: Arena (OC1K Rev E)
        zC, zL = self.base.imp.zC, self.base.imp.zL
        z_rf_snub = (5.1 + zC(68e-12)) if self.cfg.oc_rf_snubbers else 1e12
        z_rect_snub = (5.1 + zC(68e-12))
        rload_ac = 8 / (np.pi ** 2) * rload_dc
        z_SingleFERDOffState = zz.parallel(zC(self.cfg.C_SingleFERDOffState_), z_rect_snub)
        # NOTE: This calculates the instantaneous impedance seen during power
        # transfer.  In other words, it assumes that rectification is actively
        # happening, and thus two legs of the H-bridge are conducting (modeled
        # as a short) while the other two legs are off (modeled by their
        # junction capacitance when reverse-biased). This is not necessarily
        # what one would experience at the VNA.
        z_rectifier = zz.parallel(z_SingleFERDOffState, z_SingleFERDOffState, z_SingleFERDOffState, z_SingleFERDOffState, rload_ac) \
            if self.cfg.oc_parallel_ferds else zz.parallel(z_SingleFERDOffState, z_SingleFERDOffState, rload_ac)
        return zz.parallel(*[
            z_rf_snub + z_rf_snub,
            zC(self.cfg.C_OCMatch_),
            zL(self.cfg.L_OCParasitic_) + zL(self.cfg.L_OCEachMatchL_) + z_rectifier + zL(self.cfg.L_OCEachMatchL_),
        ])

    def z_at(self, rload_dc, dist_mm):
        return self.zformat.convert(self.base.imp, {
            ZNode.ZDCDC: self.zdcdc(self.z_cables_coils_oc(self.z_OC1K(rload_dc), dist_mm)),
            ZNode.ZDS: self.zds(self.z_cables_coils_oc(self.z_OC1K(rload_dc), dist_mm)),
            ZNode.ZCablesCoilsOC: self.z_cables_coils_oc(self.z_OC1K(rload_dc), dist_mm),
            ZNode.ZOC: self.z_OC1K(rload_dc),
        }[self.znode])

    def _plot_sweep(self, ax, sweep_arr, z_arr, label):
        if self.zformat == ZFormat.Smith:
            ax.plot(z_arr, "o-", label=label)
            plt.legend()
        elif self.zformat == ZFormat.Both:
            z_real = [z[0] for z in z_arr]
            z_imag = [z[1] for z in z_arr]
            first_plot = not plt.gca().has_data()
            plt.plot(z_real, z_imag, label=label)
            if first_plot:
                for a, x, y in zip(sweep_arr, z_real, z_imag):
                    plt.annotate(str(a), (x, y), xytext=(5, 5), textcoords="offset points")
                plt.xlabel("Real (Ω)")
                plt.ylabel("Imag (Positive: nH, Negative: nF)")
            plt.axhline(0, color='black', linewidth=1)
            plt.grid(True)
            plt.legend()
        else:
            plt.plot(sweep_arr, z_arr, label=label)
            plt.axhline(0, color='black', linewidth=1)
            plt.grid(True)
            plt.legend()

    def sweep_distance_at_rload_dc(self, ax, rload_dc, label):
        z_arr = np.array([self.z_at(rload_dc, dist_mm) for dist_mm in DIST_MM_ARR])
        self._plot_sweep(ax, DIST_MM_ARR, z_arr, label)
        if self.zformat not in [ZFormat.Both, ZFormat.Smith]:
            plt.xlabel("Distance (mm)")

    def sweep_rload_dc_at_distance(self, ax, dist_mm, label):
        z_arr = np.array([self.z_at(rload_dc, dist_mm) for rload_dc in RLOAD_DC_ARR])
        self._plot_sweep(ax, RLOAD_DC_ARR, z_arr, label)
        if self.zformat not in [ZFormat.Both, ZFormat.Smith]:
            plt.xlabel("RLoadDC (Ω)")

    def sweep_distance_and_rload_dc(self):
        X, Y = np.meshgrid(DIST_MM_ARR, RLOAD_DC_ARR)
        z_arr = np.empty_like(X)
        for i in range(X.shape[0]):
            for j in range(X.shape[1]):
                dist_mm = X[i, j]
                rload_dc = Y[i, j]
                z_arr[i, j] = self.z_at(rload_dc, dist_mm)
        return X, Y, z_arr

    def xyz_3D(self, X, Y, z_arr_arr, label_arr, common_str, title_str):
        fig = plt.figure()
        ax = fig.add_subplot(111, projection='3d')
        cmap = plt.get_cmap("tab10")
        for i, (z_arr, label) in enumerate(zip(z_arr_arr, label_arr)):
            ax.plot_surface(X, Y, z_arr, alpha=0.6, color=cmap(i % cmap.N), label=label)
        ax.set_xlabel("Distance (mm)")
        ax.set_ylabel("RLoadDC (Ω)")
        ax.set_zlabel(title_str)
        fig.suptitle(textwrap.fill(common_str, width=80))
        plt.legend()
        plt.tight_layout()
        plt.show()

    def xyz_contour(self, X, Y, z_arr_arr, label_arr, common_str, title_str):
        fig = plt.figure()
        vmin = min(z_arr.min() for z_arr in z_arr_arr)
        vmax = max(z_arr.max() for z_arr in z_arr_arr)
        for i, (z_arr, label) in enumerate(zip(z_arr_arr, label_arr)):
            cmap = "seismic" if len(z_arr_arr) == 1 else SEQ_CMAPS[i]
            contour = plt.contour(X, Y, z_arr, cmap=cmap, levels=np.linspace(vmin, vmax, 10))
            plt.colorbar(contour, label=label)
        fig.suptitle(textwrap.fill(common_str, width=80))
        plt.xlabel("Distance (mm)")
        plt.ylabel("RLoadDC (Ω)")
        plt.grid(True)
        plt.title(title_str)
        plt.tight_layout()
        plt.show()

    def xyz_pcolormesh(self, X, Y, z_arr_arr, label_arr, common_str, title_str):
        fig, axs = plt.subplots(1, len(z_arr_arr), constrained_layout=True)
        if len(z_arr_arr) == 1:
            axs = [axs]
        vmin = min(z_arr.min() for z_arr in z_arr_arr)
        vmax = max(z_arr.max() for z_arr in z_arr_arr)
        for i, (z_arr, label) in enumerate(zip(z_arr_arr, label_arr)):
            im = axs[i].pcolormesh(X, Y, z_arr, vmin=vmin, vmax=vmax, cmap="viridis", shading="auto")
            for py in range(z_arr.shape[0]):
                for px in range(z_arr.shape[1]):
                    axs[i].text(X[py, px], Y[py, px], f"{z_arr[py, px]}", ha='center', va='center', color='white')
            axs[i].set_xlabel("Distance (mm)")
            axs[i].set_ylabel("RLoadDC (Ω)")
            axs[i].set_title(textwrap.fill(label, width=80))
            if i == 0:
                fig.colorbar(im, ax=axs)
        fig.suptitle(f"{title_str} [{textwrap.fill(common_str, width=80)}]")
        plt.show()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Impedance of the PA")
    parser.add_argument("--harmonic", default=1, type=int, help="Harmonic to analyze (default fundamental)")
    parser.add_argument("--xyz-difference", action="store_true", help="Plot the percent difference for --sweep-distance-and-rload-dc")
    parser.add_argument("--znode", default=str(ZNode.ZDS).split(".")[1], choices=list(ZNode.__members__.keys()), help="Which Z node")
    parser.add_argument("--zformat", default=str(ZFormat.L_positive_nH_else_C_negative_nF).split(".")[1], choices=list(ZFormat.__members__.keys()), help="Which Z format")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sweep-distance-and-rload-dc", action='store_true', help="Sweep (distance, rload_dc)")
    group.add_argument("--sweep-distance-at-rload-dc", type=float, help="Sweep distance at the specified rload_dc", metavar="RLOADDC")
    group.add_argument("--sweep-rload-dc-at-distance", type=int, help="Sweep rload_dc at the specified distance", metavar="DISTANCE")
    group.add_argument("--vna-ntype-into-tr", action='store_true', help="Print the impedance that the VNA would see at the N-Type connector looking into the TR. Assumes VDS_RMS=1.")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--xyz-3D", action="store_true", help="Use a 3D plot for --sweep-distance-and-rload-dc")
    group.add_argument("--xyz-contour", action="store_true", help="Use a contour plot for --sweep-distance-and-rload-dc")
    group.add_argument("--xyz-pcolormesh", default=True, action="store_true", help="Use a pcolormesh plot for --sweep-distance-and-rload-dc")

    args = parser.parse_args()
    zformat = ZFormat[args.zformat]
    if args.sweep_distance_and_rload_dc and (zformat == ZFormat.Both):
        raise Exception("ZFormat.Both and --sweep-distance-and-rload-dc are currently incompatible.")

    zpa_base = ZPaBase(args.harmonic, 1 if args.vna_ntype_into_tr else DEFAULT_VDS_RMS)
    cfgs = [
        ("OC1k-ST", ZPaConfig()),
        ("OC1k-ST, 90pF", ZPaConfig(CTCTank_=90e-12)),
        ("OC1k-ST, 110pF", ZPaConfig(CTCTank_=110e-12)),
        # ("OC1k-WP originally-failing setup", ZPaConfig(L_OCParasitic_=51e-9, C_OCMatch_=1340e-12, L_RCCoax_=((135e-9)*2 + (135e-9)*(15/61)), CRC_=282e-12)),
        # ("OC1k-WP new proposal, 1380p", ZPaConfig(L_OCParasitic_=51e-9, C_OCMatch_=1380e-12, L_RCCoax_=((135e-9)*2 + (135e-9)*(15/61)), oc_rf_snubbers=False, CRC_=295e-12)),
        # ("OC1k-WP new proposal", ZPaConfig(L_OCParasitic_=51e-9, C_OCMatch_=1340e-12, L_RCCoax_=((135e-9)*2 + (135e-9)*(15/61)), oc_rf_snubbers=False, CRC_=295e-12)),
    ]
    common_str, diff_keys = common_and_unique(cfgs)
    if args.sweep_distance_and_rload_dc:
        X, Y, z_arr_arr, label_arr = None, None, [], []
    elif args.vna_ntype_into_tr:
        print(common_str)
        print()
    elif zformat == ZFormat.Smith:
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1, projection="smith", Z0=50)
    else:
        ax = None
    znode = ZNode[args.znode]
    for i, (name, cfg) in enumerate(cfgs):
        label = f"{name}: " + ", ".join(f"{key}={zz.qpretty(getattr(cfg, key))}" for key in diff_keys)
        zpa = ZPa(zpa_base, cfg, znode, zformat)
        if args.sweep_distance_and_rload_dc:
            X, Y, z_arr = zpa.sweep_distance_and_rload_dc()
            z_arr_arr.append(z_arr)
            label_arr.append(label)
        elif args.sweep_distance_at_rload_dc is not None:
            zpa.sweep_distance_at_rload_dc(ax, args.sweep_distance_at_rload_dc, label)
        elif args.sweep_rload_dc_at_distance is not None:
            zpa.sweep_rload_dc_at_distance(ax, args.sweep_rload_dc_at_distance, label)
        elif args.vna_ntype_into_tr:
            zpa.vna_ntype_into_tr(label)
    if args.sweep_distance_and_rload_dc:
        f = zpa.xyz_3D if args.xyz_3D else (zpa.xyz_contour if args.xyz_contour else (zpa.xyz_pcolormesh if args.xyz_pcolormesh else Exception("Coding Error")))
        if args.xyz_difference:
            assert(len(z_arr_arr) == 2)
            z_arr_arr = [(z_arr_arr[1] - z_arr_arr[0]) / (z_arr_arr[0]) * 100]
            label_arr = [f"% Diff of Z2 wrt Z1, where Z1 is {label_arr[0]} and Z2 is {label_arr[1]}"]
        title_str = f"{args.znode} [{args.zformat}]"
        f(X, Y, z_arr_arr, label_arr, common_str, title_str)
    elif args.sweep_distance_at_rload_dc is not None:
        plt.title(f"{args.znode} [{args.zformat}] [rload_dc={args.sweep_distance_at_rload_dc}] [{textwrap.fill(common_str, width=80)}]")
        plt.show()
    elif args.sweep_rload_dc_at_distance is not None:
        plt.title(f"{args.znode} [{args.zformat}] [distance={args.sweep_rload_dc_at_distance}] [{textwrap.fill(common_str, width=80)}]")
        plt.show()
