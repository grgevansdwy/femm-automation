import numpy as np
import quantiphy

def qpretty(x):
    return str(x) if type(x) is bool else quantiphy.Quantity(x)

def parallel(*xs):
    return 1 / sum(1/x for x in xs)

def resonantF(L, C):
    w = 1 / np.sqrt(L * C)
    return w / (2 * np.pi)

def reactance_to_l_or_c(w, z, keep_sign=True):
    if z.imag > 0:
        imag = z.imag / w
    elif z.imag < 0:
        if keep_sign:
            imag = 1 / (z.imag * w)
        else:
            imag = 1 / ((-1 * z.imag) * w)
    else:
        imag = 0
    return imag

class Impedance:

    def __init__(self, f, w=None):
        if w is None:
            self.w = 2 * np.pi * f
        else:
            self.w = w

    def zL(self, x):
        return 1j * self.w * x

    def zC(self, x):
        return 1/(1j * self.w * x)

    def z_pretty(self, z):
        real_imag = f"{qpretty(z.real)}Ω + {qpretty(z.imag)}jΩ"
        imag_alt = f"{qpretty(self.reactance_to_l_or_c(z, keep_sign=False))}{'H' if z.imag > 0 else 'F'}"
        mag_phase = f"Mag={np.abs(z):0.2f} ∠={np.angle(z, deg=True):0.1f}°"
        return f"{real_imag} ({imag_alt}) [{mag_phase}]"

    def reactance_to_l_or_c(self, z, keep_sign=True):
        return reactance_to_l_or_c(self.w, z, keep_sign=keep_sign)

    def resonantX(self, CorL):
        return 1 / (self.w**2 * CorL)

class ImpedanceSymbolic:

    def __init__(self, w_symbol):
        self.w = w_symbol

    def zL(self, x):
        return 1j * self.w * x

    def zC(self, x):
        return 1/(1j * self.w * x)
