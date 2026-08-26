import femm
import numpy as np

# ==========================================================
# PARAMETERS
# ==========================================================

freq = 6.78e6          # Hz
current = 45           # Peak current (A)

num_turns = 4

wire_width = 10.0    # mm
wire_thickness = 2.0
turn_spacing = 3    # mm between turns

inner_radius = 10     # mm to inside of winding

coil_height = num_turns * wire_thickness + (num_turns-1)*turn_spacing

# ==========================================================
# FEMM
# ==========================================================

femm.openfemm()
femm.newdocument(0)

# Axisymmetric magnetics
femm.mi_probdef(
    freq,
    "millimeters",
    "axi",
    1e-12,
    0,
    30
)

femm.mi_makeABC(4,100,0,0)

femm.mi_addcircprop(
    "coil",
    current,
    1
)

femm.mi_getmaterial("Air")
femm.mi_getmaterial("Copper")

# ==========================================================
# DRAW TURNS
# ==========================================================

for n in range(num_turns):

    z1 = n*(wire_thickness + turn_spacing)
    z2 = z1 + wire_thickness

    r1 = inner_radius
    r2 = inner_radius + wire_width

    femm.mi_drawrectangle(
        r1,
        z1,
        r2,
        z2
    )

    cx = (r1+r2)/2
    cz = (z1+z2)/2

    femm.mi_addblocklabel(cx,cz)
    femm.mi_selectlabel(cx,cz)

    femm.mi_setblockprop(
        "Copper",
        1,
        0,
        "coil",
        0,
        0,
        1
    )

    femm.mi_clearselected()

# ==========================================================
# AIR REGIONS
# ==========================================================

# Inside bore
femm.mi_addblocklabel(inner_radius/2,coil_height/2)
femm.mi_selectlabel(inner_radius/2,coil_height/2)
femm.mi_setblockprop("Air",1,0,"",0,0,0)
femm.mi_clearselected()


# ==========================================================
# SOLVE
# ==========================================================
femm.mi_saveas("air_core_axi.fem")

femm.mi_analyze()
femm.mi_loadsolution()

# ==========================================================
# RESULTS
# ==========================================================

I, V, flux = femm.mo_getcircuitproperties("coil")

L = flux/I

print(f"Current = {I:.3f} A")
print(f"Flux linkage = {flux:.6e} Wb-turn")
print(f"Inductance = {L*1e6:.2f} uH")

input("Press Enter to close...")

femm.closefemm()