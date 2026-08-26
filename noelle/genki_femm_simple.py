import dataclasses
import femm
import femm
import math
import numpy as np
import quantiphy

# TODO
# - https://www.femm.drsz.pl/doku.php/users_manual
# - pyfemm-manual.pdf

@dataclasses.dataclass
class CoilSet:
    config_name: str

    # Loop
    loop_offset: float
    loop_r: float
    num_loop_turns: int
    num_parallel_loop_traces: int
    num_loop_layers: int
    loop_trace_spacing: float
    loop_turn_spacing: float
    loop_trace_width: float
    loop_PCB_thickness: float

    # Tank
    tank_center_r: float
    num_tank_turns: int
    num_parallel_tank_traces: int
    num_tank_layers: int
    tank_trace_spacing: float
    tank_turn_spacing: float
    tank_trace_width: float
    tank_PCB_thickness: float

@dataclasses.dataclass
class Inductances:
    L11: float
    M12: float
    M21: float
    L22: float

def _get_winding_radii_turns_and_parallel_traces(central_radius, number_of_turns, number_of_parallel_traces, trace_width, trace_spacing, turn_spacing):
    # TODO:
    # - Carefully go through these geometry calculations
    #   - E.g., is turn_spacing in-between or center-center
    trace_radii = []
    turn_width = number_of_parallel_traces*(trace_width+trace_spacing)-trace_spacing
    turn_indices = list(range(((number_of_turns+1)%2), math.floor(number_of_turns/2)+1))
    trace_indices = list(range(((number_of_parallel_traces+1)%2), math.floor(number_of_parallel_traces/2)+1))
    for turn in turn_indices:
        for trace in trace_indices:
            ptu_ptr = central_radius + ((turn+(turn_indices[0]*(turn-turn_indices[0])))/(1+turn_indices[0]))*(turn_width+turn_spacing) + ((trace+(trace_indices[0]*(trace-trace_indices[0])))/(1+trace_indices[0]))*(trace_width+trace_spacing)
            ntu_ntr = central_radius - ((turn+(turn_indices[0]*(turn-turn_indices[0])))/(1+turn_indices[0]))*(turn_width+turn_spacing) - ((trace+(trace_indices[0]*(trace-trace_indices[0])))/(1+trace_indices[0]))*(trace_width+trace_spacing)
            ptu_ntr = central_radius + ((turn+(turn_indices[0]*(turn-turn_indices[0])))/(1+turn_indices[0]))*(turn_width+turn_spacing) - ((trace+(trace_indices[0]*(trace-trace_indices[0])))/(1+trace_indices[0]))*(trace_width+trace_spacing)
            ntu_ptr = central_radius - ((turn+(turn_indices[0]*(turn-turn_indices[0])))/(1+turn_indices[0]))*(turn_width+turn_spacing) + ((trace+(trace_indices[0]*(trace-trace_indices[0])))/(1+trace_indices[0]))*(trace_width+trace_spacing)
            trace_radii.append(ptu_ptr)
            if(ntu_ntr != ptu_ptr and ntu_ntr > 0):
                trace_radii.append(ntu_ntr)
            if(ptu_ntr != ptu_ptr and ptu_ntr != ntu_ntr and ptu_ntr > 0):
                trace_radii.append(ptu_ntr)
            if(ntu_ptr != ptu_ptr and ntu_ptr != ntu_ntr and ntu_ptr != ptu_ntr and ntu_ptr > 0):
                trace_radii.append(ntu_ptr)
    return trace_radii

def _draw_and_plot_coil_traces(winding_radii, top_of_trace, winding_width, winding_thickness, femm_circuit_name, circuit_group):
    y_coord = top_of_trace
    images = [1]
    # for turn in winding_radii:
    for idx, turn in enumerate(winding_radii):
        for i in images: # Draw axially symettric windings
            neg_x = i*(turn - (winding_width/2))
            pos_x = i*(turn + (winding_width/2))
            pos_y = y_coord
            neg_y = y_coord-winding_thickness
            femm.mi_drawrectangle(neg_x, pos_y, pos_x, neg_y)
            femm.mi_addblocklabel(i*turn, y_coord- winding_thickness/2)
            femm.mi_selectlabel(i*turn, y_coord-winding_thickness/2)
            if(i<0):
                circuit_polarity = '-'
            else:
                circuit_polarity = '+'
            femm.mi_setblockprop('Copper', 1, 1, circuit_polarity+femm_circuit_name+str(idx), 0, circuit_group)
            femm.mi_clearselected()

def _get_inductance(circuit_name, num_turns_of_induced, driving_current):
    # vals = femm.mo_getcircuitproperties(circuit_name)
    # print(vals)
    flux_linkage = np.abs(femm.mo_getcircuitproperties(circuit_name)[2])
    # TODO
    # - !g pyfemm calculate inductance of coil
    #   - pause FEMM in analysis mode and do https://www.femm.drsz.pl/doku.php/users_manual
    #   - http://www.coilgun.eclipse.co.uk/inductance_calculation_technique.html
    # L = flux_linkage * num_turns_of_induced / driving_current
    # L = flux_linkage / (num_turns_of_induced * driving_current)
    L = flux_linkage / driving_current
    return L

def _get_inductances(loop_group, loop_turn_current, loop_y_shift, num_loop_turns, tank_group, tank_turn_current, tank_y_shift, num_tank_turns):
    for i in range(num_loop_turns):
        femm.mi_modifycircprop('+loop'+str(i), 1, loop_turn_current)
        femm.mi_modifycircprop('-loop'+str(i), 1, -loop_turn_current)
    for i in range(num_tank_turns):
        femm.mi_modifycircprop('+tank'+str(i), 1, tank_turn_current)
        femm.mi_modifycircprop('-tank'+str(i), 1, -tank_turn_current)

    femm.mi_selectgroup(loop_group)
    femm.mi_movetranslate(0,loop_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(tank_group)
    femm.mi_movetranslate(0,tank_y_shift)
    femm.mi_clearselected()

    femm.mi_saveas('coil.fem')
    femm.mi_analyze()
    femm.mi_loadsolution()

    L11 = 0
    M12 = 0
    M21 = 0
    L22 = 0

    if (loop_turn_current > 0):
        L11 = _get_inductance('+loop0', num_loop_turns, loop_turn_current)
        M12 = _get_inductance('+tank0', num_tank_turns, loop_turn_current)
        print(quantiphy.Quantity(L11))
    if (tank_turn_current > 0):
        M21 = _get_inductance('+loop0', num_loop_turns, tank_turn_current)
        print(quantiphy.Quantity(sum(_get_inductance('+tank'+str(i), num_tank_turns, tank_turn_current) for i in range(num_tank_turns))))

    femm.mi_selectgroup(loop_group)
    femm.mi_movetranslate(0,-loop_y_shift)
    femm.mi_clearselected()
    femm.mi_selectgroup(tank_group)
    femm.mi_movetranslate(0,-tank_y_shift)
    femm.mi_clearselected()
    return Inductances(L11, M12, M21, L22)

def main():
    loop_group = 1
    tank_group = 2

    tank_coil_current_target = 10 # A
    loop_current_target = 10 # A
    trace_thickness = .03556 # mm

    neg_x_dim = 0
    pos_x_dim = 250
    neg_y_dim = -20
    pos_y_dim = 30

    # Init FEMM
    femm.openfemm()
    femm.newdocument(0)
    femm.mi_probdef(6780000, 'millimeters', 'axi', 1e-12, 0, 90)
    femm.mi_getmaterial('Air')
    femm.mi_getmaterial('Copper')

    # Setup circuit properties
    # TODO
    #  - Should be able to remove -loop/+loop (just have "loop") now that axisymmetric
    #  - Series or parallel?
    #  - Set currents here and rebuild from scratch each time?
    # femm.mi_addcircprop('+loop', 0, 1)
    # femm.mi_addcircprop('-loop', 0, 1)
    # femm.mi_addcircprop('+tank', 0, 1)
    # femm.mi_addcircprop('-tank', 0, 1)
    for i in range(1):
        femm.mi_addcircprop('+loop'+str(i), 0, 0)
        femm.mi_addcircprop('-loop'+str(i), 0, 0)
    for i in range(5):
        femm.mi_addcircprop('+tank'+str(i), 0, 0)
        femm.mi_addcircprop('-tank'+str(i), 0, 0)

    # Label the Air
    femm.mi_addblocklabel(100, -200)
    femm.mi_selectlabel(100, -200)
    femm.mi_setblockprop('Air', 1, 1, 0)
    femm.mi_clearselected()

    # Fancy bounding box
    femm.mi_makeABC(4, 400, 0,0)

    femm.mi_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 75*pos_y_dim)

    tank_turn_radii = []

    named_configs = [
        CoilSet(
            config_name='225-35 RevA, 215-02 RevD',

            loop_offset=0,
            loop_r=50,
            num_loop_turns=1,
            num_parallel_loop_traces=1,
            num_loop_layers=1,
            loop_trace_spacing=9999999999,
            loop_turn_spacing=9999999999,
            loop_trace_width=2.5,
            loop_PCB_thickness=1.6,

            tank_center_r=76.152,
            num_tank_turns=5,
            num_parallel_tank_traces=1,
            num_tank_layers=1,
            tank_trace_spacing=9999999999,
            tank_turn_spacing=4.079,
            tank_trace_width=3.175,
            tank_PCB_thickness=1.6,
        )
    ]

    for coilset in named_configs:
        # Draw tank
        tank_turn_radii = _get_winding_radii_turns_and_parallel_traces(coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_width, coilset.tank_trace_spacing, coilset.tank_turn_spacing)
        tank_turn_radii.sort()
        _draw_and_plot_coil_traces(tank_turn_radii, trace_thickness, coilset.tank_trace_width, trace_thickness, 'tank', tank_group)
        femm.mi_selectrectangle(0, trace_thickness+.1, tank_turn_radii[-1]+coilset.tank_trace_width+10, -coilset.tank_PCB_thickness-trace_thickness-.2, 4)
        femm.mi_setgroup(tank_group)
        femm.mi_clearselected()

        input("pause")

        # Draw loop
        loop_turn_radii = _get_winding_radii_turns_and_parallel_traces(coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_width, coilset.loop_trace_spacing, coilset.loop_turn_spacing)
        loop_turn_radii.sort()
        _draw_and_plot_coil_traces(loop_turn_radii, -coilset.tank_PCB_thickness-coilset.loop_offset, coilset.loop_trace_width, trace_thickness, 'loop',  loop_group)
        femm.mi_selectrectangle(0, -coilset.tank_PCB_thickness-coilset.loop_offset, loop_turn_radii[-1]+coilset.loop_trace_width+10, -coilset.tank_PCB_thickness-coilset.loop_offset-coilset.loop_PCB_thickness-(trace_thickness*2)-.2, 4)
        femm.mi_setgroup(loop_group)

        input("pause")

        # Currents through traces
        tank_traces_per_turn = coilset.num_tank_layers * coilset.num_parallel_tank_traces
        total_tank_trace_count = tank_traces_per_turn * coilset.num_tank_turns
        tank_coil_current = tank_coil_current_target * coilset.num_tank_turns
        tank_individual_trace_current = tank_coil_current / total_tank_trace_count # True with tank circuit set to parallel
        tank_turn_current = tank_individual_trace_current * tank_traces_per_turn

        loop_traces_per_turn = coilset.num_loop_layers * coilset.num_parallel_loop_traces
        total_loop_trace_count = coilset.num_loop_turns * loop_traces_per_turn
        loop_current = loop_current_target * coilset.num_loop_turns
        loop_individual_trace_current = loop_current / total_loop_trace_count # True with loop circuit set to parallel
        loop_turn_current = loop_individual_trace_current * loop_traces_per_turn

        # Self-inductances with nothing nearby
        loop_inductances = _get_inductances(loop_group, loop_turn_current, 0, coilset.num_loop_turns, tank_group, 0, 300, coilset.num_tank_turns)
        L_loop_solo = loop_inductances.L11

        input("pause")

        tank_inductances = _get_inductances(loop_group, 0, 300, coilset.num_loop_turns, tank_group, tank_turn_current, 0, coilset.num_tank_turns)
        L_tank_solo = tank_inductances.L22

        # print(quantiphy.Quantity(L_loop_solo))
        # print(quantiphy.Quantity(L_tank_solo))

if __name__ == '__main__':
    main()
