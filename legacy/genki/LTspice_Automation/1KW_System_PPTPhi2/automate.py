# /// script
# dependencies = [
#     "coloredlogs",
#     "matplotlib",
#     "numpy",
#     "spicelib",
# ]
# ///

import dataclasses
import logging
import os
import pathlib

import coloredlogs
import matplotlib.pyplot as plt
import numpy as np
import spicelib
import spicelib.simulators.ltspice_simulator

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(
    logger=log,
    fmt="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO, reconfigure=False)

BASE_DIR = pathlib.Path(__file__).parent
OUTPUT_FOLDER = f"{BASE_DIR}/tmp"
FILE_BASE = f"PPTPhi2"

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

@dataclasses.dataclass
class Tweak:
    VPA_: float
    R_LoadDC_: float
    k12_: float

    def spice_params(self):
        for field in dataclasses.fields(self):
            yield f".param {field.name}={getattr(self, field.name)}\n"

    @staticmethod
    def csv_field_name(field_name):
        return field_name

    def csv_header(self):
        return ','.join(self.csv_field_name(field.name) for field in dataclasses.fields(self))

    def csv_value(self, field_name):
        return f"{getattr(self, field_name):0.2f}"

    def csv_values(self):
        return ','.join(self.csv_value(field.name) for field in dataclasses.fields(self))

@dataclasses.dataclass
class Result:
    VPA_RMS: float
    IPA_RMS: float
    IPA_Min: float
    VDS_PeakPeak: float
    PDiss_GaNFET_Avg: float
    I_TC_RMS: float
    I_RC_RMS: float
    VRect: float
    POut_Avg: float
    Eff_Avg: float

    def csv_header(self):
        return ','.join(field.name for field in dataclasses.fields(self))

    def csv_values(self):
        return ','.join(f"{getattr(self, field.name):0.1f}" for field in dataclasses.fields(self))

class Sim:
    def __init__(self, tweak):
        self.tweak = tweak

    def run_sim(self):
        pathlib.Path(OUTPUT_FOLDER).mkdir(exist_ok=True)
        with open(f"{BASE_DIR}/{FILE_BASE}.inc", 'w') as f:
            for param in self.tweak.spice_params():
                f.write(param)
        ltspice = spicelib.SimRunner(output_folder=OUTPUT_FOLDER, simulator=spicelib.simulators.ltspice_simulator.LTspice)

        netlist = spicelib.simulators.ltspice_simulator.LTspice.create_netlist(f"{BASE_DIR}/{FILE_BASE}.asc")
        netlist = spicelib.SpiceEditor(netlist)

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

    def read_raw(self, raw_file):
        raw = spicelib.RawRead(raw_file, traces_to_read='*')
        assert(len(raw.get_steps()) == 1)
        t = raw.get_time_axis()
        # See raw.get_trace_names() to see what names to use.
        v_load_ac = self.get_trace(raw, 'V(load+)') - self.get_trace(raw, 'V(load-)')
        p_out_avg = np_vartime_avg(t, v_load_ac * self.get_trace(raw, 'I(R_LoadAC)'))
        ipa = -1 * self.get_trace(raw, 'I(V1)')
        return Result(
            VPA_RMS=np_vartime_rms(t, self.get_trace(raw, 'V(vpa)')),
            IPA_RMS=np_vartime_rms(t, ipa),
            IPA_Min=np.min(ipa),
            VDS_PeakPeak=np.ptp(self.get_trace(raw, 'V(v_ds)')),
            PDiss_GaNFET_Avg=np_vartime_avg(t, self.get_trace(raw, 'V(v_ds)') * self.get_trace(raw, 'Ix(xu1:drainin)')),
            I_TC_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_TC)')),
            I_RC_RMS=np_vartime_rms(t, self.get_trace(raw, 'I(L_RC)')),
            VRect=np.sqrt(p_out_avg * self.tweak.R_LoadDC_),
            POut_Avg=p_out_avg,
            Eff_Avg=100 * p_out_avg / np_vartime_avg(t, self.get_trace(raw, 'V(vpa)') * ipa),
        )

    def run(self):
        raw_file, _ = self.run_sim()
        return self.read_raw(raw_file)

class Runner:
    def __init__(self):
        self.run_index = 0

    def run(self, tweak):
        sim = Sim(tweak)
        result = sim.run()
        if self.run_index == 0:
            print(tweak.csv_header(), end=',')
            print(result.csv_header())
        print(tweak.csv_values(), end=',')
        print(result.csv_values())
        self.run_index += 1

def main():
    runner = Runner()
    k12_ = 0.4
    # for VPA_ in [50]:
    #     for R_LoadDC_ in [10, 25, 50, 100]:
    #         runner.run(Tweak(VPA_=VPA_, R_LoadDC_=R_LoadDC_, k12_=k12_))
    # for VPA_ in [100]:
    #     for R_LoadDC_ in [10, 25, 50, 100]:
    #         runner.run(Tweak(VPA_=VPA_, R_LoadDC_=R_LoadDC_, k12_=k12_))
    # for VPA_ in [150]:
    #     # for R_LoadDC_ in [10, 25, 50, 100]:
    #     for R_LoadDC_ in [150]:
    #         runner.run(Tweak(VPA_=VPA_, R_LoadDC_=R_LoadDC_, k12_=k12_))
    for VPA_ in [200]:
        # for R_LoadDC_ in [100, 125, 150]:
        for R_LoadDC_ in [115, 120, 125]:
            runner.run(Tweak(VPA_=VPA_, R_LoadDC_=R_LoadDC_, k12_=k12_))
    # for VPA_ in [250]:
    #     for R_LoadDC_ in [100, 125, 150]:
    #         runner.run(Tweak(VPA_=VPA_, R_LoadDC_=R_LoadDC_, k12_=k12_))
    plt.show()

if __name__ == '__main__':
    main()