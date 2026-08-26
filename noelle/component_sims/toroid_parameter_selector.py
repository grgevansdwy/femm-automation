from dataclasses import dataclass, fields
import numpy as np
import ferrite_and_core_info as fci


inductance_target = .9e-6
inductance_tolerance = .1
turn_limit = 16
max_stacks = 6
max_height = 40 #mm
wire_width = 5 #mm

mu0 = 0.00000125663706127


toroid_cores = []
for material_ferrite in fci.ferrites:
    for core in fci.cores:
        for num_turns in range(1,turn_limit):
            for stack_num in range(1,max_stacks):
                inductance = (mu0*material_ferrite.mu_i*(num_turns**2)*(core.h*0.001)*stack_num*np.log(core.od/core.id))/(2*np.pi)
                if ((inductance > inductance_target*(1-inductance_tolerance)) and (inductance <  inductance_target*(1+inductance_tolerance)) and (num_turns*wire_width <= (np.pi*core.id)) and (core.h*stack_num <= max_height)):
                    print(f'L:{inductance} : {core.name}{material_ferrite.name} turns:{num_turns} stacks: {stack_num}')




