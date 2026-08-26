# /// script
# dependencies = [
#     "coloredlogs",
#     "matplotlib",
#     "numpy",
#     "pandas",
#     "quantiphy",
#     "scipy",
#     "spicelib",
# ]
# ///

import dataclasses
import logging
import math
import os
import pathlib

import coloredlogs
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import quantiphy
import scipy.interpolate
import scipy.signal
import spicelib
import spicelib.simulators.ltspice_simulator

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(logger=log, fmt="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO, reconfigure=False)

BASE_DIR = pathlib.Path(__file__).parent
OUTPUT_FOLDER = f"{BASE_DIR}/tmp"
FILE_BASE = f"1KW_System"

F0 = 6.78e6
HARMONICS = range(5, math.floor(300e6 / F0))

COL_AMPLITUDE = "Amplitude (dB)"
COL_FREQUENCY = "Frequency (Hz)"
COL_HARMONIC = "Harmonic"

def np_max_diff(x, y):
    return np.max(np.abs(x - y))

def np_vartime_avg(t, y):
    dt = np.diff(t)
    y_avg = (y[:-1] + y[1:]) / 2 # Trapezoidal rule
    integral = np.sum(y_avg * dt)
    duration = t[-1] - t[0]
    return integral / duration

def np_vartime_rms(t, y):
    dt = np.diff(t)
    integral = np.sum(y[:-1]**2 * dt)
    duration = t[-1] - t[0]
    return np.sqrt(integral / duration)

def resonant_c(f, l):
    w = 2 * np.pi * f
    return 1 / (w**2 * l)

def last_ten_percent(arr):
    return arr[int(len(arr) * 0.9):]

def vartime_fft(t, y):
    t_uniform = np.linspace(t[0], t[-1], len(t))
    interp_func = scipy.interpolate.interp1d(t, y, kind='linear', fill_value='extrapolate')
    y_uniform = interp_func(t_uniform)
    window = scipy.signal.windows.hann(len(y_uniform))
    y_uniform_windowed = y_uniform * window
    N = len(y_uniform_windowed)
    dt = t_uniform[1] - t_uniform[0]
    mags = np.abs(np.fft.rfft(y_uniform_windowed)) / (N / 2)
    mags[0] /= 2
    magnitude_dB = 20 * np.log10(mags + 1e-20)
    return np.fft.rfftfreq(N, d=dt), magnitude_dB

def fft_plot(freqs, mags_dB):
    plt.figure(figsize=(10, 6))
    mask = (freqs >= 1e6) & (freqs <= 200e6)
    plt.plot(freqs[mask] / 1e6, mags_dB[mask])
    plt.xscale('log')
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Amplitude (dB re: 1 A)")
    plt.grid(True)
    plt.tight_layout()
    plt.show()

def make_typed_array(data):
    """Make a numpy array equivalent to a read_csv() result"""
    return np.array(list(data.items()), dtype=[(COL_HARMONIC, 'int64'), (COL_AMPLITUDE, 'float64')])

def fft_analysis(t, trace):
    freqs, mags_dB = vartime_fft(t, trace)
    data = {}
    for n in HARMONICS:
        target_freq = n * F0
        data[n] = mags_dB[np.argmin(np.abs(freqs - target_freq))]
    return make_typed_array(data)

@dataclasses.dataclass
class CoilSet:
    name: str
    C_TCLoop_: float
    C_TCTank_: float
    C_RC_: float
    L_TCLoop_: float
    L_TCTank_: float
    L_RC_: float
    R_TCLoop_: float
    R_TCTank_: float
    R_RC_: float
    k12_: float
    k13_: dict
    k23_: dict
    L_TCCoax_: float
    R_TCCoax_: float
    L_RCCoax_: float
    R_RCCoax_: float

@dataclasses.dataclass
class OpConfig:
    coilset: CoilSet
    distance_mm: int
    VPA_: float
    R_OCRLoadDC_: float

    def spice_params(self):
        for field in dataclasses.fields(self.coilset):
            if field.name == 'name':
                continue
            if field.name in ['k13_', 'k23_']:
                yield f".param {field.name}={getattr(self.coilset, field.name)[self.distance_mm]}\n"
            else:
                yield f".param {field.name}={getattr(self.coilset, field.name)}\n"
        yield f".param VPA_={self.VPA_}\n"
        yield f".param R_OCRLoadDC_={self.R_OCRLoadDC_}\n"

    def csv_header(self):
        return "CoilSet,Distance (mm),VPA_,R_OCRLoadDC_"

    def csv_values(self):
        return f"{self.coilset.name},{self.distance_mm},{self.VPA_},{self.R_OCRLoadDC_}"

@dataclasses.dataclass
class Tweak:
    Ton_: float = 55.75e-9
    L_Choke_: float = 47e-6

    # Source: Series(Avg(2, 0.23), Parallel(1.5, 1.5))
    Rgd_: float = 1.85

    # Source: "EE-377 Measure-TR-PA-board-parasitics"
    L_Parasitic_: float = 50e-9

    # Source: "EE-324 Reduce/remove L2 in TR1K PA"
    C16etc_: float = 130e-12
    C_Insert_: float = 2043e-12
    L2_: float = 510e-9

    def spice_params(self):
        for field in dataclasses.fields(self):
            val = getattr(self, field.name)
            if type(val) is str:
                # Handled specially by editing SPICE netlist directly.
                continue
            yield f".param {field.name}={val}\n"

    @staticmethod
    def csv_field_name(field_name):
        if field_name.startswith('L'):
            return f"{field_name} (nH)"
        elif field_name.startswith('C'):
            return f"{field_name} (pF)"
        elif field_name == 'Ton_':
            return f"{field_name} (ns)"
        else:
            return field_name

    def csv_header(self):
        return ','.join(self.csv_field_name(field.name) for field in dataclasses.fields(self))

    def csv_value(self, field_name):
        val = getattr(self, field_name)
        if type(val) is str:
            return val
        if field_name == 'Ton_':
            return f"{val * 1e9:0.2f}"
        elif field_name.startswith('L'):
            val *= 1e9
        elif field_name.startswith('C'):
            val *= 1e12
        return f"{val:0.1f}"

    def csv_values(self):
        return ','.join(self.csv_value(field.name) for field in dataclasses.fields(self))

@dataclasses.dataclass
class Result:
    t: np.array
    VDS: np.array
    VPA_Avg: float
    IPA_Avg: float
    Admittance_Avg: float
    VDS_PeakPeak: float
    PDiss_GaNFET_Avg: float
    V_PACap_Max : float
    I_PATank_RMS: float
    I_TCLoop_RMS: float
    I_TCTank_RMS: float
    V_TCTankCap_Max: float
    I_RC_RMS: float
    I_MatchL_RMS: float
    IRectDiode_RMS: float
    VRect_Avg: float
    PRect_Avg: float
    Eff_Avg: float
    Harmonics: np.recarray

    def csv_header(self):
        ret = ','.join(field.name for field in dataclasses.fields(self) if field.name not in ['t', 'VDS', 'Harmonics'])
        return ret

    def csv_values(self):
        ret = ','.join(f"{getattr(self, field.name):0.1f}" for field in dataclasses.fields(self) if field.name not in ['t', 'VDS', 'Harmonics'])
        return ret

class Sim:
    def __init__(self, opcfg, tweak):
        self.opcfg = opcfg
        self.tweak = tweak

    def run_sim(self):
        pathlib.Path(OUTPUT_FOLDER).mkdir(exist_ok=True)
        with open(f"{BASE_DIR}/{FILE_BASE}.inc", 'w') as f:
            for param in self.opcfg.spice_params():
                f.write(param)
            for param in self.tweak.spice_params():
                f.write(param)
        ltspice = spicelib.SimRunner(output_folder=OUTPUT_FOLDER, simulator=spicelib.simulators.ltspice_simulator.LTspice)

        netlist = spicelib.simulators.ltspice_simulator.LTspice.create_netlist(f"{BASE_DIR}/{FILE_BASE}.asc")
        netlist = spicelib.SpiceEditor(netlist)

        for key in dataclasses.fields(self.tweak):
            key = key.name
            val = getattr(self.tweak, key)
            if val == 'Open':
                netlist.remove_component(key[:-1])
            if val == 'Short':
                netlist.remove_component(key[:-1])
                netlist.set_component_value(f'R_Bypass{key[:-1]}', '1p')

        ltspice.run(netlist)
        files = list(iter(ltspice))
        if len(files) != 1:
            with open(f"{OUTPUT_FOLDER}/{FILE_BASE}_1.fail", 'r') as file:
                print(file.read())
            raise Exception("Failed Simulation")
        raw_file, log_file = files[0]
        return raw_file, log_file

    @staticmethod
    def get_trace(raw, key):
        return raw.get_trace(key).get_wave(0)

    def _analyze_di_dt(self, t, raw):
        t_end = last_ten_percent(t)
        i_end = last_ten_percent(self.get_trace(raw, 'Ix(xu1:drainin)'))
        fig, ax1 = plt.subplots()
        ax2 = ax1.twinx()

        ax1.plot(t_end, i_end)
        di_dt_end = np.gradient(i_end, t_end)
        print(f"di_dt_end max: {quantiphy.Quantity(np.max(di_dt_end))}")
        print(f"di_dt_end min: {quantiphy.Quantity(np.min(di_dt_end))}")
        ax2.plot([], [])
        ax2.plot(t_end, di_dt_end)

        ax1.set_title(f"VPA: {self.opcfg.VPA_}")
        ax1.set_xlabel("Time (s)")
        ax1.set_ylabel("i")
        ax2.set_ylabel("di_dt")

        plt.show(block=False)
        plt.pause(0.1)

    def read_raw(self, raw_file):
        raw = spicelib.RawRead(raw_file, traces_to_read='*')
        assert(len(raw.get_steps()) == 1)
        t = raw.get_time_axis()

        # self._analyze_di_dt(t, raw)

        # See raw.get_trace_names() to see what names to use.
        return Result(
            t=t,
            VDS=self.get_trace(raw, 'V(v_ds)'),
            VPA_Avg=np_vartime_avg(t, self.get_trace(raw, 'V(vpa)')),
            IPA_Avg=np_vartime_avg(t, self.get_trace(raw, 'I(L_Choke)')),
            Admittance_Avg=np_vartime_avg(t, self.get_trace(raw, 'I(L_Choke)') / self.get_trace(raw, 'V(vpa)') * 1e6),
            VDS_PeakPeak=np.ptp(self.get_trace(raw, 'V(v_ds)')),
            PDiss_GaNFET_Avg=np_vartime_avg(t, self.get_trace(raw, 'V(v_ds)') * self.get_trace(raw, 'Ix(xu1:drainin)')),
            V_PACap_Max=np_max_diff(self.get_trace(raw, 'V(v_ds)'), self.get_trace(raw, 'V(v_patank)')),
            I_PATank_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L2)')),
            I_TCLoop_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_TCLoop)')),
            I_TCTank_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_TCTank)')),
            V_TCTankCap_Max=np_max_diff(self.get_trace(raw, 'V(c_tctank_a)'), self.get_trace(raw, 'V(c_tctank_b)')),
            I_RC_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_RC)')),
            I_MatchL_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_OCL4)')),
            IRectDiode_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(D_OCD31)')),
            VRect_Avg=np_vartime_avg(t, self.get_trace(raw, 'V(oc_vrect)')),
            PRect_Avg=np_vartime_avg(t, self.get_trace(raw, 'V(oc_vrect)') * self.get_trace(raw, 'I(R_OCRLoadDC)')),
            Eff_Avg=np_vartime_avg(t, 100 * self.get_trace(raw, 'V(oc_vrect)') * self.get_trace(raw, 'I(R_OCRLoadDC)') / (self.get_trace(raw, 'V(vpa)') * self.get_trace(raw, 'I(L_Choke)'))),
            Harmonics=fft_analysis(t, self.get_trace(raw, 'V(v_ds)')),
        )

    def run(self):
        raw_file, _ = self.run_sim()
        return self.read_raw(raw_file)

GEN2_COILS = CoilSet(
    name="Gen2 (FEMM)",
    # Source: Arena
    C_TCLoop_=2310e-12, C_TCTank_=101e-12, C_RC_=318e-12,

    # Source: "EE-354 Use-VNA-to-measure-L-M-for-coils" Gen 2 coils
    L_TCLoop_=219e-9, L_TCTank_=5.573e-6, L_RC_=1.671e-6,
    R_TCLoop_=0.028, R_TCTank_=1.85, R_RC_=0.389,
    k12_=0.672,
    k13_={10: 0.4182, 15: 0.3549, 20: 0.3192, 25: 0.2783, 30: 0.2443, 35: 0.2152, 40: 0.1882, 45: 0.1713, 50: 0.1487},
    k23_={10: 0.5431, 15: 0.4777, 20: 0.4200, 25: 0.3708, 30: 0.3233, 35: 0.2886, 40: 0.2552, 45: 0.2270, 50: 0.2031},

    # Source: "Coil and Cable Measurements" Google Sheet
    L_TCCoax_=160e-9, R_TCCoax_=117e-3,
    L_RCCoax_=139e-9, R_RCCoax_=220e-3,
)

class Runner:
    def __init__(self):
        self.run_index = 0

    def plot_vds(self, result):
        plt.figure()
        t, VDS = last_ten_percent(result.t), last_ten_percent(result.VDS)
        plt.plot(t, VDS)
        plt.title(f'$V_{{DS}}$ (Run Index: {self.run_index})')
        plt.xlabel('Time (s)')
        plt.ylabel('$V_{DS}$')
        plt.show(block=False)
        plt.pause(0.1)

    def run(self, opcfg, tweak):
        sim = Sim(opcfg, tweak)
        result = sim.run()
        #self.plot_vds(result)
        if self.run_index == 0:
            print(opcfg.csv_header(), end=',')
            print(tweak.csv_header(), end=',')
            print(result.csv_header())
        print(opcfg.csv_values(), end=',')
        print(tweak.csv_values(), end=',')
        print(result.csv_values())

        # harmonics = pd.DataFrame.from_records(result.Harmonics)
        # harmonics.to_csv(f"{tweak.C_Insert_}.csv", index=False)

        self.run_index += 1

def main():
    runner = Runner()

    for opcfg in [
        # Source: "EE-324 Reduce/remove L2 in TR1K PA"
        OpConfig(coilset=GEN2_COILS, distance_mm=20, VPA_=179.37, R_OCRLoadDC_=5.9),
        OpConfig(coilset=GEN2_COILS, distance_mm=10, VPA_=202.24, R_OCRLoadDC_=5.4),
        OpConfig(coilset=GEN2_COILS, distance_mm=10, VPA_=142.57, R_OCRLoadDC_=32.7),
    ]:
        for tweak in [
            Tweak(),
        ]:
            runner.run(opcfg, tweak)
    plt.show()

if __name__ == '__main__':
    main()
