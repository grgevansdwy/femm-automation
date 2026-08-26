import femm
import numpy as np
import math
from typing import NamedTuple
from dataclasses import dataclass, fields
import itertools
import coil_configs as cc

@dataclass
class CoilSet():
    config_name : str

    tank_ferrite_present: int
    tank_ferrite_y_offset : float
    tank_ferrite_thickness  : float
    tank_mu  : float
    tank_ferrite_outer_r  : float

    rc_ferrite_present: int
    rc_ferrite_y_offset : float
    rc_ferrite_thickness  : float
    rc_mu  : float
    rc_ferrite_outer_r  : float

    #Backplane
    backplane : int
    backplane_y_distance  : float

    #Loop
    loop_offset  : float
    loop_r  : float
    num_loop_turns : int
    num_parallel_loop_traces : int
    num_loop_layers : int
    loop_trace_spacing  : float
    loop_turn_spacing  : float
    

    #Tank
    tank_center_r  : float
    num_tank_turns : int
    num_parallel_tank_traces : int
    num_tank_layers : int
    tank_trace_spacing  : float
    tank_turn_spacing  : float
  

    #RC
    rc_radius  : float
    num_rc_turns : int
    num_parallel_rc_traces : int
    num_rc_layers : int
    rc_trace_spacing  : float
    rc_turn_spacing  : float
    coax_cable: float

    loop_tune_f : float
    tank_tune_f : float
    rc_tune_f : float

    tank_trace_width  : float
    loop_trace_width : float
    rc_trace_width : float
    
    tank_PCB_thickness : float
    loop_PCB_thickness : float
    rc_PCB_thickness : float
    rc_backplane : int
    rc_backplane_y_distance : float

    tc_shield : int
    tc_shield_y_distance : float
    tc_shield_radius : float
    tc_shield_thickness : float

    rc_shield : int
    rc_shield_y_distance : float
    rc_shield_radius : float
    rc_shield_thickness : float

    loop_trace_thickness: float
    tank_trace_thickness: float
    rc_trace_thickness: float


    rc_loss_angle: float=0.0125 #FJ7 is the default
    tc_loss_angle: float=0.0125 #FJ7 is the default


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



def loop_inductance(circuit_name, num_turns_of_induced, driving_current): 
    reality_correction_factor = 1 # 1.1475 #good ol' fashioned fudge -- do not be fooled by all those 'significant' figures. 
    flux_linkage = 0
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        flux_linkage += np.real(femm.mo_getcircuitproperties(circuit_name+str(i))[2])
    L = flux_linkage / driving_current * reality_correction_factor
    #input("pause")
    return L

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

def coil_get_prop(circuit_name, num_turns_of_induced, driving_current): 
    for i in range(num_turns_of_induced):
        #print(circuit_name+str(i))
        results = femm.mo_getcircuitproperties(circuit_name+str(i))
        print(f"{circuit_name}'s Current:{results[0]}, Voltage: {results[1]}, Flux: {results[2]}")



def get_winding_center(winding_index, num_windings_in_set, winding_mean_center, indv_winding_center_to_center_distance):
    return winding_mean_center + ( ( ( (2*winding_index) - (num_windings_in_set-1) ) * indv_winding_center_to_center_distance) / 2)


def draw_and_plot_coil_traces_test(central_radius, number_of_turns, number_of_parallel_traces, trace_spacing, turn_spacing):
    coil_turns = []
    for turn_index in range(number_of_turns):
        traces_in_turn = []
        turn_center = get_winding_center(turn_index, number_of_turns, central_radius, turn_spacing)
        for trace_index in range(number_of_parallel_traces):
            trace_center = get_winding_center(trace_index, number_of_parallel_traces, turn_center, trace_spacing)
            traces_in_turn.append(trace_center)
        coil_turns.append(traces_in_turn)
    return coil_turns


def draw_and_plot_coil_traces(central_radius, number_of_turns, number_of_parallel_traces, trace_width, trace_spacing, turn_spacing, top_of_trace, winding_thickness, wire_or_trace, femm_circuit_name, plot_name, plot_color, line_style, layer_count, pcb_thickness, circuit_group):
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
    xs = []
    ys = []
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
                    
                    xs = [neg_x, neg_x, pos_x, pos_x, neg_x]
                    ys = [neg_y, pos_y, pos_y, neg_y, neg_y]

                    plot_name.plot(xs, ys, color=plot_color, alpha=0.5, ls=line_style, linewidth=2.0)


                    femm.mi_addblocklabel(single_trace, y_coord- winding_thickness/2)
                    femm.mi_selectlabel(single_trace, y_coord-winding_thickness/2)
                    femm.mi_setblockprop('Copper', 1, 1, femm_circuit_name+str(turn_index), 0, circuit_group)
                    femm.mi_selectrectangle(neg_x,neg_y, pos_x, pos_y, 4)
                    femm.mi_setgroup(circuit_group)
                    femm.mi_clearselected()
                
                

    return coil_turns, layer_coords

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
    xs = []
    ys = []
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
                    
                    xs = [neg_x, neg_x, pos_x, pos_x, neg_x]
                    ys = [neg_y, pos_y, pos_y, neg_y, neg_y]



                    femm.mi_addblocklabel(single_trace, y_coord- winding_thickness/2)
                    femm.mi_selectlabel(single_trace, y_coord-winding_thickness/2)
                    femm.mi_setblockprop('Copper', 1, 1, femm_circuit_name+str(turn_index), 0, circuit_group)
                    femm.mi_selectrectangle(neg_x,neg_y, pos_x, pos_y, 4)
                    femm.mi_setgroup(circuit_group)
                    femm.mi_clearselected()
                
                

    return coil_turns, layer_coords


def print_and_log(str_to_print, timestamp):
    with open('./results/ID'+timestamp+'/log_' + timestamp + '.txt', 'a') as log:
        log.write(str_to_print+'\n')
    print(str_to_print)

def resonant_capacitance(inductance, frequency):
    if (inductance == 0):
        return 0
    c_res = 1/(inductance*((2*np.pi*frequency)**2))
    return c_res

def resonant_frequency(inductance, capacitance):
    if (inductance == 0 or capacitance == 0):
        return 0
    f = 1/(2*np.pi*np.sqrt(inductance*capacitance))
    return f

def rc_ys_offset(rc_ys, offset, original_offset):
    output = []
    for rc_y in rc_ys:
        output.append(rc_y-original_offset+offset)
    return output

def set_circuit_currents(loop_current, num_loop_turns, tank_current, num_tank_turns, rc_current, num_rc_turns):
    for i in range(num_loop_turns):
        femm.mi_modifycircprop('loop'+str(i), 1, loop_current)
    
    for i in range(num_tank_turns):
        femm.mi_modifycircprop('tank'+str(i), 1, tank_current)

    for i in range(num_rc_turns):
        femm.mi_modifycircprop('rc'+str(i), 1, rc_current)

def set_circuit_current(circuit_name, num_turns, target_current):
    for i in range(num_turns):
        femm.mi_modifycircprop(circuit_name+str(i), 1, target_current)


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

    if(loop_turn_current > 0):
        L11 = loop_inductance('loop', num_loop_turns, loop_turn_current)
        M12 = loop_inductance('tank', num_tank_turns, loop_turn_current)
        M13 = loop_inductance('rc', num_rc_turns, loop_turn_current)

    
    if(tank_turn_current > 0):
        M21 = loop_inductance('loop', num_loop_turns, tank_turn_current)
        L22 = loop_inductance('tank', num_tank_turns, tank_turn_current)
        M23 = loop_inductance('rc', num_rc_turns, tank_turn_current)
        coil_get_prop('tank', num_tank_turns, tank_turn_current)
        loop_ESR('tank', num_tank_turns, tank_turn_current)


    if(rc_turn_current > 0):
        M31 = loop_inductance('loop', num_loop_turns, rc_turn_current)
        M32 = loop_inductance('tank', num_tank_turns, rc_turn_current)
        L33 = loop_inductance('rc', num_rc_turns, rc_turn_current)

    femm.mi_selectgroup(loop_group)
    femm.mi_movetranslate(-loop_x_shift,-loop_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(rc_group)
    femm.mi_movetranslate(-rc_x_shift,-rc_y_shift)
    femm.mi_clearselected()

    femm.mi_selectgroup(tank_group)
    femm.mi_movetranslate(-tank_x_shift,-tank_y_shift)
    femm.mi_clearselected()

    return self_and_mutual_inductance_container(L11, M12, M13, M21, L22, M23, M31, M32, L33)


def draw_rectangular_material(material_str, thickness, width, top_y_coord, coil_group, display_panel, plot_color, plot_symbol):
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

    xs = [neg_x, neg_x, pos_x, pos_x, neg_x]
    ys = [neg_y, pos_y, pos_y, neg_y, neg_y]

    y_mean = (neg_y + pos_y) / 2
    y_means = [(y_mean) for y_mean in ys]

    if(material_str == 'Copper'):
        for i in range(int(thickness*10)):
            display_panel.plot([neg_x, pos_x], [pos_y-(i/10), pos_y-(i/10)], color='goldenrod', alpha=0.5, ls='-', linewidth=1) #'-'
    if(material_str == 'AluFoil'):
        for i in range(int(thickness*10)):
            display_panel.plot([neg_x, pos_x], [pos_y-(i/10), pos_y-(i/10)], color='gray', alpha=0.5, ls='-', linewidth=1) #'-'
    elif(material_str == 'Ferrite'):
         for i in range(int(thickness*10)):
            display_panel.plot([neg_x, pos_x], [pos_y-(i/10), pos_y-(i/10)], color='black', alpha=0.5, ls='-', linewidth=1) #'-'

    display_panel.plot(xs, ys, color=plot_color, alpha=0.3, ls=plot_symbol) #'-'

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


def get_peak_flux_density_of_rect(x_limit, y_test):
    max_flux_density = 0
    for x in range(int(x_limit)):
        bx, by = femm.mo_getb(x, y_test)
        flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
        if (flux_density > max_flux_density):
            max_flux_density = flux_density
    return max_flux_density

def get_block_losses(x, y):
    femm.mo_selectblock(x,y)
    #input('pause for block selection')
    losses = femm.mo_blockintegral(6) #total losses
    femm.mo_clearblock()
    return np.abs(losses)

def get_coil_losses(radii, ys, trace_thickness):
    total_losses = 0
    for y in ys:
        for radius in list(itertools.chain(*radii)):
            total_losses += get_block_losses(radius, y - (trace_thickness/2))
    return total_losses
        

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

def check_valid_coilset(loop_r, num_loop_turns, num_parallel_loop_traces, loop_trace_spacing, loop_turn_spacing, loop_trace_width, tank_center_r, num_tank_turns, num_parallel_tank_traces, tank_trace_spacing, tank_turn_spacing, tank_trace_width,rc_radius, num_rc_turns, num_parallel_rc_traces, rc_trace_spacing, rc_turn_spacing, rc_trace_width):
    loop_str, loop_check = check_valid_coil(loop_r, loop_trace_width, num_loop_turns, loop_turn_spacing, loop_trace_spacing, num_parallel_loop_traces)
    tank_str, tank_check = check_valid_coil(tank_center_r, tank_trace_width, num_tank_turns, tank_turn_spacing, tank_trace_spacing, num_parallel_tank_traces)
    rc_str, rc_check = check_valid_coil(rc_radius, rc_trace_width, num_rc_turns, rc_turn_spacing, rc_trace_spacing, num_parallel_rc_traces)
    error_str = loop_str + tank_str + rc_str
    return error_str, loop_check and tank_check and rc_check

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

def get_turn_current(target_current, num_layers, num_parallel_traces, num_turns):
    if(num_turns == 0):
        return 0
    traces_per_turn = num_layers * num_parallel_traces
    total_trace_count = num_parallel_traces * num_layers * num_turns
        # * coilset.num_rc_turns
    individual_trace_current = target_current / total_trace_count #true with loop circuit set to parallel
    
    return (individual_trace_current * traces_per_turn)


