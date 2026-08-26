#Noel Hosking

import femm
import matplotlib.pyplot as plt
import matplotlib.transforms as xfrms
from matplotlib.font_manager import FontProperties
import numpy as np
import random
from quantiphy import Quantity
import noel_FEMM_functions as nff
import datetime
from pathlib import Path
import itertools as iter
import sys
from typing import NamedTuple
from dataclasses import dataclass, fields

from noel_FEMM_functions import CoilSet
import ferrite_and_core_info as fci

freq = 100e3 #6.78e6 
omega = 2*np.pi*freq



#####INPUTS v--------------------------------------------------------------------

core = fci.E18_ungapped
core_plate = fci.I18
ferrite = fci.Fair_rite_95  

num_turns = 5
air_gap = 20 #um
L_curr = 1
fillet_height = .1
trace_thickness = .07 #mm 2oz copper
num_parallel_layers = 1
pcb_thickness = 1.6

coupled_inductor = False
coupled_trace_space = 1.6

pcb_y_offset = 1 #mm
inner_trace_clearance = 1 #mm
copper_to_route_clearance = .254 #mm
outer_board_edge_to_ferrite_clearance = 1 #mm

#####INPUTS ^--------------------------------------------------------------------

if (core.slot_depth - air_gap > 0):
    half_center_leg_width = core.center_leg_width/2
else: # core.slot_depth == air_gap
    half_center_leg_width = 0
    half_center_leg_width = core.center_leg_width/2 # this line is fudge
trace_to_trace_clearance = 1


trace_width = (core.interior_length/2-half_center_leg_width-((num_turns-1)*trace_to_trace_clearance)-(copper_to_route_clearance+outer_board_edge_to_ferrite_clearance+inner_trace_clearance))/num_turns #mm








# The package must be initialized with the openfemm command.
femm.openfemm()
    
# # We need to create a new Magnetostatics document to work on.
femm.newdocument(0)

# # Define the problem type.  Magnetostatic; Units of mm; Axisymmetric; 
# # Precision of 10^(-8) for the linear solver; a placeholder of 0 for 
# # the depth dimension, and an angle constraint of 30 degrees


femm.mi_probdef(freq , 'millimeters', 'planar', 1.e-12, core.width, 30)
#femm.mi_addcircprop('L1p', L_curr, 1)
femm.mi_addcircprop('L1m', L_curr, 1)

# #FEMM Materials and Circuits 
femm.mi_getmaterial('Air')
femm.mi_getmaterial('Copper') 

for i in range(num_turns):
    femm.mi_addcircprop('Ltp' + str(i), 0, 0)
    femm.mi_modifycircprop('Ltp'+str(i), 1, L_curr)
    femm.mi_addcircprop('Ltm' + str(i), 0, 0)
    femm.mi_modifycircprop('Ltm'+str(i), 1, -L_curr)

# femm.mi_addmaterial('AluFoil')
# femm.mi_modifymaterial('AluFoil', 1, 1)
# femm.mi_modifymaterial('AluFoil', 2, 1)

fci.add_ferrite_to_sim(ferrite)




fci.draw_planar_e_core(core, ferrite.name, air_gap, 0, 1, fillet_height)
fci.draw_planar_e_core(core, ferrite.name, air_gap, core.height*2, -1, fillet_height)



# fci.draw_planar_plate(core_plate, core_plate, ferrite.name, -core_plate.height) #.2 is good -- 
# fci.draw_planar_plate(core_plate, core_plate, ferrite.name, 3.6) #.2 is good -- 
#fci.draw_planar_plate(core, core_plate, ferrite.name, 0) #.2 is good -- 
layer_coords = [pcb_thickness+pcb_y_offset]
if(num_parallel_layers > 1):
    layer_coords.append(pcb_y_offset+trace_thickness)
if(num_parallel_layers > 2):
    layer_spacing = pcb_thickness/(num_parallel_layers-1)
    for i in range(1, num_parallel_layers-1):
        layer_coords.append(pcb_y_offset+(i*layer_spacing))

for trace_y_offset in layer_coords:
    for j in range(num_turns):
        l_trace_start = -half_center_leg_width - inner_trace_clearance-((j)*(trace_width+trace_to_trace_clearance))
        trace_y = core.height-core.slot_depth+trace_y_offset
        femm.mi_drawrectangle(l_trace_start, trace_y, l_trace_start-trace_width,trace_y+trace_thickness)
        l_trace_x_center = l_trace_start-(trace_width/2)
        trace_y_center = trace_y+(trace_thickness/2)
        femm.mi_addblocklabel(l_trace_x_center, trace_y_center)
        femm.mi_selectlabel(l_trace_x_center, trace_y_center)
        femm.mi_setblockprop('Copper', 1, 1, 'Ltm'+str(j), 0, 1, 1)
        femm.mi_clearselected()

        r_trace_start = half_center_leg_width + inner_trace_clearance+((j)*(trace_width+trace_to_trace_clearance))
        trace_y = core.height-core.slot_depth+trace_y_offset
        femm.mi_drawrectangle(r_trace_start, trace_y, r_trace_start+trace_width,trace_y+trace_thickness)
        r_trace_x_center = r_trace_start+(trace_width/2)
        femm.mi_addblocklabel(r_trace_x_center, trace_y_center)
        femm.mi_selectlabel(r_trace_x_center, trace_y_center)
        femm.mi_setblockprop('Copper', 1, 1, 'Ltp'+str(j), 0, 1, 1)
        femm.mi_clearselected()


# if(coupled_inductor):
#     coupled_trace_y = core.height-core.slot_depth+trace_y_offset+trace_thickness+coupled_trace_space
#     femm.mi_drawrectangle(l_trace_start, coupled_trace_y, l_trace_start-trace_width,coupled_trace_y+trace_thickness)
#     coupled_trace_y_center = coupled_trace_y+(trace_thickness/2)
#     femm.mi_addblocklabel(l_trace_x_center, coupled_trace_y_center)
#     femm.mi_selectlabel(l_trace_x_center, coupled_trace_y_center)
#     femm.mi_setblockprop('Copper', 1, 1, 'L2', 0, 1, 1)
#     femm.mi_clearselected()

#     femm.mi_drawrectangle(r_trace_start, coupled_trace_y, r_trace_start+trace_width,coupled_trace_y+trace_thickness)
#     femm.mi_addblocklabel(r_trace_x_center, coupled_trace_y_center)
#     femm.mi_selectlabel(r_trace_x_center, coupled_trace_y_center)
#     femm.mi_setblockprop('Copper', 1, 1, 'L2', 0, 1, -1)
#     femm.mi_clearselected() 


air_label_y = core.height-core.slot_depth+(trace_y_offset/2)
femm.mi_addblocklabel(l_trace_x_center-trace_width/4, air_label_y)
femm.mi_selectlabel(l_trace_x_center-trace_width/4, air_label_y)
femm.mi_setblockprop('Air', 1, 1, 0)
femm.mi_clearselected()

if(air_gap == 0):
    femm.mi_addblocklabel(r_trace_x_center-trace_width/4, air_label_y)
    femm.mi_selectlabel(r_trace_x_center-trace_width/4, air_label_y)
    femm.mi_setblockprop('Air', 1, 1, 0)
    femm.mi_clearselected()



# #Use a fancy bounding box
femm.mi_makeABC(7, core.length*2, 0,0)

#input("PAUSE")


femm.mi_saveas(f"tmp.fem")
femm.mi_analyze()
femm.mi_loadsolution()

#input("PAUSE")

Lp = nff.loop_inductance('Ltp', num_turns, L_curr)
Lm = nff.loop_inductance('Ltm', num_turns, L_curr)
print(f'Inductance: {Lp+Lm}')


copper_loss_r = nff.total_loop_losses('Ltp', num_turns)
copper_loss_l = nff.total_loop_losses('Ltm', num_turns)
print(f'Copper Loss: {copper_loss_r+copper_loss_l}')

input("PAUSE")


femm.closefemm()






