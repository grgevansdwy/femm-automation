import femm
import numpy as np
from dataclasses import dataclass

# CoilSet moved to repository/coilset.py - it is plain data, and keeping it
# here meant reading the coil library dragged in the whole FEMM package.


# Holds the 3x3 matrix of self and mutual inductances, plus each coil's ESR,
# between the loop, tank, and rx coils.
@dataclass
class self_and_mutual_inductance_container():
    L11 : float
    M12 : float
    M13 : float

    M21 : float
    L22 : float
    M23 : float

    M31 : float
    M32 : float
    L33 : float

    R11 : float
    R22 : float
    R33 : float


# Computes a circuit's inductance by summing the real flux linkage over all of its turns and dividing by the driving current.
def loop_inductance(circuit_name, num_turns_of_induced, driving_current):
    reality_correction_factor = 1 # 1.1475 #good ol' fashioned fudge -- do not be fooled by all those 'significant' figures.
    flux_linkage = 0
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        flux_linkage += np.real(femm.mo_getcircuitproperties(circuit_name+str(i))[2])
    L = flux_linkage / driving_current * reality_correction_factor
    #input("pause")
    return L

# Returns a circuit's resistance from the real part of its voltage drop, and prints an ESR estimate derived from the imaginary flux linkage.
def loop_ESR(circuit_name, num_turns_of_induced, driving_current):
    voltage_drop = 0
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        results = femm.mo_getcircuitproperties(circuit_name+str(i))
        #print(results)
        voltage_drop += np.real(results[1])
    R = voltage_drop / driving_current

    imag_L = 0
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        imag_L += np.imag(femm.mo_getcircuitproperties(circuit_name+str(i))[2])
    ESR = np.abs(imag_L) / driving_current * 6.78e6 * np.pi
    print(f"{circuit_name}'s ESR is: {ESR}")
    #input("pause")
    return R

# Prints the solved current, voltage, and flux of every turn in a circuit.
def coil_get_prop(circuit_name, num_turns_of_induced, driving_current):
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        results = femm.mo_getcircuitproperties(circuit_name+str(i))
        print(f"{circuit_name}'s Current:{results[0]}, Voltage: {results[1]}, Flux: {results[2]}")


# Returns the radial center of one winding in an evenly spaced set centered on a given mean radius.
def get_winding_center(winding_index, num_windings_in_set, winding_mean_center, indv_winding_center_to_center_distance):
    return winding_mean_center + ( ( ( (2*winding_index) - (num_windings_in_set-1) ) * indv_winding_center_to_center_distance) / 2)


# Draws a multi-layer coil's copper traces in FEMM, assigns them to circuits and a group.
def draw_coil_traces(central_radius, number_of_turns, number_of_parallel_traces, trace_width, trace_spacing, turn_spacing, top_of_trace, winding_thickness, wire_or_trace, femm_circuit_name, layer_count, pcb_thickness, circuit_group):
    coil_turns = []
    for turn_index in range(number_of_turns):
        traces_in_turn = []
        turn_center = get_winding_center(turn_index, number_of_turns, central_radius, turn_spacing)
        for trace_index in range(number_of_parallel_traces):
            trace_center = get_winding_center(trace_index, number_of_parallel_traces, turn_center, trace_spacing)
            traces_in_turn.append(trace_center)
        coil_turns.append(traces_in_turn)

    layer_coords = [top_of_trace]
    if(layer_count > 1):
       layer_coords.append(top_of_trace-pcb_thickness-winding_thickness)
    if(layer_count > 2):
        layer_spacing = pcb_thickness/(layer_count-1)
        for i in range(1, layer_count-1):
            layer_coords.append(top_of_trace-winding_thickness+(winding_thickness/2)-(i*layer_spacing))
    # if ((planar_or_axisymmetric=='axi') or (planar_or_axisymmetric=='axisymmetric')):
    #     images = [1]
    # else: #planar requires reflecting across the z-axis for full symmetry
    #     images = [-1,1]
    for y_coord in layer_coords:
        for turn_index, turn in enumerate(coil_turns):
            for single_trace in turn: #draw axially symettric windings
                if (wire_or_trace == 'wire'):
                    print("THIS IS BROKEN--needs to select the trace rather than just the label for group properties")
                    arc_x = (turn)
                    arc_y_top = y_coord+winding_thickness/2
                    arc_y_bottom = y_coord-winding_thickness/2
                    femm.mi_drawarc(arc_x, arc_y_top, arc_x, arc_y_bottom, 180, 100)
                    femm.mi_drawarc(arc_x, arc_y_bottom, arc_x, arc_y_top, 180, 100)
                    femm.mi_addblocklabel(turn, y_coord)
                    femm.mi_selectlabel(turn, y_coord)
                elif (wire_or_trace == 'trace'):
                    neg_x = (single_trace - (trace_width/2))
                    pos_x = (single_trace + (trace_width/2))
                    pos_y = y_coord
                    neg_y = y_coord-winding_thickness
                    femm.mi_drawrectangle(neg_x, pos_y, pos_x, neg_y)


                    femm.mi_addblocklabel(single_trace, y_coord- winding_thickness/2)
                    femm.mi_selectlabel(single_trace, y_coord-winding_thickness/2)
                    femm.mi_setblockprop('Copper', 1, 1, femm_circuit_name+str(turn_index), 0, circuit_group)
                    femm.mi_selectrectangle(neg_x,neg_y, pos_x, pos_y, 4)
                    femm.mi_setgroup(circuit_group)
                    femm.mi_clearselected()


    return coil_turns, layer_coords


# Sets the drive current on every turn of the loop, tank, and rx circuits at once.
def set_circuit_currents(loop_current, num_loop_turns, tank_current, num_tank_turns, rc_current, num_rc_turns):
    for i in range(num_loop_turns):
        femm.mi_modifycircprop('loop'+str(i), 1, loop_current)

    for i in range(num_tank_turns):
        femm.mi_modifycircprop('tank'+str(i), 1, tank_current)

    for i in range(num_rc_turns):
        femm.mi_modifycircprop('rc'+str(i), 1, rc_current)

# Sets the drive current on every turn of a single named circuit.
def set_circuit_current(circuit_name, num_turns, target_current):
    for i in range(num_turns):
        femm.mi_modifycircprop(circuit_name+str(i), 1, target_current)


# Moves each coil group to its offset position, solves the model, and returns the full self/mutual inductance matrix before moving the groups back.
def get_inductances(loop_group, loop_turn_current, loop_x_shift, loop_y_shift, num_loop_turns, tank_group, tank_turn_current, tank_x_shift, tank_y_shift, num_tank_turns, rc_group, rc_turn_current, rc_x_shift, rc_y_shift, num_rc_turns):

    set_circuit_currents(loop_turn_current, num_loop_turns, tank_turn_current, num_tank_turns, rc_turn_current, num_rc_turns)

    femm.mi_selectgroup(loop_group)
    femm.mi_movetranslate(loop_x_shift,loop_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(rc_group)
    femm.mi_movetranslate(rc_x_shift,rc_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(tank_group)
    femm.mi_movetranslate(tank_x_shift,tank_y_shift)
    femm.mi_clearselected()

    femm.mi_saveas('tmp.fem')

    try:
        femm.mi_analyze()
        femm.mi_loadsolution()
    except BaseException as error:
        print('Caught this error: ' + repr(error))
        input("Pausing for debug")

    L11 = 0
    M12 = 0
    M13 = 0
    M21 = 0
    L22 = 0
    M23 = 0
    M31 = 0
    M32 = 0
    L33 = 0
    R11 = 0
    R22 = 0
    R33 = 0

    if(loop_turn_current > 0):
        L11 = loop_inductance('loop', num_loop_turns, loop_turn_current)
        M12 = loop_inductance('tank', num_tank_turns, loop_turn_current)
        M13 = loop_inductance('rc', num_rc_turns, loop_turn_current)
        R11 = loop_ESR('loop', num_loop_turns, loop_turn_current)


    if(tank_turn_current > 0):
        M21 = loop_inductance('loop', num_loop_turns, tank_turn_current)
        L22 = loop_inductance('tank', num_tank_turns, tank_turn_current)
        M23 = loop_inductance('rc', num_rc_turns, tank_turn_current)
        R22 = loop_ESR('tank', num_tank_turns, tank_turn_current)


    if(rc_turn_current > 0):
        M31 = loop_inductance('loop', num_loop_turns, rc_turn_current)
        M32 = loop_inductance('tank', num_tank_turns, rc_turn_current)
        L33 = loop_inductance('rc', num_rc_turns, rc_turn_current)
        R33 = loop_ESR('rc', num_rc_turns, rc_turn_current)

    femm.mi_selectgroup(loop_group)
    femm.mi_movetranslate(-loop_x_shift,-loop_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(rc_group)
    femm.mi_movetranslate(-rc_x_shift,-rc_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(tank_group)
    femm.mi_movetranslate(-tank_x_shift,-tank_y_shift)
    femm.mi_clearselected()

    return self_and_mutual_inductance_container(L11, M12, M13, M21, L22, M23, M31, M32, L33, R11, R22, R33)


# Draws a rectangular block of material in FEMM.
def draw_rectangular_material_no_plot(material_str, thickness, width, top_y_coord, coil_group):
    pos_x = width
    pos_y = top_y_coord
    neg_x = .1
    neg_y = top_y_coord - thickness

    x_center = width/2
    y_center = pos_y-(thickness/2)


    femm.mi_drawrectangle(neg_x, pos_y, pos_x,neg_y)
    femm.mi_addblocklabel(x_center, y_center)
    femm.mi_selectlabel(x_center, y_center)
    femm.mi_setblockprop(material_str, 1, 1, 0)
    femm.mi_clearselected()

    if(coil_group > -1):
        femm.mi_selectrectangle(neg_x, pos_y, pos_x,neg_y, 4)
        femm.mi_setgroup(coil_group)
        femm.mi_clearselected()


# Checks one coil's trace spacing, turn spacing, and overall width for geometric self-consistency, returning a reason string and a validity flag.
def check_valid_coil(radius, trace_width, num_turns, turn_spacing, trace_spacing, num_parallel_traces):
    trace_spacing_valid = True
    turn_spacing_valid = True
    coil_width_valid = True
    reason = ""
    if((trace_spacing < trace_width) and num_parallel_traces>1):
        trace_spacing_valid = False
        reason+=f"trace space of {trace_spacing} is less than trace width of {trace_width}, "
    turn_width = ((num_parallel_traces-1)*trace_spacing)+trace_width
    if((turn_spacing < turn_width) and num_turns>1):
            turn_spacing_valid = False
            reason+=f"turn space of {turn_spacing} is less than total turn width of {turn_width}, "
    coil_width = ((num_turns-1)*turn_spacing) + turn_width
    if(radius < coil_width/2):
        coil_width_valid = False
        reason+=f"coil width of {coil_width} is too large for a center radius of {radius}, and so it crosses the y-axis "
    return reason, (trace_spacing_valid and turn_spacing_valid and coil_width_valid)

# Runs check_valid_coil on the loop, tank, and rx coils and returns the combined reason string and validity flag.
def check_valid_coilset(loop_r, num_loop_turns, num_parallel_loop_traces, loop_trace_spacing, loop_turn_spacing, loop_trace_width, tank_center_r, num_tank_turns, num_parallel_tank_traces, tank_trace_spacing, tank_turn_spacing, tank_trace_width,rc_radius, num_rc_turns, num_parallel_rc_traces, rc_trace_spacing, rc_turn_spacing, rc_trace_width):
    loop_str, loop_check = check_valid_coil(loop_r, loop_trace_width, num_loop_turns, loop_turn_spacing, loop_trace_spacing, num_parallel_loop_traces)
    tank_str, tank_check = check_valid_coil(tank_center_r, tank_trace_width, num_tank_turns, tank_turn_spacing, tank_trace_spacing, num_parallel_tank_traces)
    rc_str, rc_check = check_valid_coil(rc_radius, rc_trace_width, num_rc_turns, rc_turn_spacing, rc_trace_spacing, num_parallel_rc_traces)
    error_str = loop_str + tank_str + rc_str
    return error_str, loop_check and tank_check and rc_check

# Opens FEMM, creates an axisymmetric magnetics document, and defines the materials, air region, and bounding box used by every simulation.
def init_femm():
    # The package must be initialized with the openfemm command.
    femm.openfemm(1)
    # We need to create a new Magnetostatics document to work on.
    femm.newdocument(0)
    # Define the problem type.  Magnetostatic; Units of mm; Axisymmetric;
    # Precision of 10^(-8) for the linear solver; a placeholder of 0 for
    # the depth dimension, and an angle constraint of 30 degrees
    femm.mi_probdef(6780000 , 'millimeters', 'axi', 1.e-8, 1, 30)
    #FEMM Materials and Circuits
    femm.mi_getmaterial('Air')
    femm.mi_getmaterial('Copper')
    femm.mi_addmaterial('Ferrite')
    femm.mi_addmaterial('AluFoil')
    femm.mi_modifymaterial('AluFoil', 1, 1)
    femm.mi_modifymaterial('AluFoil', 2, 1)
    femm.mi_modifymaterial('AluFoil', 5, 37.7) #conductivity
    #Boundary & Boundary Conditions
    femm.mi_addblocklabel(100, -200)
    femm.mi_selectlabel(100, -200)
    femm.mi_setblockprop('Air', 1, 1, 0)
    femm.mi_clearselected()
    #Use a fancy bounding box
    femm.mi_makeABC(4, 400, 0,0)
    femm.mi_zoom(0, -100, 100, 100)

# Returns the per-turn current for a coil whose traces and layers are wired in parallel from the target total current.
def get_turn_current(target_current, num_layers, num_parallel_traces, num_turns):
    if(num_turns == 0):
        return 0
    traces_per_turn = num_layers * num_parallel_traces
    total_trace_count = num_parallel_traces * num_layers * num_turns
        # * coilset.num_rc_turns
    individual_trace_current = target_current / total_trace_count #true with loop circuit set to parallel

    return (individual_trace_current * traces_per_turn)

