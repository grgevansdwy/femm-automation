import femm
import matplotlib.pyplot as plt
import matplotlib.transforms as xfrms
from matplotlib.font_manager import FontProperties
import numpy as np
import random
from quantiphy import Quantity
import coil_configs as cc
import noel_FEMM_functions as nff
import datetime
from pathlib import Path
import itertools as iter
import sys
from typing import NamedTuple
from dataclasses import dataclass, fields

num_turns = 3
peak_current = 45 #peak, not RMS

nff.init_femm()

femm.mi_modifymaterial('Ferrite', 1, 125)
femm.mi_modifymaterial('Ferrite', 2, 125)
femm.mi_modifymaterial('Ferrite', 5, 1e-11) #conductivity
femm.mi_modifymaterial('Ferrite', 10, .012544)
femm.mi_modifymaterial('Ferrite', 11, .012544)
femm.mi_zoom(0, -400, 400, 400)

nff.draw_rectangular_material_no_plot("AluFoil", 2, 75, -10, 1)
nff.draw_rectangular_material_no_plot('Ferrite', 2, 26, -8, 1)
nff.draw_rectangular_material_no_plot('Ferrite', 2, 26, 8, 1)


nff.draw_rectangular_material_no_plot("AluFoil", 2, 75, 10, 1)

for i in range(num_turns):
        femm.mi_addcircprop('ind'+str(i), 0, 0)

nff.draw_coil_traces(16, num_turns, 1, 2, 2, 3, 0, 1.62, 'trace', 'ind', 1, -1.6, 1)  #def draw_coil_traces(central_radius, number_of_turns, number_of_parallel_traces, trace_width, trace_spacing, turn_spacing, top_of_trace, winding_thickness, wire_or_trace, femm_circuit_name, layer_count, pcb_thickness, circuit_group):




for i in range(num_turns):
    femm.mi_modifycircprop('ind'+str(i), 1, peak_current)


femm.mi_saveas('coil.fem')



try:
    femm.mi_analyze()
    femm.mi_loadsolution()
except BaseException as error:
    print('Caught this error: ' + repr(error))
    input("Pausing for debug")

femm.mi_zoom(0, -400, 400, 400)

#def loop_inductance(circuit_name, num_turns_of_induced, driving_current): 
print(nff.loop_inductance('ind', num_turns, peak_current))



input("PAUSE")