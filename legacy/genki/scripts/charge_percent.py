#!/usr/bin/env python3

import sys

import coilbot.battery_model.sim

charge_percent = int(sys.argv[1])

chem = 'LiFePO4'
cells = 8

chem = getattr(coilbot.battery_model.sim.Chemistry, chem)()
v_per_cell = chem.linear_interp(charge_percent / 100)
print(v_per_cell * cells)
