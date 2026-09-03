# /// script
# dependencies = [
#     "coloredlogs",
#     "requests",
#     "websockets",
# ]
# ///

import sys
import logging
import os
import time

import coloredlogs

from vendor.core import packettools as wpt
from vendor.core import packettype
from vendor.core.packettools import DeviceID, ParamID, AdcID
from vendor.interface import VendorSocket
from vendor.util import connections

assert(len(sys.argv) == 4)
assert(sys.argv[1] in ["--vpa-70", "--vpa-11-no-gd", "--vpa-slow-jumps"])

log_fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
coloredlogs.install(fmt=log_fmt, level=logging.INFO)
log = logging.getLogger(os.path.basename(__file__))

TR_TO_CONNECT_TO = sys.argv[2]
OC_TO_CONNECT_TO = int(sys.argv[3], base=16)

TR_MAX_VPA = 200
OC_MAX_VRECT = 90

TR_DCDC_TEMP_LIMIT = 70
TR_PA_TEMP_LIMIT = 70
OC_RECT_TEMP_LIMIT = 70

TR_SYS_POWER_LIMIT = 1500
TR_PA_POWER_LIMIT = 1300
OC_BATT_POWER_LIMIT = 1050

RUN_TIME_S = 60 * 60 * 24

class Control:

    def __init__(self, w):
        self.w = w
        self.tx_data = None
        self.rx_data = None
        self.error_msg = None

    async def handle_adc(self, packet):
        if not packet.is_core():
            return
        if packet.device == wpt.DeviceID.TX:
            self.tx_data = packet.values
            vpa = self.tx_data[AdcID.VMonPa]
            if vpa > TR_MAX_VPA:
                self.error_msg = f"TR VPA {vpa:.1f}V exceeds {TR_MAX_VPA}V limit"
            dcdc_temp = self.tx_data[AdcID.TMonAmb]
            if dcdc_temp > TR_DCDC_TEMP_LIMIT:
                self.error_msg = f"TR DCDC {dcdc_temp:.1f}C exceeds {TR_DCDC_TEMP_LIMIT}C limit"
            pa_temp = self.tx_data[AdcID.TMonPa]
            if pa_temp > TR_PA_TEMP_LIMIT:
                self.error_msg = f"TR PA {pa_temp:.1f}C exceeds {TR_PA_TEMP_LIMIT}C limit"
            sys_power = self.tx_data[AdcID.VMonSys] * self.tx_data[AdcID.IMonSys]
            if sys_power > TR_SYS_POWER_LIMIT:
                self.error_msg = f"TR SYS POWER {sys_power:.1f}W exceeds {TR_SYS_POWER_LIMIT}W limit"
            tx_power = self.tx_data[AdcID.VMonPa] * self.tx_data[AdcID.IMonPa]
            if tx_power > TR_PA_POWER_LIMIT:
                self.error_msg = f"TR PA POWER {tx_power:.1f}W exceeds {TR_PA_POWER_LIMIT}W limit"
        elif packet.device == wpt.DeviceID.RX_1:
            self.rx_data = packet.values
            vrect = self.rx_data[AdcID.VRect]
            if vrect > OC_MAX_VRECT:
                self.error_msg = f"OC VRECT {vrect:.1f}V exceeds {OC_MAX_VRECT}V limit"
            rect_temp = self.rx_data[AdcID.TBoard]
            if rect_temp > OC_RECT_TEMP_LIMIT:
                self.error_msg = f"OC RECT TEMP {rect_temp:.1f}C exceeds {OC_RECT_TEMP_LIMIT}C limit"
            batt_power = self.rx_data[AdcID.VMonBatt] * self.rx_data[AdcID.IBattery]
            if batt_power > OC_BATT_POWER_LIMIT:
                self.error_msg = f"OC BATT POWER {batt_power:.1f}W exceeds {OC_BATT_POWER_LIMIT}W limit"

    def turn_off(self):
        self.w.set_parameter(DeviceID.TX, ParamID.VPASet, 0)
        self.w.set_parameter(DeviceID.TX, ParamID.TxVicorEnable, 0)
        time.sleep(0.1)
        while self.tx_data[AdcID.VMonPa] > 5:
            self.check_error()
            self.log_state()
            time.sleep(1)
        self.w.set_parameter(DeviceID.TX, ParamID.TxGateDriverEnable, 0)
        while self.tx_data[AdcID.VMonGateDriver] > 5:
            time.sleep(1)
        self.w.set_parameter(DeviceID.TX, ParamID.Fan1Enable, 0)
        self.w.set_parameter(DeviceID.TX, ParamID.Fan2Enable, 0)
        self.w.set_parameter(DeviceID.TX, ParamID.Fan3Enable, 0)
        log.info("TR turned off.")
        self.w.set_parameter(DeviceID.RX_1, ParamID.ChargerCurrentLimit, 0)
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryConnect, 0)
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryChargerEnable, 0)
        self.w.set_parameter(DeviceID.RX_1, ParamID.Fan1Enable, 0)
        log.info("OC turned off.")

    def check_error(self):
        if self.error_msg is not None:
            raise Exception(self.error_msg)

    def log_state(self):
        vpa = self.tx_data[AdcID.VMonPa]
        ipa = self.tx_data[AdcID.IMonPa]
        vrect = self.rx_data[AdcID.VRect]
        pbatt = self.rx_data[AdcID.VMonBatt] * self.rx_data[AdcID.IBattery]
        log.info(f"VPA: {vpa:0.1f}, IPA: {ipa:0.1f}, VRect: {vrect:0.1f}, PBatt: {pbatt:0.1f}")

    def setup(self):
        self.w.set_parameter(DeviceID.TX, ParamID.AccessLevel, 15)
        self.w.set_parameter(DeviceID.TX, ParamID.ManualMode, 1)
        self.w.set_parameter(DeviceID.TX, ParamID.TxPowerEnable, 0)
        self.w.set_parameter(DeviceID.TX, ParamID.VPASet, 0)
        log.info("TR set to manual mode.")

        log.info("Connecting to OC in Distance Connect Mode...")
        connected_to = connections.radio_connect_to_oc(self.w, OC_TO_CONNECT_TO)
        log.info(f"Connected to OC via DCM with MAC {connected_to:06x}.")

        self.w.set_parameter(DeviceID.RX_1, ParamID.AccessLevel, 15)
        self.w.set_parameter(DeviceID.RX_1, ParamID.ManualMode, 1)
        self.w.set_parameter(DeviceID.RX_1, ParamID.ChargerCurrentLimit, 0)
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryConnect, 0)
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryChargerEnable, 0)
        log.info("OC set to manual mode.")

    def turn_on_tr_fans(self):
        self.w.set_parameter(DeviceID.TX, ParamID.Fan1Enable, 255)
        self.w.set_parameter(DeviceID.TX, ParamID.Fan2Enable, 255)
        self.w.set_parameter(DeviceID.TX, ParamID.Fan3Enable, 255)
        log.info("TR fans turned on.")

    def turn_on_oc(self):
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryConnect, 1)
        log.info("OC battery switch turned on.")
        time.sleep(0.1)
        self.w.set_parameter(DeviceID.RX_1, ParamID.RxBatteryChargerEnable, 1)
        log.info("OC charger turned on.")
        time.sleep(0.1)
        self.w.set_parameter(DeviceID.RX_1, ParamID.Fan1Enable, 255)
        log.info("OC fan turned on.")
        time.sleep(0.1)
        self.w.set_parameter(DeviceID.RX_1, ParamID.ChargerCurrentLimit, 1000)
        log.info("OC charger current limit set to 1A.")
        time.sleep(0.1)

    def turn_on_gate_driver(self):
        self.w.set_parameter(DeviceID.TX, ParamID.TxGateDriverEnable, 1)
        log.info("TR gate driver turned on.")
        time.sleep(0.1)

    def set_vpa_target(self, vpa_target):
        self.w.set_parameter(DeviceID.TX, ParamID.VPASet, int(vpa_target * 1000))
        log.info(f"TR VPA target set to {vpa_target:0.1f}V.")
        time.sleep(0.1)

    def set_vpa_target_at_1vperms(self, start_v, end_v):
        t0 = time.perf_counter()
        slew_rate = 1000 # V per s (= 1 V/ms)
        while True:
            dt = time.perf_counter() - t0
            v = min(start_v + slew_rate * dt, end_v)
            self.w.set_parameter(DeviceID.TX, ParamID.VPASet, int(v * 1000))
            if v >= end_v:
                break
        time.sleep(0.0005)

    def enable_dcdc(self):
        self.w.set_parameter(DeviceID.TX, ParamID.TxVicorEnable, 1)
        log.info("TR DC-DC enabled.")
        time.sleep(0.1)

    def ramp_init(self):
        self.turn_on_tr_fans()
        if sys.argv[1] == "--vpa-70":
            self.turn_on_oc()
            self.turn_on_gate_driver()
            self.set_vpa_target(70)
            self.enable_dcdc()
            time.sleep(1)
        elif sys.argv[1] == "--vpa-11-no-gd":
            self.set_vpa_target(11)
            self.enable_dcdc()
            time.sleep(1.5)
            self.set_vpa_target(70)
            self.turn_on_gate_driver()
            self.turn_on_oc()
        elif sys.argv[1] == "--vpa-slow-jumps":
            self.turn_on_oc()
            self.turn_on_gate_driver()
            self.set_vpa_target(30)
            self.enable_dcdc()
            time.sleep(0.5)
            self.set_vpa_target_at_1vperms(30, 55)
            time.sleep(1)
        else:
            assert(False)

    def ramp(self):
        vpa_target = 72
        ibatt_target = 1
        vpa_delta = (173 - vpa_target) / 30
        ibatt_delta = (19 - ibatt_target) / 30 * 1.05
        while True:
            self.check_error()
            self.log_state()
            self.w.set_parameter(DeviceID.TX, ParamID.VPASet, int(vpa_target * 1000))
            time.sleep(0.5)
            self.w.set_parameter(DeviceID.RX_1, ParamID.ChargerCurrentLimit, int(ibatt_target * 1000))
            vpa_target += vpa_delta
            ibatt_target += ibatt_delta
            time.sleep(0.5)
            pbatt = self.rx_data[AdcID.VMonBatt] * self.rx_data[AdcID.IBattery]
            if pbatt >= 995:
                self.log_state()
                log.info(f"Hit 1KW! Running at this state for {RUN_TIME_S} seconds...")
                return

    def steady_state(self):
        self.start_time = time.time()
        while True:
            if (time.time() - self.start_time) > RUN_TIME_S:
                return
            self.check_error()
            time.sleep(1)

    def run(self):
        log.info("Pausing 1 second to wait for first packets...")
        time.sleep(1)
        self.setup()
        while True:
            log.info(f"===================")
            log.info(f"Starting new run...")
            log.info(f"===================")
            self.ramp_init()
            # self.ramp()
            # self.steady_state()
            self.turn_off()
            log.info(f"Done with run!")
            time.sleep(1)

def main():
    websocket_url = f"ws://{TR_TO_CONNECT_TO}/ws"
    with VendorSocket(websocket_url) as w:
        control = Control(w)
        w.register_handler(packettype.ADCUpdate, control.handle_adc)
        try:
            log.info("Press Ctrl+C to stop and turn system off.")
            control.run()
        except KeyboardInterrupt:
            log.info("Interrupted.")
        finally:
            control.turn_off()

if __name__ == "__main__":
    main()
