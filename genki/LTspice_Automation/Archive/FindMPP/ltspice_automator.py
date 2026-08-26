import collections
import coloredlogs
import dataclasses
import enum
import logging
import math
import numpy
import os
import pathlib
import spicelib

import spicelib.simulators.ltspice_simulator

BASE_DIR = pathlib.Path(__file__).parent

VPA_MAX = 200 + 2 # Firmware maximum (indirect way of avoiding VDS_MAX)
IPA_MAX = 10 # Empirical maximum (above this value, DC-DC heats too much)
VDS_MAX = 650 # Datasheet maximum of GaNFET
VRECT_MAX = 85 + 2 # Firmware maximum (indirect way of avoiding datasheet maximum of rectification diodes)
PBATT_MAX = 1000 + 20 # Firmware maximum (1KW system)
IRECT_MAX = PBATT_MAX / 71 # Empirical maximum (at 1000W, VRect must be >= 71V to avoid rectifier over-heating)
VBATT_MAX = 54 # Bizdev maximum (16s LiFePO4)

RLOADDC_LIST = range(1, 31)

IGNORE_MPP_NEIGHBOR_CHECKS = False # You want this set to False unless you're debugging

log_fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(logger=log, fmt=log_fmt, level=logging.INFO, reconfigure=False)

@dataclasses.dataclass
class OperatingPoint:
    is_valid: bool
    RLoadDC: float
    VPA: float
    IPA: float
    VDSMax: float
    VSMag: float
    VRect: float
    IRect: float
    PBatt: float
    ITCLoopRMS: float
    ITCTankRMS: float
    IRCRMS: float
    # PTCLoopLoss: float
    # PTCTankLoss: float
    # PRCLoss: float

    def __str__(self):
        ret = '' if self.is_valid else '(INVALID) '
        for attr in dataclasses.fields(self):
            if attr.name == 'is_valid':
                continue
            ret += f"{attr.name}={getattr(self, attr.name):0.1f}, "
        ret += f"Eff={(100 * self.PBatt / (self.VPA * self.IPA)):0.1f}%, Adm={(self.IPA/self.VPA * 1e6):0.0f}"
        return ret

class SimType(enum.Enum):
    DCLoad = 1 # Full bridge rectifier diodes
    ACLoad = 2 # First-harmonic approximation for full bridge diodes

def numpy_ac_mag(arr):
    return 0.5 * (numpy.max(arr) - numpy.min(arr))

# THIS IS WRONG. Variable timesteps requires calculating differently. See below.
def numpy_rms(arr):
    return numpy.sqrt(numpy.mean(arr**2))

@dataclasses.dataclass
class Config:
    name: str
    L_TC_Loop: float
    L_TC_Tank: float
    L_RC: float
    f_TC_Loop: float
    f_TC_Tank: float
    f_RC: float
    R_TC_Loop: float
    R_TC_Tank: float
    R_RC: float
    k12: float
    k13: float
    k23: float
    L_TC_Coax: float
    R_TC_Coax: float
    L_RC_Coax: float
    R_RC_Coax: float

    def __post_init__(self):
        self.op_cache = {}

    def run_sim(self, rload_dc, vpa, sim_type=SimType.ACLoad):
        file_base = f"1KW_System_{sim_type.name}"
        pathlib.Path(f"{BASE_DIR}/tmp").mkdir(exist_ok=True)
        with open(f"{BASE_DIR}/tmp/LTspiceAutomator.inc", 'w') as f:
            for attr in dataclasses.fields(self):
                if attr.name == 'name':
                    continue
                f.write(f".param {attr.name}={getattr(self, attr.name)}\n")
            f.write(f".param V_pa={vpa}\n")
            f.write(f".param R_loadDC={rload_dc}\n")
        ltspice = spicelib.SimRunner(output_folder=f"{BASE_DIR}/tmp", simulator=spicelib.simulators.ltspice_simulator.LTspice)
        netlist = spicelib.AscEditor(f"{BASE_DIR}/{file_base}.asc")
        ltspice.run(netlist)
        files = list(iter(ltspice))
        assert(len(files) == 1)
        raw_file, log_file = files[0]
        return raw_file, log_file

    def read_raw(self, raw_file, rload_dc, vpa, sim_type=SimType.ACLoad):
        traces_to_read = ['I(L1)', 'V(vds)', 'V(vs)', 'I(Ltcloop)', 'I(Ltctank)', 'I(Lrc)']
        if sim_type is SimType.DCLoad:
            raw = spicelib.RawRead(raw_file, traces_to_read=traces_to_read+['V(vrect)', 'I(Oc_rloaddc)'])
        else:
            assert(sim_type is SimType.ACLoad)
            raw = spicelib.RawRead(raw_file, traces_to_read=traces_to_read+['I(Oc_rloadac)'])
        assert(len(raw.get_steps()) == 1)
        ipa = numpy.mean(raw.get_trace('I(L1)').get_wave(0))
        vds_max = numpy.max(raw.get_trace('V(VDS)').get_wave(0))
        vs_mag = numpy_ac_mag(raw.get_trace('V(VS)').get_wave(0))
        itclooprms = numpy_rms(raw.get_trace('I(LTCLoop)').get_wave(0))
        itctankrms = numpy_rms(raw.get_trace('I(LTCTank)').get_wave(0))
        ircrms = numpy_rms(raw.get_trace('I(LRC)').get_wave(0))
        # ptclooploss = numpy_rms(raw.get_trace('I(LTCLoop)').get_wave(0))**2 * self.R_TC_Loop
        # ptctankloss = numpy_rms(raw.get_trace('I(LTCTank)').get_wave(0))**2 * self.R_TC_Tank
        # prcloss = numpy_rms(raw.get_trace('I(LRC)').get_wave(0))**2 * self.R_RC
        if sim_type is SimType.DCLoad:
            vrect_waveform = raw.get_trace('V(VRect)').get_wave(0)
            pbatt = numpy.mean(raw.get_trace('I(OC_RLoadDC)').get_wave(0) * vrect_waveform)
            vrect = numpy.mean(vrect_waveform)
        else:
            assert(sim_type is SimType.ACLoad)
            rload_ac = rload_dc * 8 / (numpy.pi ** 2)
            iload_rms = numpy_ac_mag(raw.get_trace('I(OC_RLoadAC)').get_wave(0)) / numpy.sqrt(2)
            pbatt = (iload_rms**2) * rload_ac
            vrect = numpy.sqrt(pbatt * rload_dc)
        irect = pbatt / vrect
        return OperatingPoint(
            is_valid=True,
            RLoadDC=rload_dc, VPA=vpa, IPA=ipa, VDSMax=vds_max, VSMag=vs_mag, VRect=vrect, IRect=irect, PBatt=pbatt,
            ITCLoopRMS=itclooprms, ITCTankRMS=itctankrms, IRCRMS=ircrms)
            # PBatt=pbatt, PTCLoopLoss=ptclooploss, PTCTankLoss=ptctankloss, PRCLoss=prcloss)

    def get_op(self, rload_dc, sim_type=SimType.ACLoad):
        if (rload_dc, sim_type) in self.op_cache:
            # Memoize. Don't use @functools.cache and friends until understand issues around memo'izing class methods.
            return self.op_cache[(rload_dc, sim_type)]
        vpa = VPA_MAX
        log.debug(f"  Analyzing RLoadDC={rload_dc:0.1f} (SimType={sim_type.name})...")
        while True:
            log.debug(f"    Simulating with VPA={vpa:0.1f}...")
            raw_file, log_file = self.run_sim(rload_dc, vpa, sim_type=sim_type)
            log.debug("      Analyzing raw file...")
            op = self.read_raw(raw_file, rload_dc, vpa, sim_type=sim_type)
            log.debug(f"      Result: {op}")
            factor = max(op.IPA / IPA_MAX, op.VDSMax / VDS_MAX, op.VRect / VRECT_MAX, op.IRect / IRECT_MAX, numpy.sqrt(op.PBatt / PBATT_MAX))
            if factor >= 1.01:
                vpa /= factor
            else:
                break
        rload_dc_min = VBATT_MAX**2 / op.PBatt
        if rload_dc < rload_dc_min:
            # To charge *all* possible batteries at op.PBatt, there is a minimum R_eq of the battery.
            # If our proposed R_eq is lower than that minimum, we need to increase PBatt to rise above it.
            # However by this point in the function, we've already found that this is the maximum PBatt we can hit.
            # Thus, we simply cannot charge at this RLoadDC.
            log.debug(f"    RLoadDC={rload_dc:0.1f} is larger than rload_dc_min={rload_dc_min:0.1f}, marking as invalid...")
            op.is_valid = False
        self.op_cache[(rload_dc, sim_type)] = op
        return op

    def _get_mpp(self, rload_dc_list):
        if len(rload_dc_list) == 1:
            return self.get_op(rload_dc_list[0])
        midpoint = math.ceil(len(rload_dc_list)/2) - 1
        log.debug(f"Considering RLoads: {list(rload_dc_list)}")
        op1 = self.get_op(rload_dc_list[midpoint])
        # THEORY (not proven yet): If an RLoadDC is invalid, then all RLoadDC below it will be invalid.
        if not op1.is_valid:
            return self._get_mpp(rload_dc_list[midpoint+1:])
        op2 = self.get_op(rload_dc_list[midpoint+1])
        if not op2.is_valid:
            raise Exception(f"ERROR: above theory is invalid (see comment in code)")
        if op2.PBatt > op1.PBatt:
            return self._get_mpp(rload_dc_list[midpoint+1:])
        else:
            return self._get_mpp(rload_dc_list[:midpoint+1])

    def is_bad_config(self, rload_dc_list):
        op_end = self.get_op(rload_dc_list[-1])
        op_neighbor = self.get_op(rload_dc_list[-2])
        assert(op_end.is_valid)
        assert(op_neighbor.is_valid)
        return op_end.PBatt > op_neighbor.PBatt

    def get_mpp(self, rload_dc_list):
        log.debug(f"Finding MPP of config: {self.name}")
        log.debug(f"First checking if the given RLOADDC_LIST is sufficient...")
        if self.is_bad_config(rload_dc_list):
            log.warning(f"MPP is at upper bound of RLOADDC_LIST!")
            return self.get_op(rload_dc_list[-1], sim_type=SimType.DCLoad)
        mpp = self._get_mpp(rload_dc_list)
        log.debug(f"Confirming potential MPP ({mpp})")
        if IGNORE_MPP_NEIGHBOR_CHECKS:
            log.warning(f"Ignoring neighbor checks on MPP!")
        else:
            if mpp.RLoadDC in [rload_dc_list[0], rload_dc_list[-1]]:
                raise Exception(f"ERROR: MPP at RLoadDC={mpp.RLoadDC:0.1f} is at bounds of RLOADDC_LIST. Expand RLOADS span and re-run.")
            idx = rload_dc_list.index(mpp.RLoadDC)
            neighbors = [self.get_op(rload_dc_list[neighbor_idx]) for neighbor_idx in [idx - 1, idx + 1]]
            if any(mpp.PBatt < neighbor.PBatt for neighbor in neighbors):
                raise Exception("ERROR: \"MPP\" has less power than one of its neighbors. There must be a bug in the code.")
        log.debug(f"MPP confirmed. Checking with DCLoad sim to ensure accurate results...")
        mpp = self.get_op(mpp.RLoadDC, sim_type=SimType.DCLoad)
        log.debug(f"Final MPP: {mpp}")
        return mpp

    def one_off(self, vpa, rload_dc):
        sim_type = SimType.DCLoad
        raw_file, log_file = self.run_sim(rload_dc, vpa, sim_type=sim_type)
        return self.read_raw(raw_file, rload_dc, vpa, sim_type=sim_type)

CFGS = [
    # Config(
    #     name="OG HP Coils w/ RG8X TC cable, d23=10mm, Ansys coeffs",
    #     L_TC_Loop=308e-9, L_TC_Tank=6.618e-6, L_RC=1.574e-6,
    #     f_TC_Loop=6.83e6, f_TC_Tank=6.73e6, f_RC=6.86e6,
    #     R_TC_Loop=0.196, R_TC_Tank=1.907, R_RC=0.545,
    #     k12=0.358, k13=0.314, k23=0.263,
    #     L_TC_Coax=139e-9, R_TC_Coax=220e-3,
    #     L_RC_Coax=139e-9, R_RC_Coax=220e-3,
    # ),
    Config(
        name="OG HP Coils w/ RG8X TC cable, d23=20mm, Ansys coeffs",
        L_TC_Loop=308e-9, L_TC_Tank=6.619e-6, L_RC=1.571e-6,
        f_TC_Loop=6.83e6, f_TC_Tank=6.73e6, f_RC=6.86e6,
        R_TC_Loop=0.190, R_TC_Tank=1.836, R_RC=0.512,
        k12=0.359, k13=0.226, k23=0.229,
        L_TC_Coax=139e-9, R_TC_Coax=220e-3,
        L_RC_Coax=139e-9, R_RC_Coax=220e-3,
    ),
    # Config(
    #     name="OG HP Coils w/ RG8X TC cable, d23=60mm, Ansys coeffs",
    #     L_TC_Loop=308e-9, L_TC_Tank=6.631e-6, L_RC=1.558e-6,
    #     f_TC_Loop=6.83e6, f_TC_Tank=6.73e6, f_RC=6.86e6,
    #     R_TC_Loop=0.187, R_TC_Tank=1.834, R_RC=0.519,
    #     k12=0.360, k13=0.0665, k23=0.104,
    #     L_TC_Coax=139e-9, R_TC_Coax=220e-3,
    #     L_RC_Coax=139e-9, R_RC_Coax=220e-3,
    # ),
]

def main():
    raise Exception("Do not use this code, see the \"THIS IS WRONG\" comment above")
    for cfg in CFGS:
        log.info(f"Cfg: {cfg.name}")
        mpp = cfg.get_mpp(RLOADDC_LIST)
        log.info(f"MPP: {mpp}")
        log.info("")

if __name__ == '__main__':
    main()