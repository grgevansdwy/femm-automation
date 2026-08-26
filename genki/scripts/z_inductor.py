# /// script
# dependencies = [
#     "matplotlib",
#     "numpy",
#     "quantiphy",
# ]
# ///

import dataclasses

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.ticker as plt_ticker

import zz

F0 = 6.78e6
VNA_Z0 = 50
VNA_POWER_W = 1e-3 # 0 dBm

def zL(w, L):
    return 1j * w * L

def zC(w, C):
    return 1 / (1j * w * C)

@dataclasses.dataclass
class Core:
    Ae_cm2: float
    Ve_cm3: float
    loss_a: float
    loss_b: float
    loss_c: float
    loss_d: float

    def Ae_m2(self):
        return self.Ae_cm2 * 1e-4

    def loss_density_mW_cm3(self, f, Bpk_gauss):
        return (
            f / (
                self.loss_a / Bpk_gauss**3
                + self.loss_b / Bpk_gauss**2.3
                + self.loss_c / Bpk_gauss**1.65
            )
            + self.loss_d * Bpk_gauss**2 * f**2
        )

    def Bpk_T_to_gauss(self, Bpk_T):
        return Bpk_T * 1e4

    def loss_W(self, f, Bpk_T):
        Bpk_gauss = self.Bpk_T_to_gauss(Bpk_T)
        loss_mW = self.loss_density_mW_cm3(f, Bpk_gauss) * self.Ve_cm3
        return loss_mW * 1e-3

    def B_pk_T(self, L, I_pk, N):
        # Flux Linkage = Flux Linkage
        # N * Flux     = L * I
        # Flux         = L * I / N
        # B * Ae       = L * I / N
        # B            = L * I / (N * Ae)
        return L * I_pk / (N * self.Ae_m2())

    def R_core(self, f, L, N, I_pk):
        w = 2 * np.pi * f
        V_pk_core = w * L * I_pk
        V_rms_core = V_pk_core / np.sqrt(2)
        Bpk_T = self.B_pk_T(L, I_pk, N)
        return V_rms_core**2 / self.loss_W(f, Bpk_T)

def plot(core, L, N, Rs_at_678, C_self=None):
    f = np.logspace(4, 10, 5000)
    w = 2 * np.pi * f

    if True:
        V_rms_VNA = 2 * np.sqrt(VNA_Z0 * VNA_POWER_W)
        V_pk_VNA = V_rms_VNA * np.sqrt(2)
        # Assumes low R_winding and high R_core...
        I_pk = np.abs(V_pk_VNA / (VNA_Z0 + (1j * w * L)))
        print(V_rms_VNA, V_pk_VNA, I_pk)
    else:
        I_pk = 180e-6
        # I_pk = 20000e-6

    # Model: Figure 9.2a from "High-Frequency Magnetic Components" (2nd Ed.) - Kazimierczuk
    R_core = core.R_core(f, L, N, I_pk)
    # print(I_pk, core.R_core(6.78e6, L, N, I_pk))
    R_winding = 1e-3 + Rs_at_678 * np.sqrt(f / 6.78e6) # HACK!
    Z = R_winding + zz.parallel(zL(w, L), R_core)
    if C_self is not None:
        Z = zz.parallel(Z, zC(w, C_self))

    fig, ax = plt.subplots(4, 1, figsize=(16, 10), sharex=True, constrained_layout=True)

    # Magnitude
    ax[0].loglog(f, np.abs(Z), label="|Z|", linewidth=2)
    ax[0].loglog(f, R_winding, "--", label="$ R_{winding} $")
    ax[0].loglog(f, R_core, "--", label="$ R_{core} $")
    ax[0].loglog(f, np.abs(zL(w, L)), "--", label=r"$|j \omega L|$")
    if C_self is not None:
        ax[0].loglog(f, np.abs(zC(w, C_self)), "--", label=r"$ | \frac{1}{j \omega C_{self}} | $")
    ax[0].axvline(F0, linestyle="--", color="black")
    ax[0].set_ylabel("Impedance [Ω]")
    ax[0].set_ylim(np.min(R_winding) / 5, np.max(R_core) * 5)
    ax[0].set_xlim(1e4, 1e10)
    ax[0].grid(True, which="both")
    ax[0].legend()

    # Phase
    ax[1].semilogx(f, np.angle(Z, deg=True), linewidth=2, label="∡Z")
    ax[1].axhline(0, linestyle="-", color="black")
    ax[1].axvline(F0, linestyle="--", color="black")
    ax[1].set_ylabel("Phase [deg]")
    ax[1].grid(True, which="both")
    ax[1].legend()

    # Real
    ax[2].loglog(f, np.real(Z), linewidth=2, label="Re[Z]")
    ax[2].loglog(f, R_winding, "--", label="$ R_{winding} $")
    ax[2].loglog(f, R_core, "--", label="$ R_{core} $")
    ax[2].axvline(F0, linestyle="--", color="black")
    ax[2].set_ylabel("Real [Ω]")
    ax[2].grid(True, which="both")
    ax[2].legend()

    # Imag
    ax[3].semilogx(f, np.imag(Z) / (2 * np.pi * f) * 1e9, linewidth=2)
    ax[3].axhline(L * 1e9, linestyle="--", label="L [nH]", color="tab:red")
    if C_self is not None:
        ax[3].axhline(-1 * C_self * 1e9, linestyle="--", label="$ C_{self} $ [nF]", color="tab:purple")
    ax[3].set_yscale("symlog", linthresh=1e-3)
    ax[3].axhline(0, linestyle="-", color="black")
    ax[3].axvline(F0, linestyle="--", label="6.78 MHz", color="black")
    ax[3].set_xlabel("Frequency [Hz]")
    ax[3].set_ylabel("Imag [nH else nF]")
    ax[3].grid(True, which="both")
    ax[3].legend()

    freq_fmt = plt_ticker.EngFormatter(unit="Hz", places=2)
    ax[0].format_coord = lambda x, y: f"f={freq_fmt(x)}, |Z|={y:.3g} Ω"
    ax[1].format_coord = lambda x, y: f"f={freq_fmt(x)}, phase={y:.2f}°"
    ax[2].format_coord = lambda x, y: f"f={freq_fmt(x)}, Re[Z]={y:.4f} Ω"
    ax[3].format_coord = lambda x, y: f"f={freq_fmt(x)}, Im[Z]={y:.2f} nH/nF"

    plt.show()

# T106_17 = Core(Ae_cm2=0.461, Ve_cm3=3, loss_a=4e9, loss_b=3e8, loss_c=2.7e6, loss_d=4.4e-16)
# https://www.rf-microwave.com/resources/products_attachments/612cd11f34199.pdf
T106_17 = Core(Ae_cm2=0.659, Ve_cm3=4.28, loss_a=4e9, loss_b=3e8, loss_c=2.7e6, loss_d=4.4e-16)

def main():
    L = 185e-9
    N = 5
    Rs_at_678 = 25e-3
    C_self = 4.1e-12
    plot(core=T106_17, L=L, N=N, Rs_at_678=Rs_at_678, C_self=C_self)

if __name__ == '__main__':
    main()
