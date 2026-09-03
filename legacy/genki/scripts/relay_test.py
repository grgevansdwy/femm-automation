#!/usr/bin/env python3

# NOTE: Do these before running:
#   sudo ip link set down can0
#   sudo ip link set can0 type can bitrate 500000 restart-ms 100
#   sudo ip link set up can0
#   source ~/vendor_python/bin/activate
#   https://learn.adafruit.com/circuitpython-on-any-computer-with-ft232h/setup
#   https://learn.adafruit.com/circuitpython-on-any-computer-with-ft232h/troubleshooting

import atexit
import board
import digitalio
import sys
import time
from vendor.core import packettools as wpt, packettype
from vendor.can import vendorcan

MAXIMUM_SILENCE_TIME_MS = 100
SWITCH_INTERVAL_MS = 60000 * 3
RAMP_INIT_TIME_MS = 10000
MAX_VBATT_VCHG_DELTA_MV = 200

CAN_DEVICE = "can0"
CAN_NODE_NAME = "com.vendor.relay_test"
CAN_REMOTE_NODE_ID = 18
CAN_NODE_ID = 99

OC_ADCViewRate = 10

class Test:
  def __init__(self):
    self.last_comms_time = None
    self.relay = digitalio.DigitalInOut(board.C0)
    self.last_switch_time_s = 0

  def _error_exit(self, msg):
    print("ERROR:", msg)
    self.relay.value = False
    print(f"Relay --> {self.relay.value}")
    sys.exit()

  def _on_exit(self):
    self.relay.value = False
    self.can_node.close()
    print("Closed Connection")

  async def _vendor_info_callback(self, data):
    global last_comms_time
    self.last_comms_time = time.time()
    VMonCharger = data.values[wpt.AdcID.VMonCharger]
    VMonBatt = data.values[wpt.AdcID.VMonBatt]
    if (self.relay.value is True) and (1000 * (time.time() - self.last_switch_time_s) > RAMP_INIT_TIME_MS):
      if abs(VMonBatt - VMonCharger) > MAX_VBATT_VCHG_DELTA_MV / 1000:
        self._error_exit(f"VMonBatt={VMonBatt}, VMonCharger={VMonCharger}")

  def _write_param(self, param, value):
    write_value, write_status = self.can_node.write_param(param, value)
    if write_status == wpt.ParamStatus.SUCCESS:
      print(f"Parameter {param} successfully set to {write_value}")
    elif write_status == wpt.ParamStatus.FAILURE:
      print("Parameter set failed")
      sys.exit()

  def main(self):
    print("Initializing CAN...")
    try:
      self.can_node = vendorcan.VendorCANNode(vendorcan.get_can_media, CAN_NODE_ID, CAN_REMOTE_NODE_ID, CAN_DEVICE, CAN_NODE_NAME)
    except OSError:
      print("ERROR: Device not found.")
      sys.exit()
    atexit.register(self._on_exit)

    self.relay.direction = digitalio.Direction.OUTPUT
    self.relay.value = False

    print("Connected to", CAN_DEVICE)
    self._write_param(wpt.ParamID.AccessLevel, 15)
    self._write_param(wpt.ParamID.ADCViewRate, OC_ADCViewRate)
    self._write_param(wpt.ParamID.ChargeEnable, 1)
    self.can_node.register_handler(packettype.ADCUpdate, self._vendor_info_callback)

    num_switches = 0
    while True:
      time.sleep(0.1)
      silent_time_ms = 1000 * (time.time() - self.last_comms_time)
      if silent_time_ms > MAXIMUM_SILENCE_TIME_MS:
        self._error_exit(f"OC did not give update for {silent_time_ms} milliseconds!")
      time_elapsed_ms = 1000 * (time.time() - self.last_switch_time_s)
      print(f"Seconds until next switch: {int((SWITCH_INTERVAL_MS - time_elapsed_ms) / 1000)}", end="\r", flush=True)
      if time_elapsed_ms >= SWITCH_INTERVAL_MS:
        self.relay.value = not self.relay.value
        self.last_switch_time_s = time.time()
        num_switches += 1
        print(f"Relay state -> {self.relay.value}. Switches completed: {num_switches}")

if __name__ == "__main__":
  test = Test()
  test.main()
