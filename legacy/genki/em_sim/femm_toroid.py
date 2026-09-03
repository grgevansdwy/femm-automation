# /// script
# dependencies = [
#     "coloredlogs",
#     "numpy",
#     "pyfemm",
# ]
# ///

"""
THIS CODE IS MOSTLY JUST COPIED FROM NOEL'S REPO.
It's not been edited / reviewed yet.
"""


# All units in millimeters unless otherwise specified.

import femm
import numpy as np
from dataclasses import dataclass

@dataclass
class toroidal_core_shape():
    name: str
    od: float = 0 #outer diameter in mm
    id: float = 0 #inner diameter in mm
    h: float = 0 #height
    type: str = 'toroid'

@dataclass
class ferrite():
    name: str
    mu_i: float #relative permeability
    mu_prime_prime: float=0
    resistivity: float=1e11 # Ohms*cm
    coercivity: float=20 #Oe

T157 = toroidal_core_shape(name='T157', od=39.88, id=24.13, h=14.48)
f6 = ferrite(name='_-6', mu_i = 8.5)

freq = 6.78e6
omega = 2*np.pi*freq

selected_core = T157
selected_ferrite = f6

toroid_id = selected_core.id
toroid_od = selected_core.od

number_of_stacked_cores = 1

num_turns = 4
mu = selected_ferrite.mu_i
wire_width = 12.4 #mm
wire_thickness = 1 #mm .2
L_curr = 45 #this is peak, NOT RMS
toroid_height = (selected_core.h)*number_of_stacked_cores
core_packing_in_radians = 2*np.pi #winding coverage of core -- 2*np.pi is full circle

femm.openfemm()

femm.newdocument(0)

femm.mi_probdef(6780000, 'millimeters', 'planar', 1.e-12, toroid_height, 30)

femm.mi_makeABC(4, 400, 0,0)

femm.mi_addcircprop('inductor', L_curr, 1)

femm.mi_getmaterial('Air')
femm.mi_getmaterial('Copper')

femm.mi_addmaterial('Ferrite')
femm.mi_modifymaterial('Ferrite', 1, mu)
femm.mi_modifymaterial('Ferrite', 2, mu)
femm.mi_modifymaterial('Ferrite', 5, 1e-11) #conductivity
femm.mi_modifymaterial('Ferrite', 10, .0025)
femm.mi_modifymaterial('Ferrite', 11, .0025)

turn_angle = (core_packing_in_radians) / np.round(num_turns)
inner_wire_outer_r = toroid_id/2
inner_wire_inner_r = inner_wire_outer_r - wire_thickness
outer_wire_inner_r = toroid_od/2
outer_wire_outer_r = outer_wire_inner_r + wire_thickness

print(f'wire_outer_r: {inner_wire_outer_r}')

inner_winding_starting_angle = turn_angle/2

#draw inner wire
for i in range(int(np.floor(num_turns))):

    trace_angle_subtended = wire_width / (toroid_id/2)

    inner_winding_angle = turn_angle*i + inner_winding_starting_angle

    x1 = inner_wire_outer_r*np.cos(inner_winding_angle-(trace_angle_subtended/2))
    y1 = inner_wire_outer_r*np.sin(inner_winding_angle-(trace_angle_subtended/2))

    x2 = inner_wire_outer_r*np.cos(inner_winding_angle+(trace_angle_subtended/2))
    y2 = inner_wire_outer_r*np.sin(inner_winding_angle+(trace_angle_subtended/2))

    x3 = inner_wire_inner_r*np.cos(inner_winding_angle-(trace_angle_subtended/2))
    y3 = inner_wire_inner_r*np.sin(inner_winding_angle-(trace_angle_subtended/2))

    x4 = inner_wire_inner_r*np.cos(inner_winding_angle+(trace_angle_subtended/2))
    y4 = inner_wire_inner_r*np.sin(inner_winding_angle+(trace_angle_subtended/2))

    femm.mi_drawarc(x1, y1, x2, y2, np.rad2deg(trace_angle_subtended), 1)
    femm.mi_drawarc(x3, y3, x4, y4, np.rad2deg(trace_angle_subtended), 1)
    femm.mi_drawline(x1, y1, x3, y3)
    femm.mi_drawline(x2, y2, x4, y4)

    wire_center_x = (inner_wire_outer_r-(wire_thickness/2))*np.cos(inner_winding_angle)
    wire_center_y = (inner_wire_outer_r-(wire_thickness/2))*np.sin(inner_winding_angle)

    femm.mi_addblocklabel(wire_center_x, wire_center_y)
    femm.mi_selectlabel(wire_center_x, wire_center_y)
    femm.mi_setblockprop('Copper', 1, 1, 'inductor', 0, 1, -1)
    femm.mi_clearselected()

#draw outer wire
for i in range(int(np.ceil(num_turns))):

    outer_winding_angle = turn_angle*i
    trace_angle_subtended = wire_width / (toroid_od/2)

    x1 = outer_wire_outer_r*np.cos(outer_winding_angle-(trace_angle_subtended/2))
    y1 = outer_wire_outer_r*np.sin(outer_winding_angle-(trace_angle_subtended/2))

    x2 = outer_wire_outer_r*np.cos(outer_winding_angle+(trace_angle_subtended/2))
    y2 = outer_wire_outer_r*np.sin(outer_winding_angle+(trace_angle_subtended/2))

    x3 = outer_wire_inner_r*np.cos(outer_winding_angle-(trace_angle_subtended/2))
    y3 = outer_wire_inner_r*np.sin(outer_winding_angle-(trace_angle_subtended/2))

    x4 = outer_wire_inner_r*np.cos(outer_winding_angle+(trace_angle_subtended/2))
    y4 = outer_wire_inner_r*np.sin(outer_winding_angle+(trace_angle_subtended/2))

    femm.mi_drawarc(x1, y1, x2, y2, np.rad2deg(trace_angle_subtended), 1)
    femm.mi_drawarc(x3, y3, x4, y4, np.rad2deg(trace_angle_subtended), 1)
    femm.mi_drawline(x1, y1, x3, y3)
    femm.mi_drawline(x2, y2, x4, y4)

    wire_center_x = (outer_wire_outer_r-(wire_thickness/2))*np.cos(outer_winding_angle)
    wire_center_y = (outer_wire_outer_r-(wire_thickness/2))*np.sin(outer_winding_angle)

    femm.mi_addblocklabel(wire_center_x, wire_center_y)
    femm.mi_selectlabel(wire_center_x, wire_center_y)
    femm.mi_setblockprop('Copper', 1, 1, 'inductor', 0, 1, 1)

femm.mi_drawarc(-toroid_od/2, 0, toroid_od/2, 0, 180, 1)
femm.mi_drawarc(toroid_od/2, 0, -toroid_od/2, 0, 180, 1)

femm.mi_drawarc(-toroid_id/2, 0, toroid_id/2, 0, 180, 1)
femm.mi_drawarc(toroid_id/2, 0, -toroid_id/2, 0, 180, 1)

femm.mi_addblocklabel(0, 0)
femm.mi_selectlabel(0, 0)
femm.mi_setblockprop('Air', 1, 1, 0)
femm.mi_clearselected()

femm.mi_addblocklabel(toroid_od-1, 0)
femm.mi_selectlabel(toroid_od-1, 0)
femm.mi_setblockprop('Air', 1, 1, 0)
femm.mi_clearselected()

femm.mi_addblocklabel(((toroid_od+toroid_id)/4), 0)
femm.mi_selectlabel(((toroid_od+toroid_id)/4), 0)
femm.mi_setblockprop('Ferrite', 1, 1, 0)
femm.mi_clearselected()

femm.mi_saveas('torroid.fem')
femm.mi_analyze()
femm.mi_loadsolution()

input("PAUSE")
femm.closefemm()
