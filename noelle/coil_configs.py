import femm_model as nff

#Special Configs
###############################
#5-turn V1 HP tank, George Coil (3-turn RC 100mm), 130mm Loop 

Gen3 = nff.CoilSet(config_name='RevA1 with Ferrite \n',
    tank_ferrite_present=1,
    tank_ferrite_y_offset=3.24,
    tank_ferrite_thickness=2,
    tank_mu=184.5,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=1,
    rc_ferrite_y_offset=10,
    rc_ferrite_thickness=1,
    rc_mu=184.5,
    rc_ferrite_outer_r=70,

    backplane=0,
    backplane_y_distance=6,
    #loop
    loop_offset=10,
    loop_r=65,
    num_loop_turns=1,
    num_parallel_loop_traces=5,
    num_loop_layers=2,
    loop_trace_spacing=4.464,
    loop_turn_spacing=0,
    #Tank
    tank_center_r=72.688,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=2,
    tank_trace_spacing=0,
    tank_turn_spacing=7.444,
    #RC
    rc_radius=56,
    num_rc_turns=3,
    num_parallel_rc_traces=1,
    num_rc_layers=2,
    rc_trace_spacing=0,
    rc_turn_spacing=6.475,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,
    tank_trace_width=3.175,
    loop_trace_width=3.175,
    rc_trace_width=3.3,
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.8,
    rc_backplane=0,
    rc_backplane_y_distance=6,
    
    tc_shield=1,
    tc_shield_y_distance=2,
    tc_shield_radius=105,
    tc_shield_thickness=1,

    rc_shield=1,
    rc_shield_y_distance=3,
    rc_shield_radius=60,
    rc_shield_thickness=1,

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)


base_coil = nff.CoilSet(config_name='base_iterative_coil \n',
    tank_ferrite_present=1,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=1,
    tank_mu=184.5,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=1,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=1,
    rc_mu=184.5,
    rc_ferrite_outer_r=70,

    backplane=0,
    backplane_y_distance=100,
    #loop
    loop_offset=10,
    loop_r=65,
    num_loop_turns=1,
    num_parallel_loop_traces=1,
    num_loop_layers=1,
    loop_trace_spacing=3.25,
    loop_turn_spacing=3.25,
    #Tank
    tank_center_r=72.688,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=1,
    tank_trace_spacing=3.25,
    tank_turn_spacing=3.25,
    #RC
    rc_radius=56,
    num_rc_turns=3,
    num_parallel_rc_traces=1,
    num_rc_layers=1,
    rc_trace_spacing=3.25,
    rc_turn_spacing=3.25,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,
    tank_trace_width=3,
    loop_trace_width=3,
    rc_trace_width=3,
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.8,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=1,
    tc_shield_y_distance=3,
    tc_shield_radius=105,
    tc_shield_thickness=1,

    rc_shield=1,
    rc_shield_y_distance=0,
    rc_shield_radius=60,
    rc_shield_thickness=1, 

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)

plated_slot_coil = nff.CoilSet(config_name='plated slots \n',
    tank_ferrite_present=1,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=1,
    tank_mu=184.5,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=1,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=1,
    rc_mu=184.5,
    rc_ferrite_outer_r=70,

    backplane=0,
    backplane_y_distance=100,
    #loop
    loop_offset=10,
    loop_r=36,
    num_loop_turns=2,
    num_parallel_loop_traces=8,
    num_loop_layers=1,
    loop_trace_spacing=2,
    loop_turn_spacing=24,
    #Tank
    tank_center_r=79,
    num_tank_turns=4,
    num_parallel_tank_traces=2,
    num_tank_layers=1,
    tank_trace_spacing=2,
    tank_turn_spacing=7,
    #RC
    rc_radius=29,
    num_rc_turns=6,
    num_parallel_rc_traces=2,
    num_rc_layers=1,
    rc_trace_spacing=2,
    rc_turn_spacing=7,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,
    tank_trace_width=.025,
    loop_trace_width=.025,
    rc_trace_width=.025,
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.6,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=1,
    tc_shield_y_distance=3,
    tc_shield_radius=105,
    tc_shield_thickness=1,

    rc_shield=1,
    rc_shield_y_distance=0,
    rc_shield_radius=60,
    rc_shield_thickness=1, 

    loop_trace_thickness=1.6,
    tank_trace_thickness=1.6,
    rc_trace_thickness=1.6)

#
Gen2_coil = nff.CoilSet(config_name='Gen2 \n',
    tank_ferrite_present=0,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=0,
    tank_mu=1,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=0,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=0,
    rc_mu=1,
    rc_ferrite_outer_r=1,

    backplane=0,
    backplane_y_distance=6,
    #loop
    loop_offset=10,
    loop_r=65,
    num_loop_turns=1,
    num_parallel_loop_traces=5,
    num_loop_layers=2,
    loop_trace_spacing=4.464,
    loop_turn_spacing=0,
    #Tank
    tank_center_r=72.688,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=2,
    tank_trace_spacing=0,
    tank_turn_spacing=7.444,
    #RC
    rc_radius=56,
    num_rc_turns=3,
    num_parallel_rc_traces=1,
    num_rc_layers=2,
    rc_trace_spacing=0,
    rc_turn_spacing=6.457,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,
    tank_trace_width=5.544,
    loop_trace_width=3.175,
    rc_trace_width=3.175,
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.6,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=0,
    tc_shield_y_distance=0,
    tc_shield_radius=0,
    tc_shield_thickness=0,

    rc_shield=0,
    rc_shield_y_distance=0,
    rc_shield_radius=0,
    rc_shield_thickness=0, 

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)

#225-10 + 225-11
TR302_and_OC150 = nff.CoilSet(config_name='300W_OG \n',
    tank_ferrite_present=0,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=0,
    tank_mu=1,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=0,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=0,
    rc_mu=1,
    rc_ferrite_outer_r=1,

    backplane=0,
    backplane_y_distance=6,
    #loop
    loop_offset=0,
    loop_r=50,
    num_loop_turns=1,
    num_parallel_loop_traces=1,
    num_loop_layers=1,
    loop_trace_spacing=0,
    loop_turn_spacing=0,
    #Tank
    tank_center_r=71.818,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=1,
    tank_trace_spacing=0,
    tank_turn_spacing=7.112,
    #RC
    rc_radius=35.5,
    num_rc_turns=4,
    num_parallel_rc_traces=1,
    num_rc_layers=1,
    rc_trace_spacing=0,
    rc_turn_spacing=5.334,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,
    tank_trace_width=3.175,
    loop_trace_width=2.54,
    rc_trace_width=3.175,
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.6,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=0,
    tc_shield_y_distance=0,
    tc_shield_radius=0,
    tc_shield_thickness=0,

    rc_shield=0,
    rc_shield_y_distance=0,
    rc_shield_radius=0,
    rc_shield_thickness=0, 

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)

#225-10 + 225-11
TR302_and_mithril = nff.CoilSet(config_name='50W_mitril \n',
    tank_ferrite_present=0,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=0,
    tank_mu=1,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=0,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=0,
    rc_mu=1,
    rc_ferrite_outer_r=1,

    backplane=0,
    backplane_y_distance=6,
    #loop
    loop_offset=0,
    loop_r=50,
    num_loop_turns=1,
    num_parallel_loop_traces=1,
    num_loop_layers=1,
    loop_trace_spacing=0,
    loop_turn_spacing=0,
    #Tank
    tank_center_r=71.818,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=1,
    tank_trace_spacing=0,
    tank_turn_spacing=7.112,
    #RC
    rc_radius=19.2,
    num_rc_turns=6,
    num_parallel_rc_traces=1,
    num_rc_layers=1,
    rc_trace_spacing=0,
    rc_turn_spacing=3.048,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,

    tank_trace_width=3.175,
    loop_trace_width=2.54,
    rc_trace_width=1.524,

    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.6,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=0,
    tc_shield_y_distance=0,
    tc_shield_radius=0,
    tc_shield_thickness=0,

    rc_shield=0,
    rc_shield_y_distance=0,
    rc_shield_radius=0,
    rc_shield_thickness=0, 

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)

#Reily's YOLO coil
TR302_and_225_000056 = nff.CoilSet(config_name='reilys \n',
    tank_ferrite_present=0,
    tank_ferrite_y_offset=0,
    tank_ferrite_thickness=0,
    tank_mu=1,
    tank_ferrite_outer_r=100,

    rc_ferrite_present=0,
    rc_ferrite_y_offset=0,
    rc_ferrite_thickness=0,
    rc_mu=1,
    rc_ferrite_outer_r=1,

    backplane=0,
    backplane_y_distance=6,
    #loop
    loop_offset=0,
    loop_r=50,
    num_loop_turns=1,
    num_parallel_loop_traces=1,
    num_loop_layers=1,
    loop_trace_spacing=0,
    loop_turn_spacing=0,
    #Tank
    tank_center_r=71.818,
    num_tank_turns=5,
    num_parallel_tank_traces=1,
    num_tank_layers=1,
    tank_trace_spacing=0,
    tank_turn_spacing=7.112,
    #RC
    rc_radius=19.5,
    num_rc_turns=6,
    num_parallel_rc_traces=1,
    num_rc_layers=1,
    rc_trace_spacing=0,
    rc_turn_spacing=2,

    coax_cable=213,
    loop_tune_f=6.85,
    tank_tune_f=6.75,
    rc_tune_f=6.84,

    tank_trace_width=3.175,
    loop_trace_width=2.54,
    rc_trace_width=1,
    
    tank_PCB_thickness=1.6,
    loop_PCB_thickness=1.6,
    rc_PCB_thickness=1.6,
    rc_backplane=0,
    rc_backplane_y_distance=100,
    
    tc_shield=0,
    tc_shield_y_distance=0,
    tc_shield_radius=0,
    tc_shield_thickness=0,

    rc_shield=0,
    rc_shield_y_distance=0,
    rc_shield_radius=0,
    rc_shield_thickness=0, 

    loop_trace_thickness=.07,
    tank_trace_thickness=.07,
    rc_trace_thickness=.07)


seedcoil = nff.CoilSet(config_name='base_iterative_coil \n', tank_ferrite_present=0, tank_ferrite_y_offset=0, tank_ferrite_thickness=0, tank_mu=1, tank_ferrite_outer_r=100, rc_ferrite_present=0, rc_ferrite_y_offset=0, rc_ferrite_thickness=0, rc_mu=1, rc_ferrite_outer_r=1, backplane=0, backplane_y_distance=100, loop_offset=10, loop_r=31.45410922828834, num_loop_turns=2, num_parallel_loop_traces=1, num_loop_layers=1, loop_trace_spacing=3.25, loop_turn_spacing=11.321676596879232, tank_center_r=96.47322052521042, num_tank_turns=9, num_parallel_tank_traces=1, num_tank_layers=1, tank_trace_spacing=3.25, tank_turn_spacing=12.153244983164921, rc_radius=41.365786468616264, num_rc_turns=5, num_parallel_rc_traces=1, num_rc_layers=1, rc_trace_spacing=3.25, rc_turn_spacing=6.03634182473567, coax_cable=213, loop_tune_f=6.85, tank_tune_f=6.75, rc_tune_f=6.84, tank_trace_width=3, loop_trace_width=3, rc_trace_width=3, tank_PCB_thickness=1.6, loop_PCB_thickness=1.6, rc_PCB_thickness=1.8, rc_backplane=0, rc_backplane_y_distance=100, tc_shield=0, tc_shield_y_distance=0, tc_shield_radius=0, tc_shield_thickness=0, rc_shield=0, rc_shield_y_distance=0, rc_shield_radius=0, rc_shield_thickness=0, loop_trace_thickness=0.07, tank_trace_thickness=0.07, rc_trace_thickness=0.07)

def create_iter_base_coil(variables):
    #variables = [loop_turns, tank_turns, rc_turns, loop_r, tank_r, rc_r, loop_turn_spacing, tank_turn_spacing, rc_turn_spacing, loop_trace_width, tank_trace_width, rc_trace_width]
    new_coil = copy_coilset(base_coil)
    new_coil.num_loop_turns = int(variables[0])
    new_coil.num_tank_turns =  int(variables[1])
    new_coil.num_rc_turns =  int(variables[2])
    new_coil.loop_r = variables[3]
    new_coil.tank_center_r = variables[4]
    new_coil.rc_radius = variables[5]
    new_coil.loop_turn_spacing = variables[6]
    new_coil.tank_turn_spacing = variables[7]
    new_coil.rc_turn_spacing = variables[8]
    new_coil.loop_trace_width = variables[9]
    new_coil.tank_trace_width= variables[10]
    new_coil.rc_trace_width = variables[11]

    # new_coil.num_parallel_loop_traces = int(variables[12])
    # new_coil.num_parallel_tank_traces = int(variables[13])
    # new_coil.num_parallel_rc_traces = int(variables[14])
    # new_coil.rc_trace_spacing = variables[15]
    # new_coil.tank_trace_spacing = variables[16]
    # new_coil.rc_trace_spacing = variables[17]

    return new_coil

def copy_coilset(coilset_to_copy):
    new_coil = nff.CoilSet(config_name=coilset_to_copy.config_name,
    tank_ferrite_present= coilset_to_copy.tank_ferrite_present,
    tank_ferrite_y_offset=coilset_to_copy.tank_ferrite_y_offset,
    tank_ferrite_thickness=coilset_to_copy.tank_ferrite_thickness,
    tank_mu=coilset_to_copy.tank_mu,
    tc_loss_angle=coilset_to_copy.tc_loss_angle,
    tank_ferrite_outer_r=coilset_to_copy.tank_ferrite_outer_r,

    rc_ferrite_present=coilset_to_copy.rc_ferrite_present,
    rc_ferrite_y_offset=coilset_to_copy.rc_ferrite_y_offset,
    rc_ferrite_thickness=coilset_to_copy.rc_ferrite_thickness,
    rc_mu=coilset_to_copy.rc_mu,
    rc_loss_angle=coilset_to_copy.rc_loss_angle,
    rc_ferrite_outer_r= coilset_to_copy.rc_ferrite_outer_r,

    backplane=coilset_to_copy.backplane,
    backplane_y_distance=coilset_to_copy.backplane_y_distance,
    #loop
    loop_offset=coilset_to_copy.loop_offset,
    loop_r=coilset_to_copy.loop_r,
    num_loop_turns=coilset_to_copy.num_loop_turns,
    num_parallel_loop_traces=coilset_to_copy.num_parallel_loop_traces,
    num_loop_layers=coilset_to_copy.num_loop_layers,
    loop_trace_spacing=coilset_to_copy.loop_trace_spacing,
    loop_turn_spacing=coilset_to_copy.loop_turn_spacing,
    #Tank
    tank_center_r=coilset_to_copy.tank_center_r,
    num_tank_turns=coilset_to_copy.num_tank_turns,
    num_parallel_tank_traces=coilset_to_copy.num_parallel_tank_traces,
    num_tank_layers=coilset_to_copy.num_tank_layers,
    tank_trace_spacing=coilset_to_copy.tank_trace_spacing,
    tank_turn_spacing=coilset_to_copy.tank_turn_spacing,
    #RC
    rc_radius=coilset_to_copy.rc_radius,
    num_rc_turns=coilset_to_copy.num_rc_turns,
    num_parallel_rc_traces=coilset_to_copy.num_parallel_rc_traces,
    num_rc_layers=coilset_to_copy.num_rc_layers,
    rc_trace_spacing=coilset_to_copy.rc_trace_spacing,
    rc_turn_spacing=coilset_to_copy.rc_turn_spacing,

    coax_cable=coilset_to_copy.coax_cable,
    loop_tune_f=coilset_to_copy.loop_tune_f,
    tank_tune_f=coilset_to_copy.tank_tune_f,
    rc_tune_f=coilset_to_copy.rc_tune_f,
    tank_trace_width=coilset_to_copy.tank_trace_width,
    loop_trace_width=coilset_to_copy.loop_trace_width,
    rc_trace_width=coilset_to_copy.rc_trace_width,
    tank_PCB_thickness=coilset_to_copy.tank_PCB_thickness,
    loop_PCB_thickness=coilset_to_copy.loop_PCB_thickness,
    rc_PCB_thickness=coilset_to_copy.rc_PCB_thickness,
    rc_backplane=coilset_to_copy.rc_backplane,
    rc_backplane_y_distance=coilset_to_copy.rc_backplane_y_distance,
    
    tc_shield=coilset_to_copy.tc_shield,
    tc_shield_y_distance=coilset_to_copy.tc_shield_y_distance,
    tc_shield_radius=coilset_to_copy.tc_shield_radius,
    tc_shield_thickness=coilset_to_copy.tc_shield_thickness,

    rc_shield=coilset_to_copy.rc_shield,
    rc_shield_y_distance=coilset_to_copy.rc_shield_y_distance,
    rc_shield_radius=coilset_to_copy.rc_shield_radius,
    rc_shield_thickness=coilset_to_copy.rc_shield_thickness, 

    loop_trace_thickness=coilset_to_copy.loop_trace_thickness,
    tank_trace_thickness=coilset_to_copy.tank_trace_thickness,
    rc_trace_thickness=coilset_to_copy.rc_trace_thickness)

    return new_coil