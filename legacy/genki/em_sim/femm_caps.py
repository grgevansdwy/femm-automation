# /// script
# dependencies = [
#     "coloredlogs",
#     "numpy",
#     "pyfemm",
# ]
# ///

# All units in millimeters unless otherwise specified.

import argparse
import dataclasses
import enum
import logging
import os
import sys

import coloredlogs
import femm
import numpy as np

import femm_coils

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(logger=log, fmt="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO, reconfigure=False)

BOUNDING_CIRCLE_RAD = 200
CAP_WIDTH = 2.55
CAP_HEIGHT = 1.9
CAP_DEPTH = 3.47

def draw_rectangle(x1, y1, width, thickness, circuit_name, femm_group_num):
    femm.mi_drawline(x1, y1, x1, y1 + thickness)
    femm.mi_drawline(x1, y1 + thickness, x1 + width, y1 + thickness)
    femm.mi_drawline(x1 + width, y1 + thickness, x1 + width, y1)
    femm.mi_drawline(x1 + width, y1, x1, y1)
    x_center, y_center = x1 + width / 2, y1 + thickness / 2
    femm.mi_addblocklabel(x_center, y_center)
    femm.mi_selectlabel(x_center, y_center)
    femm.mi_setblockprop("Copper", 1, 0, circuit_name, 0, femm_group_num, 1)
    femm.mi_clearselected()

def main(freq_khz):
    femm.openfemm()
    femm.newdocument(0) # 0: Magnetics Problem
    femm.mi_probdef(freq_khz * 1000, 'millimeters', 'planar', 1e-12, CAP_DEPTH, 30)
    femm.mi_getmaterial('Air')
    femm.mi_getmaterial('Copper')

    femm.mi_addblocklabel(BOUNDING_CIRCLE_RAD/2, -BOUNDING_CIRCLE_RAD/2)
    femm.mi_selectlabel(BOUNDING_CIRCLE_RAD/2, -BOUNDING_CIRCLE_RAD/2)
    femm.mi_setblockprop('Air')
    femm.mi_clearselected()
    femm.mi_makeABC(1, BOUNDING_CIRCLE_RAD, 0, 0)

    femm.mi_showgrid()
    femm.mi_zoomnatural()

    cap_circuit_names = ["CAP_IN", "CAP_OUT"]
    for name in cap_circuit_names:
        femm.mi_addcircprop(name, femm_coils.DRIVE_CURRENT, 0)

    CAP_METAL_WIDTH = CAP_WIDTH * 0.75
    CAP_METAL_HEIGHT = CAP_HEIGHT * 0.75
    num_electrodes = 16

    x = 0
    y = 0
    width = CAP_METAL_WIDTH
    thickness = (CAP_METAL_HEIGHT / num_electrodes) / 4
    gap = (CAP_METAL_HEIGHT / num_electrodes) * 3/4
    cap_to_cap = 4

    for _ in range(4):
        for electrode_num in range(16):
            circuit_name = cap_circuit_names[electrode_num % 2]
            femm_group_num = electrode_num % 2
            draw_rectangle(x, y, width, thickness, circuit_name, femm_group_num)
            y += thickness
            y += gap
        x += cap_to_cap
        y = 0

    # for turn_num in range(len(femm_coils.COILSETS[0].tank.turn_radii)):
    #     turn_circuit_name = f"TURN{turn_num}"
    #     # Note -1 to get opposite direction to produce desired B fields.
    #     femm.mi_addcircprop(turn_circuit_name, -1 * femm_coils.DRIVE_CURRENT, 0)
    #     trace_width = femm_coils.COILSETS[0].tank.trace_width
    #     trace_thickness = femm_coils.COILSETS[0].tank.trace_thickness
    #     trace_spacing = np.mean(np.diff(femm_coils.COILSETS[0].tank.turn_radii))
    #     x = -trace_width - (turn_num * (trace_width + trace_spacing))
    #     draw_rectangle(x, 0, trace_width, trace_thickness, turn_circuit_name, 2)

    femm.mi_saveas(f"{sys.argv[0]}.FEM")
    femm.mi_analyze()
    femm.mi_loadsolution()

    input()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FEMM capacitor simulator")
    parser.add_argument("--freq-khz", type=int, required=True, help="Frequency (kHz) to analyze at")
    args = parser.parse_args()
    main(args.freq_khz)
