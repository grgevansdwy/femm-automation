# CoilSet moved to repository/coilset.py - it is plain data, and keeping it
# here meant reading the coil library dragged in the whole FEMM package.
#
# NOTE ON BACKENDS: this module no longer imports `femm` at module level -
# the functions below that build geometry/materials/circuits take an explicit
# `femm_like` object instead (the real `femm` module, or a
# services.xfemm_backend.LuaScript). This is what lets check_valid_coilset/
# get_turn_current stay importable even where `femm` (pyfemm's win32com
# dependency) isn't installed, and lets draw_coil_traces()/init_femm() be
# reused by both backends. The functions that used to read live results back
# from FEMM (loop_inductance, loop_ESR, get_inductances, ...) can't be reused
# this way - femmcli only runs a whole script start-to-finish, so there's no
# live value to read mid-script. Those are gone from here; the Lua-side
# equivalent is services/xfemm_backend.py, and the original Python/COM
# versions are preserved unchanged in legacy/wine_backend/femm_service.py.


# Returns the radial center of one winding in an evenly spaced set centered on a given mean radius.
def get_winding_center(winding_index, num_windings_in_set, winding_mean_center, indv_winding_center_to_center_distance):
    return winding_mean_center + ( ( ( (2*winding_index) - (num_windings_in_set-1) ) * indv_winding_center_to_center_distance) / 2)


# Draws a multi-layer coil's copper traces in FEMM, assigns them to circuits and a group.
def draw_coil_traces(femm_like, central_radius, number_of_turns, number_of_parallel_traces, trace_width, trace_spacing, turn_spacing, top_of_trace, winding_thickness, wire_or_trace, femm_circuit_name, layer_count, pcb_thickness, circuit_group):
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
                    femm_like.mi_drawarc(arc_x, arc_y_top, arc_x, arc_y_bottom, 180, 100)
                    femm_like.mi_drawarc(arc_x, arc_y_bottom, arc_x, arc_y_top, 180, 100)
                    femm_like.mi_addblocklabel(turn, y_coord)
                    femm_like.mi_selectlabel(turn, y_coord)
                elif (wire_or_trace == 'trace'):
                    neg_x = (single_trace - (trace_width/2))
                    pos_x = (single_trace + (trace_width/2))
                    pos_y = y_coord
                    neg_y = y_coord-winding_thickness
                    femm_like.mi_drawrectangle(neg_x, pos_y, pos_x, neg_y)


                    femm_like.mi_addblocklabel(single_trace, y_coord- winding_thickness/2)
                    femm_like.mi_selectlabel(single_trace, y_coord-winding_thickness/2)
                    femm_like.mi_setblockprop('Copper', 1, 1, femm_circuit_name+str(turn_index), 0, circuit_group)
                    femm_like.mi_selectrectangle(neg_x,neg_y, pos_x, pos_y, 4)
                    femm_like.mi_setgroup(circuit_group)
                    femm_like.mi_clearselected()


    return coil_turns, layer_coords


# Draws a rectangular block of material in FEMM.
def draw_rectangular_material_no_plot(femm_like, material_str, thickness, width, top_y_coord, coil_group):
    pos_x = width
    pos_y = top_y_coord
    neg_x = .1
    neg_y = top_y_coord - thickness

    x_center = width/2
    y_center = pos_y-(thickness/2)


    femm_like.mi_drawrectangle(neg_x, pos_y, pos_x,neg_y)
    femm_like.mi_addblocklabel(x_center, y_center)
    femm_like.mi_selectlabel(x_center, y_center)
    femm_like.mi_setblockprop(material_str, 1, 1, 0)
    femm_like.mi_clearselected()

    if(coil_group > -1):
        femm_like.mi_selectrectangle(neg_x, pos_y, pos_x,neg_y, 4)
        femm_like.mi_setgroup(coil_group)
        femm_like.mi_clearselected()


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
#
# `femm_like.far_field_boundary()` stands in for the original mi_makeABC(4,
# 400, 0, 0) call - broken under femmcli, see services/xfemm_backend.py.
def init_femm(femm_like):
    # The package must be initialized with the openfemm command.
    femm_like.openfemm(1)
    # We need to create a new Magnetostatics document to work on.
    femm_like.newdocument(0)
    # Define the problem type.  Magnetostatic; Units of mm; Axisymmetric;
    # Precision of 10^(-8) for the linear solver; a placeholder of 0 for
    # the depth dimension, and an angle constraint of 30 degrees
    femm_like.mi_probdef(6780000 , 'millimeters', 'axi', 1.e-8, 1, 30)
    #FEMM Materials and Circuits
    femm_like.mi_getmaterial('Air')
    femm_like.mi_getmaterial('Copper')
    femm_like.mi_addmaterial('Ferrite')
    femm_like.mi_addmaterial('AluFoil')
    femm_like.mi_modifymaterial('AluFoil', 1, 1)
    femm_like.mi_modifymaterial('AluFoil', 2, 1)
    femm_like.mi_modifymaterial('AluFoil', 5, 37.7) #conductivity
    #Boundary & Boundary Conditions
    femm_like.mi_addblocklabel(100, -200)
    femm_like.mi_selectlabel(100, -200)
    femm_like.mi_setblockprop('Air', 1, 1, 0)
    femm_like.mi_clearselected()
    #Use a fancy bounding box
    femm_like.far_field_boundary()
    femm_like.mi_zoom(0, -100, 100, 100)

# Returns the per-turn current for a coil whose traces and layers are wired in parallel from the target total current.
def get_turn_current(target_current, num_layers, num_parallel_traces, num_turns):
    if(num_turns == 0):
        return 0
    traces_per_turn = num_layers * num_parallel_traces
    total_trace_count = num_parallel_traces * num_layers * num_turns
        # * coilset.num_rc_turns
    individual_trace_current = target_current / total_trace_count #true with loop circuit set to parallel

    return (individual_trace_current * traces_per_turn)
