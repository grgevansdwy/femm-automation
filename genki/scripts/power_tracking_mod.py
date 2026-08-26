#!/usr/bin/env python3

import atexit
import coloredlogs
import logging
import os
import time

import vendor
import vendor.interface
from vendor.core import packettools as wpt

log_fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
coloredlogs.install(fmt=log_fmt, level=logging.INFO)
log = logging.getLogger(os.path.basename(__file__))

VENDOR_SOCKET = "ws://coilbot:5555/ws"

# NOTE: This code is not organized very nicely but it's because it's optimized
# for copy-pasting to/from coilbot code.

class PowerTrackingMod:
    def __init__(self):
        self.vendor_system.register_handler(vendor.core.packettype.ADCUpdate, self.handle_adc)

    async def handle_adc(self, data):
        if not data.is_core():
            return
        if data.device == wpt.DeviceID.TX:
            self.tx_adc_data = data.values
        elif data.device == wpt.DeviceID.RX_1:
            self.rx_adc_data = data.values

    def power_tracking_mod_reset(self):
        log.info("power_tracking_mod_reset(): ...")
        self.power_tracking_mod_next_action_at = 0

    def power_tracking_mod_check(self):
        if time.time() <= self.power_tracking_mod_next_action_at:
            return
        log.info(f"power_tracking_mod_check(): ...")

    def run(self):
        while True:
            self.power_tracking_mod_check()

if __name__ == "__main__":
    ptm = PowerTrackingMod()
    @atexit.register
    def exit():
        ptm.vendor_system.close()
    time.sleep(5)
    ptm.run()