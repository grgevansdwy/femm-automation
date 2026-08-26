import femm
import matplotlib.pyplot as plt
import matplotlib.transforms as xfrms
from matplotlib.font_manager import FontProperties
import numpy as np
import random
from quantiphy import Quantity
import datetime
from pathlib import Path
import itertools as iter
import sys
from typing import NamedTuple
from dataclasses import dataclass, fields
import ferrite_and_core_info as fci
import math

def draw_round_wire(center_r, center_y, wire_radius, material, circuit_name):
    """Draws a single solid circular conductor (one turn)."""
    # 1. Draw the physical circle (using 2 nodes and 2 arcs)
    femm.mi_addnode(center_r - wire_radius, center_y)
    femm.mi_addnode(center_r + wire_radius, center_y)
    femm.mi_addarc(center_r - wire_radius, center_y, center_r + wire_radius, center_y, 180, 1)
    femm.mi_addarc(center_r + wire_radius, center_y, center_r - wire_radius, center_y, 180, 1)
    
    # 2. Place a block label exactly in the center of the wire
    femm.mi_addblocklabel(center_r, center_y)
    femm.mi_selectlabel(center_r, center_y)
    
    # 3. Assign properties: AutoMesh=1, MeshSize=1 (adjust as needed), Group=2, Turns=1
    # Because the circuit is Series, FEMM will push the full circuit current through this 1 turn.
    femm.mi_setblockprop(material, 1, 1, circuit_name, 0, 2, 1)
    femm.mi_clearselected()

def draw_coil_grid(r_min, r_max, y_min, y_max, turns_radial, turns_axial, wire_diam, circuit_name, material="Cu"):
    """Distributes a grid of solid turns evenly inside a defined rectangular window."""
    wire_radius = wire_diam / 2.0
    r_step = (r_max - r_min) / turns_radial
    y_step = (y_max - y_min) / turns_axial
    
    for row in range(turns_axial):
        center_y = y_min + (y_step / 2.0) + (row * y_step)
        for col in range(turns_radial):
            center_r = r_min + (r_step / 2.0) + (col * r_step)
            draw_round_wire(center_r, center_y, wire_radius, material, circuit_name)

def draw_polygon(points):
    # Add nodes
    for p in points:
        femm.mi_addnode(p[0], p[1])
    # Connect nodes with segments
    for i in range(len(points)):
        next_i = (i + 1) % len(points)
        femm.mi_addsegment(points[i][0], points[i][1], points[next_i][0], points[next_i][1])


def draw_wire_column(column_r, window_z0, window_height, turns, circuit_name, wire_diameter,  material="Cu"):
    """Distributes a grid of solid turns evenly inside a defined rectangular window."""
    wire_radius = wire_diameter / 2.0
    z_spacing = (window_height-(2*padding)-wire_diameter)/(turns-1)
    #print(f"z_spacing: {z_spacing}, wire_diameter: {wire_diameter}, window_height: {window_height}, turns: {turns}, padding:{padding}")
    if(z_spacing < wire_diameter):
        print("Packing too tight")
        input("Pausing for debug")
    for wire_num in range(turns):
        draw_round_wire(column_r, window_z0+padding+wire_radius+(wire_num*z_spacing), wire_radius, material, circuit_name)
        #print(wire_num*z_spacing)

# Start FEMM and create a new magnetics document
femm.openfemm()
femm.newdocument(0) # 0 creates a Magnetics problem




femm.mi_addcircprop('Pri', .37, 1)   # 1 Ampere test current
femm.mi_addcircprop('Sec', 0.0, 1) # 0 Amperes (Open circuit test)
femm.mi_addmaterial('Air', 1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0)
femm.mi_addmaterial('Ferrite', 2200, 2200, 0, 0, 0, 0, 0, 0, 0, 0, 0)
femm.mi_addmaterial('Cu', 1, 1, 0, 0, 58, 0, 0, 1, 0, 0, 0)

femm.mi_modifymaterial('Ferrite', 10, 0.09966865249) #loss angle @ 65kHz
femm.mi_modifymaterial('Ferrite', 11, 0.09966865249) #loss angle @ 65kHz 

# 1. DEFINE PARAMETERS (Based loosely on a PQ 40/40 core, units in mm)
# You can change these values to match your specific datasheet.
D1 = 14.9     # Center post diameter
D2 = 37      # Inner diameter of the outer shell (winding window outer limit)
D3 = 40.5      # Outer diameter of the core
H  = 39.8      # Total height of the mated core (both halves)
C  = 29.5/2      # Depth of the recess (winding window height for one half)
g  = 0       # Total air gap in the center post

# Derived radial (x) and vertical (y) coordinates
r1 = D1 / 2.0 #inner radius of window (outer radius of center post)
r2 = D2 / 2.0 #outer radius of window
r3 = D3 / 2.0
y_top = H / 2.0
y_win = C         # Top edge of the winding window
y_gap = g / 2.0   # Top edge of the air gap

window_width = r1 - r2

pri_turns = 32
sec_turns = 22





# 2. PROBLEM DEFINITION
# mi_probdef(frequency, units, type, precision, depth, minangle)
# 'axi' sets it to Axisymmetric mode.
femm.mi_probdef(0, 'millimeters', 'axi', 1e-8, 0, 30)

# Helper function to draw closed polygons

# 3. DRAW THE CORE GEOMETRY
# Top Half of the PQ Core
top_core = [
    (0, y_gap), (r1, y_gap), (r1, y_win), (r2, y_win), 
    (r2, 0), (r3, 0), (r3, y_top), (0, y_top)
]
draw_polygon(top_core)

# Bottom Half of the PQ Core (Mirrored across y=0)
bottom_core = [
    (0, -y_gap), (r1, -y_gap), (r1, -y_win), (r2, -y_win), 
    (r2, 0), (r3, 0), (r3, -y_top), (0, -y_top)
]
draw_polygon(bottom_core)
padding = .05

pri_wire_diameter = 1.8
sec_wire_diameter = 2.286

#.5p,1s,1p,1s,.5p interleaving
col_num = 5

col0_radius = r1+padding+pri_wire_diameter/2
col1_radius = r1+2*padding+pri_wire_diameter+sec_wire_diameter/2
col2_radius = r1+3*padding+3*pri_wire_diameter/2+sec_wire_diameter
col3_radius = r1+4*padding+2*pri_wire_diameter+3*sec_wire_diameter/2
col4_radius = r1+5*padding+5*pri_wire_diameter/2+2*sec_wire_diameter

draw_wire_column(column_r = col0_radius, window_z0=-y_win, window_height=C*2, turns=8, circuit_name="Pri", wire_diameter=pri_wire_diameter,  material="Cu")
draw_wire_column(column_r = col1_radius, window_z0=-y_win, window_height=C*2, turns=11, circuit_name="Sec", wire_diameter=sec_wire_diameter,  material="Cu")
draw_wire_column(column_r = col2_radius, window_z0=-y_win, window_height=C*2, turns=16, circuit_name="Pri", wire_diameter=pri_wire_diameter,  material="Cu")
draw_wire_column(column_r = col3_radius, window_z0=-y_win, window_height=C*2, turns=11, circuit_name="Sec", wire_diameter=sec_wire_diameter,  material="Cu")
draw_wire_column(column_r = col4_radius, window_z0=-y_win, window_height=C*2, turns=8, circuit_name="Pri", wire_diameter=pri_wire_diameter,  material="Cu")

femm.mi_makeABC(4, 40, 0,0)


# Background Air
femm.mi_addblocklabel(r3 + 5, 0)
femm.mi_selectlabel(r3 + 5, 0)
femm.mi_setblockprop('Air', 1, 3, '<None>', 0, 0, 0)
femm.mi_clearselected()




# ==========================================
# 5. ASSIGN CORE AND AIR PROPERTIES
# ==========================================
# Core properties
y_yoke = (y_top + y_win) / 2.0
for y_pos in [y_yoke, -y_yoke]:
    femm.mi_addblocklabel(r1 / 2.0, y_pos)
    femm.mi_selectlabel(r1 / 2.0, y_pos)
    femm.mi_setblockprop('Ferrite', 1, 1, '<None>', 0, 1, 0)
    femm.mi_clearselected()

# Center Air gap
femm.mi_addblocklabel(r1+.1, C/2)
femm.mi_selectlabel(r1+.1, C/2)
femm.mi_setblockprop('Air', 1, 0.5, '<None>', 0, 0, 0)
femm.mi_clearselected()




femm.mi_saveas('tmp.fem')

# 8. FINALIZE
femm.mi_zoomnatural()
try:
    femm.mi_analyze()
    femm.mi_loadsolution()
except BaseException as error:
    print('Caught this error: ' + repr(error))
    input("Pausing for debug")

input()