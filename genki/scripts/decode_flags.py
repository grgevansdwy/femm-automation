#!/usr/bin/env python3

import sys

rx_or_tx = 't' if 't' in sys.argv[1] else 'r'
val = int(sys.argv[2])

# vendor.core.packettools.TxStatusFlags
TX_ENUMS = [
    "POWER_LIMIT_LIMITED",
    "POWER_LOSS_LIMITED",
    "TEMPERATURE_LIMITED",
    "BURST_TEMPERATURE_LIMITED",
    "INPUT_POWER_LIMITED",
]

# vendor.core.packettools.RxStatusFlags
RX_ENUMS = [
    "ALARM",
    "WALL_POWER",
    "CHARGE_ENABLED",
    "THERMAL_LIMITED",
    "CAN_CHARGE",
    "NEW_PARAMS",
    "BATTERY_OVERVOLTAGE",
    "BATTERY_UNDERVOLTAGE",
    "TEMPERATURE_LOW",
    "BATTERY_TEMP_HIGH",
]

for idx, name in enumerate(TX_ENUMS if rx_or_tx == 't' else RX_ENUMS):
    if val & (1 << idx):
        print(name)
