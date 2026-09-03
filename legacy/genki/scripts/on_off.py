# /// script
# dependencies = [
#     "coloredlogs",
#     "requests",
#     "websockets",
# ]
# ///

import logging
import os
import time

import coloredlogs

from vendor.interface import VendorSocket
from vendor.core import packettools as wpt, packettype

log_fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
coloredlogs.install(fmt=log_fmt, level=logging.INFO)
log = logging.getLogger(os.path.basename(__file__))

TIME_ON = 3 * 60 * 60
TIME_OFF = 30

class Test:
  def __init__(self):
    self.vendor = VendorSocket("ws://192.168.2.20/ws")

  def write(self, device, param, value):
    self.vendor.set_parameter(device, param, value).status
    read_back = self.vendor.get_parameter(device, param).value
    log.info(f"Device={device}, param={param}, value={read_back}")

  def main(self):
    self.write(wpt.DeviceID.TX, wpt.ParamID.AccessLevel, 15)
    self.write(wpt.DeviceID.TX, wpt.ParamID.ADCViewRate, 1)
    self.write(wpt.DeviceID.TX, wpt.ParamID.ChargeEnable, 1)

    # We don't want interference with our manual set.
    self.write(wpt.DeviceID.RX_1, wpt.ParamID.MaxChargeTime, TIME_ON * 2)
    self.write(wpt.DeviceID.RX_1, wpt.ParamID.MaxCVChargeTime, TIME_ON * 2)

    num_switches = 0
    while True:
      log.info(f"num_switches={num_switches}: Beginning charge for {TIME_ON} seconds...")
      self.write(wpt.DeviceID.RX_1, wpt.ParamID.ChargeEnable, 1)
      time.sleep(TIME_ON)
      log.info(f"num_switches={num_switches}: Beginning rest for {TIME_OFF} seconds...")
      self.write(wpt.DeviceID.RX_1, wpt.ParamID.ChargeEnable, 0)
      time.sleep(TIME_OFF)
      num_switches += 1

if __name__ == "__main__":
  test = Test()
  test.main()
