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

log = logging.getLogger(os.path.basename(__file__))
coloredlogs.install(logger=log, fmt="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO, reconfigure=False)

BOUNDING_CIRCLE_RAD = 1000
DRIVE_CURRENT = 10 # Amps, magnitude (sinusoidal positive-peak)

def mil_to_mm(mil):
    inches = mil / 1000
    return inches * 25.4

def copper_oz_to_mm(oz):
    return oz * 35e-3

class TraceTrapezoid:
    """
    Draw trapezoid, with the sides being composed of 1+ distinct line segments.
                ______
               /      \
              /        \
             /          \
            `            `
          `                `
        `                    `
       ------------------------
    """

    RECTANGLE = [(0, 1)]
    DEG45 = [(1, 1)]
    DEG63 = [(0.5, 1)]
    DEG45_DEG63 = [(0.25, 0.25), (0.375, 0.75)]
    DEG45_DEG90 = [(0.5, 0.5), (0, 0.5)]

    def __init__(self, side_dx_dy):
        # Normalized values.
        self.side_dx_dy = side_dx_dy
        angles = [f"{np.rad2deg(np.atan2(dy, dx)):0.1f}" for dx, dy in self.side_dx_dy]
        log.info(f"{self.__class__.__name__}: angles=[{", ".join(angles)}]")

    def draw(self, y_base, r, trace_width, trace_thickness):
        """Return (x1, y1, x2, y2) - the rectangle boundary around the trapezoid."""
        x1 = r - (trace_width / 2)
        x2 = r + (trace_width / 2)
        y1 = y_base
        y2 = y_base + trace_thickness
        trace_thickness = y2 - y1
        # Draw left side.
        xtop_left, ytop_left = self.draw_side(x1, y1, trace_thickness, trace_thickness)
        assert(ytop_left == y2)
        # Draw right side.
        xtop_right, ytop_right = self.draw_side(x2, y1, -1 * trace_thickness, trace_thickness)
        assert(ytop_right == y2)
        # Draw top.
        femm.mi_drawline(xtop_left, y2, xtop_right, y2)
        # Draw bottom.
        femm.mi_drawline(x1, y1, x2, y1)
        return x1, y1, x2, y2

    def draw_side(self, xcurr, ycurr, x_mult, y_mult):
        for dx, dy in self.side_dx_dy:
            dx = dx * x_mult
            dy = dy * y_mult
            femm.mi_drawline(xcurr, ycurr, xcurr + dx, ycurr + dy)
            xcurr += dx
            ycurr += dy
        return xcurr, ycurr

class TraceCircle:

    def __init__(self):
        pass

    def draw(self, y_base, r, trace_width, trace_thickness):
        """Return (x1, y1, x2, y2) - the rectangle boundary around the circle."""
        rr = trace_width / 2
        x_center = r
        y_center = y_base + rr
        femm.mi_drawarc(x_center, y_center - rr, x_center, y_center + rr, 180, 1)
        femm.mi_drawarc(x_center, y_center + rr, x_center, y_center - rr, 180, 1)
        return (x_center - rr, y_center - rr, x_center + rr, y_center + rr)

class CoilType(enum.Enum):
    LOOP = 1
    TANK = 2
    RC = 3

    def femm_group(self):
        """FEMM group value. Just used for organization."""
        return self.value + 1

@dataclasses.dataclass
class Coil:
    type: CoilType
    turn_radii: list
    trace_width: float
    trace_thickness: float

    def circuit_name(self, idx):
        """
        FEMM circuit name. Used for driving currents.
        Note each turn is considered a separate circuit, each getting the full current amount.
        """
        return f"{self.type.name}{idx}"

    def draw(self, y_base):
        trace_drawer = TraceTrapezoid(TraceTrapezoid.DEG45_DEG90)
        for i, r in enumerate(self.turn_radii):
            # for r_ in [r - self.trace_width / 4, r + self.trace_width / 4]:
            #     x1, y1, x2, y2 = trace_drawer.draw(y_base, r_, self.trace_width / 2 * 0.75, self.trace_thickness)
            for r_ in [r]:
                x1, y1, x2, y2 = trace_drawer.draw(y_base, r_, self.trace_width, self.trace_thickness)
                femm.mi_selectrectangle(x1, y1, x2, y2)
                femm.mi_setgroup(self.type.femm_group())
                femm.mi_clearselected()
                x_center = x1 + (x2 - x1) / 2
                y_center = y1 + (y2 - y1) / 2
                femm.mi_addblocklabel(x_center, y_center)
                femm.mi_selectlabel(x_center, y_center)
                # Note: These params don't seem to matter: @magdir.
                femm.mi_setblockprop("Copper", 1, 0, self.circuit_name(i), 0, self.type.femm_group(), 1)
                femm.mi_clearselected()

@dataclasses.dataclass
class CoilSet:
    name: str
    loop: Coil
    tank: Coil
    loop_tank_separation: float
    # rc: Coil

    def draw(self):
        self.loop.draw(0)
        self.tank.draw(self.loop_tank_separation + self.loop.trace_thickness)

@dataclasses.dataclass
class Result:
    R11: float
    R22: float
    L11: float
    L22: float
    M12: float
    M21: float

    def pprint(self):
        assert(np.isclose(self.M12, self.M21, rtol=1e-12))
        print(f"R11 [Ω], R22 [Ω], L11 [uH], L22 [uH], M12 [nH], k")
        print(f"{self.R11:0.3f}")
        print(f"{self.R22:0.3f}")
        print(f"{self.L11*1e6:0.3f}")
        print(f"{self.L22*1e6:0.3f}")
        print(f"{self.M12*1e6:0.3f}")
        print(f"{self.M21 / np.sqrt(self.L11 * self.L22):0.3f}")

COILSETS = [
    CoilSet(
        name='225-35 RevA',
        loop=Coil(
            type=CoilType.LOOP,
            turn_radii=[50],
            trace_width=mil_to_mm(100),
            trace_thickness=copper_oz_to_mm(1),
        ),
        tank=Coil(
            type=CoilType.TANK,
            turn_radii=[61.928, 69.040, 76.152, 83.264, 90.376],
            trace_width=mil_to_mm(125),
            trace_thickness=copper_oz_to_mm(1),
        ),
        loop_tank_separation=1.6,
    )
]

def get_impedance(coil, i_drive):
    if i_drive == 0:
        return (0, 0)
    R = 0
    L = 0
    for i, _ in enumerate(coil.turn_radii):
        voltage_drop = femm.mo_getcircuitproperties(coil.circuit_name(i))[1]
        flux_linkage = femm.mo_getcircuitproperties(coil.circuit_name(i))[2]
        R += np.real(voltage_drop) / i_drive
        # See https://www.femm.info/doku/doku.php?id=FAQ#anchor27 on what imaginary flux linkage represents.
        L += np.real(flux_linkage) / i_drive
    return (R, L)

def simulate(coilset, i_drives):
    for coil in [coilset.loop, coilset.tank]:
        for i in range(len(coil.turn_radii)):
            femm.mi_modifycircprop(coil.circuit_name(i), 1, i_drives[coil.type])
    femm.mi_saveas(f"{sys.argv[0]}.FEM")
    femm.mi_analyze()
    femm.mi_loadsolution()
    return Result(
        get_impedance(coilset.loop, i_drives[CoilType.LOOP])[0],
        get_impedance(coilset.tank, i_drives[CoilType.TANK])[0],
        get_impedance(coilset.loop, i_drives[CoilType.LOOP])[1],
        get_impedance(coilset.tank, i_drives[CoilType.TANK])[1],
        get_impedance(coilset.tank, i_drives[CoilType.LOOP])[1],
        get_impedance(coilset.loop, i_drives[CoilType.TANK])[1])

class PositionTweak:
    def __init__(self, shifts):
        self.shifts = shifts

    def __enter__(self):
        for coil_type, shift in self.shifts.items():
            femm.mi_selectgroup(coil_type.femm_group())
            femm.mi_movetranslate(0, shift)
            femm.mi_clearselected()

    def __exit__(self, exc_type, exc, tb):
        for coil_type, shift in self.shifts.items():
            femm.mi_selectgroup(coil_type.femm_group())
            femm.mi_movetranslate(0, -shift)
            femm.mi_clearselected()

def main(freq_khz):
    femm.openfemm()
    femm.newdocument(0) # 0: Magnetics Problem
    femm.mi_probdef(freq_khz * 1000, 'millimeters', 'axi', 1e-12, 0, 30)
    femm.mi_getmaterial('Air')
    femm.mi_getmaterial('Copper')

    femm.mi_addblocklabel(BOUNDING_CIRCLE_RAD/2, -BOUNDING_CIRCLE_RAD/2)
    femm.mi_selectlabel(BOUNDING_CIRCLE_RAD/2, -BOUNDING_CIRCLE_RAD/2)
    femm.mi_setblockprop('Air')
    femm.mi_clearselected()
    femm.mi_makeABC(1, BOUNDING_CIRCLE_RAD, 0, 0)

    femm.mi_showgrid()
    femm.mi_zoomnatural()

    coilset = COILSETS[0]
    for coil in [coilset.loop, coilset.tank]:
        for i in range(len(coil.turn_radii)):
            # Specify parallel (0) for option of turns with parallel sub-traces.
            # Specify 0 amps for now.
            femm.mi_addcircprop(coil.circuit_name(i), 0, 0)

    coilset.draw()

    # Isolated self-impedances.
    with PositionTweak({CoilType.LOOP: 0, CoilType.TANK: 300}):
        result = simulate(coilset, {CoilType.LOOP: DRIVE_CURRENT, CoilType.TANK: 0})
        R11 = result.R11
        L11 = result.L11
    with PositionTweak({CoilType.LOOP: 300, CoilType.TANK: 0}):
        result = simulate(coilset, {CoilType.LOOP: 0, CoilType.TANK: DRIVE_CURRENT})
        R22 = result.R22
        L22 = result.L22

    # Mutual inductances.
    M12 = simulate(coilset, {CoilType.LOOP: DRIVE_CURRENT, CoilType.TANK: 0}).M12
    M21 = simulate(coilset, {CoilType.LOOP: 0, CoilType.TANK: DRIVE_CURRENT}).M21

    Result(R11, R22, L11, L22, M12, M21).pprint()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FEMM coil simulator")
    parser.add_argument("--freq-khz", type=int, required=True, help="Frequency (kHz) to analyze at")
    args = parser.parse_args()
    main(args.freq_khz)
