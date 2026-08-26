# Wound Copper Coil with an Iron Core
# David Meeker
# dmeeker@ieee.org
#  
# This program consider an axisymmetric magnetostatic problem
# of a cylindrical coil with an axial length of 100 mm, an
# inner radius of 50 mm, and an outer radius of 100 mm.  The
# coil has 200 turns and the coil current is 20 Amps. There is
# an iron bar 80 mm long with a radius of 10 mm centered co-
# axially with the coil.  The objective of the analysis is to
# determine the flux density at the center of the iron bar,
# and to plot the field along the r=0 axis. This analysis
# defines a nonlinear B-H curve for the iron and employs an
# asymptotic boundary condition to approximate an "open"
# boundary condition on the edge of the solution domain.
  
import femm
import matplotlib.pyplot as plt
import numpy as np
import random
from quantiphy import Quantity
import noel_FEMM_functions as nff



#specified tank properties
#tank_inner_r = 61.928 #mm
tank_outer_r = 90.376 #mm
tank_coil_current = 10 #A
num_tank_turns = 5


#specified loop properties
#loop_r = 90 #mm
loop_current = 20 #A
#loop_offset = 5 #mm
num_loop_turns = 1

#specified receive coil properties
rc_inner_r = 51.816 #mm
rc_outer_r = 63.5 #mm
num_rc_turns = 3
rc_trace_width = 2.54 #mm
rc_trace_spacing = 3.302 #mm
rc_distance = 10 #mm
rc_test_current = 10 #A

#General PCB properties
trace_width = 3.175 #mm
trace_spacing = 3.937 #mm
trace_thickness = 1.6 #mm #.07mm is 2oz
PCB_thickness = 0 #mm #1.6mm is standard

#Ferrite Properties
ferrite_bar_length = 115#mm #63mm - existing
ferrite_bar_thickness = 6 #mm
ferrite_inner_r = 5 #mm
ferrite_y_distance = 10 #mm

#Backplane properties
backplane_length = 300 #mm
backplane_thickness = 1 #mm
backplane_y_distance = 60 #mm





# The package must be initialized with the openfemm command.
femm.openfemm();
    
# We need to create a new Magnetostatics document to work on.
femm.newdocument(0);

# Define the problem type.  Magnetostatic; Units of mm; Axisymmetric; 
# Precision of 10^(-8) for the linear solver; a placeholder of 0 for 
# the depth dimension, and an angle constraint of 30 degrees
femm.mi_probdef(6780000 , 'millimeters', 'planar', 1.e-8, 1, 30);

#FEMM Materials and Circuits 
femm.mi_getmaterial('Air')
femm.mi_getmaterial('Copper')

mu = 1   
femm.mi_addmaterial('Ferrite', mu, mu, 0, 0, 0, 0, 0, 1, 0, 0, 0);


femm.mi_addcircprop('+loop', 0, 1)
femm.mi_addcircprop('-loop', 0, 1)

femm.mi_addcircprop('+rc_test', 0, 1)
femm.mi_addcircprop('-rc_test', 0, 1)

femm.mi_addcircprop('+tank', tank_coil_current, 1)
femm.mi_addcircprop('-tank', -tank_coil_current, 1)

#Boundary & Boundary Conditions

femm.mi_addblocklabel(-300, -300)
femm.mi_selectlabel(-300, -300);
femm.mi_setblockprop('Air', 1, 1, 0);
femm.mi_clearselected()

#Use a fancy bounding box
#femm.mi_makeABC(4, 600, 0,0)

#Use Basic Bounding box
femm.mi_drawrectangle(-500, -500, 500, 500);
femm.mi_addboundprop('A=0', 0, 0, 0)
femm.mi_selectsegment(500, 0)
femm.mi_setsegmentprop('A=0', 1, 0,0,0)
femm.mi_clearselected()

femm.mi_selectsegment(-500, 0)
femm.mi_setsegmentprop('A=0', 1, 0,0,0)
femm.mi_clearselected()

femm.mi_selectsegment(0, 500)
femm.mi_setsegmentprop('A=0', 1, 0,0,0)
femm.mi_clearselected()

femm.mi_selectsegment(0, -500)
femm.mi_setsegmentprop('A=0', 1, 0,0,0)
femm.mi_clearselected()

#distances = [1.6, 10, 20, 30, 40, 50, 100, 150, 200]
#ferrite_lengths = [63, 90, 120]
#inner_radii = [0, 15, 30, 63]

distances = [0]
ferrite_thicknesses = [5]
permeabilities = [120]
ferrite_outer_radii = [100]
wall_heights = [0]
wall_y_distances = [0] 
ferrite_gaps = [0] #mm
num_discrete_ferrites = [1]
loop_order_position = [0, 1, 2, 3, 4, 5]
#loop_radii = [100]
#loop_offsets = [1.6]

loop_config = 'trace' #trace or wire

#only used if loop_config is 'wire'
wire_diameter = 1.628 #mm   

num_configs = len(distances)*len(ferrite_thicknesses)*len(permeabilities)*len(ferrite_gaps)*len(ferrite_outer_radii)*len(wall_heights)*len(wall_y_distances)*len(num_discrete_ferrites)*len(loop_order_position)

cm = plt.get_cmap('gist_rainbow')


colors = []
configs = []
ferrite_bs = []
tank_turn_radii = []
k12s = []
k13s = []
k23s = []
L_rcs = []
L_loops = []
L_tanks = []

fig, axs = plt.subplots(2, 2)
top = axs[0,0]
bot = axs[1,0]
top_right = axs[0,1]
bot_right = axs[1,1]

#fig, axes = plt.subplots(nrows=2, ncols=2, sharex=True, sharey=False, gridspec_kw={'height_ratios':[2,2,1,1]})
run_count = 0

for mu in permeabilities:
    for ferrite_thicc in ferrite_thicknesses:
        for f_y in distances:
            for ferrite_gap in ferrite_gaps:
                for ferrite_num in num_discrete_ferrites:
                    for ferrite_outer_r in ferrite_outer_radii:
                        for wall_height in wall_heights:
                            for wall_y_distance in wall_y_distances:
                                for loop_position in loop_order_position:


                                

                                    run_count+=1
                                    #modify the ferrite to have the x,y permeability for this run
                                    femm.mi_modifymaterial('Ferrite', 1, mu)
                                    femm.mi_modifymaterial('Ferrite', 2, mu)

                                    ferrite_y_distance = f_y+PCB_thickness+trace_thickness+.1
                                    #ferrite_inner_r = int(bar_width / (2*np.tan(np.pi/ferrite_qty)))
                                    #if (ferrite_inner_r > tank_inner_r):
                                    #   print(ferrite_qty)
                                    #  print(bar_width)
                                    # break;
                                    ferrite_bar_thickness = ferrite_thicc
                                    #ferrite_bar_length = 100 - ferrite_inner_r


                                    #DRAWING RC
                                    if(run_count == 1):
                                        rc_turn_radii = nff.get_winding_radii(rc_inner_r, rc_outer_r, num_rc_turns)
                                        nff.draw_coil_traces(rc_turn_radii, rc_distance, rc_trace_width, trace_thickness, 'planar', 'trace', 'rc_test')
                                    
                                    total_pcb_turns = num_loop_turns + num_tank_turns
                                    turns_inner_radius = tank_outer_r - (total_pcb_turns*(trace_width+trace_spacing))

                                    aggregate_turn_radii = nff.get_winding_radii(turns_inner_radius, tank_outer_r, total_pcb_turns)
                                    tank_turn_radii = []
                                    #DRAWING TANK 
                                    if (loop_position > 0):
                                        inner_tank_turns = aggregate_turn_radii[0:loop_position]              
                                        nff.draw_coil_traces(inner_tank_turns, trace_thickness, trace_width, trace_thickness, 'planar', 'trace', 'tank')
                                        tank_turn_radii+=inner_tank_turns
                                    #DRAWING LOOP
                                    loop_turn_radii = aggregate_turn_radii[loop_position:loop_position+num_loop_turns]
                                    nff.draw_coil_traces(loop_turn_radii, trace_thickness, trace_width, trace_thickness, 'planar', 'trace', 'loop')
                                    #DRAWING TANK 
                                    if (loop_position < len(aggregate_turn_radii)):
                                        outer_tank_turns = aggregate_turn_radii[loop_position+num_loop_turns:]      
                                        nff.draw_coil_traces(outer_tank_turns, trace_thickness, trace_width, trace_thickness, 'planar', 'trace', 'tank')
                                        tank_turn_radii+=outer_tank_turns

                                    
                                

                                    #ferrite generation
                                    if (ferrite_num > 0):
                                        ferrite_bar_length = ((2*ferrite_outer_r)-((ferrite_num-1)*ferrite_gap))/ferrite_num 
                                        for nb in range(ferrite_num):
                                            femm.mi_drawrectangle(-ferrite_outer_r+(nb*(ferrite_bar_length+ferrite_gap)), -ferrite_y_distance, -ferrite_outer_r+((nb+1)*ferrite_bar_length)+(nb*ferrite_gap),-ferrite_y_distance-ferrite_bar_thickness)
                                            femm.mi_addblocklabel(-ferrite_outer_r+((nb+.5)*ferrite_bar_length)+(nb*ferrite_gap),-ferrite_y_distance-(ferrite_bar_thickness/2))
                                            femm.mi_selectlabel(-ferrite_outer_r+((nb+.5)*ferrite_bar_length)+(nb*ferrite_gap),-ferrite_y_distance-(ferrite_bar_thickness/2))
                                            femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                            femm.mi_clearselected()


                                        # #Right Ferrite
                                        # femm.mi_drawrectangle(ferrite_inner_r, -ferrite_y_distance, ferrite_inner_r+ferrite_bar_length, -ferrite_y_distance-ferrite_bar_thickness);
                                        # femm.mi_addblocklabel(ferrite_inner_r+(ferrite_bar_length/2), -ferrite_y_distance-(ferrite_bar_thickness/2));
                                        # femm.mi_selectlabel(ferrite_inner_r+(ferrite_bar_length/2), -ferrite_y_distance-(ferrite_bar_thickness/2));
                                        # femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                        # femm.mi_clearselected()
                                            

                                        #Right wall Ferrite
                                        if (wall_height > 0):
                                            femm.mi_drawrectangle(ferrite_outer_r, -ferrite_y_distance+.1+wall_y_distance, ferrite_outer_r-ferrite_bar_thickness, -ferrite_y_distance+wall_y_distance+.1+wall_height)
                                            femm.mi_addblocklabel(ferrite_outer_r-.1, -ferrite_y_distance+wall_y_distance+.1+(wall_height/2));
                                            femm.mi_selectlabel(ferrite_outer_r-.1, -ferrite_y_distance+wall_y_distance+.1+(wall_height/2));
                                            femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                            femm.mi_clearselected()

                            
                                        # #Left Ferrite
                                        # femm.mi_drawrectangle(-ferrite_inner_r, -ferrite_y_distance, -ferrite_inner_r-ferrite_bar_length, -ferrite_y_distance-ferrite_bar_thickness);
                                        # femm.mi_addblocklabel(-ferrite_inner_r-(ferrite_bar_length/2), -ferrite_y_distance-(ferrite_bar_thickness/2));
                                        # femm.mi_selectlabel(-ferrite_inner_r-(ferrite_bar_length/2), -ferrite_y_distance-(ferrite_bar_thickness/2));
                                        # femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                        # femm.mi_clearselected()


                                        #Left Wall Ferrite
                                        if (wall_height > 0):
                                            femm.mi_drawrectangle(-ferrite_outer_r, -ferrite_y_distance+.1+wall_y_distance, -ferrite_outer_r+ferrite_bar_thickness, -ferrite_y_distance+wall_y_distance+.1+wall_height)
                                            femm.mi_addblocklabel(-ferrite_outer_r-.1, -ferrite_y_distance+wall_y_distance+.1+(wall_height/2));
                                            femm.mi_selectlabel(-ferrite_outer_r-.1, -ferrite_y_distance+wall_y_distance+.1+(wall_height/2));
                                            femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                            femm.mi_clearselected()
                                    


                                    ##Copper Backplane
                                    #femm.mi_drawrectangle(-(backplane_length/2), -backplane_y_distance, (backplane_length/2), -backplane_y_distance-backplane_thickness);
                                    #femm.mi_addblocklabel(0, -backplane_y_distance-(backplane_thickness/2));
                                    #femm.mi_selectlabel(0, -backplane_y_distance-(backplane_thickness/2));
                                    #femm.mi_setblockprop('Copper', 1, 1, 0);
                                    #femm.mi_clearselected()
                                    
                                    #Drawing    ^
                                    #------------------------------------------------------------------------------------------------------------#
                                    #Simulation v

                                    #measure rc inductance
                                    if(run_count == 1):
                                        femm.mi_modifycircprop('+loop', 1, 0)
                                        femm.mi_modifycircprop('-loop', 1, 0)
                                        femm.mi_modifycircprop('+tank', 1, 0)
                                        femm.mi_modifycircprop('-tank', 1, 0)
                                        femm.mi_modifycircprop('+rc_test', 1, rc_test_current)
                                        femm.mi_modifycircprop('-rc_test', 1, -rc_test_current)

                                        femm.mi_zoomnatural()
                                        femm.mi_saveas('coil.fem');
                                        femm.mi_analyze()
                                        femm.mi_loadsolution()


                                        #Finding rc self inductance
                                        total_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii, rc_distance)
                                        L_rc = total_rc_flux / rc_test_current
                                        L_rc_display = Quantity(L_rc, 'H')
                                        L_rcs.append(L_rc)
                                        print('L_rc: ' + str(L_rc_display))
                                        #input("Press Enter to continue...")
                                        femm.mi_selectrectangle(-rc_outer_r-rc_trace_width-3, rc_distance-2, rc_outer_r+2, rc_distance+rc_trace_width+3, 4)
                                        femm.mi_deleteselected()



                                
                                    #first real case: just tank current
                                    femm.mi_modifycircprop('+loop', 1, 0)
                                    femm.mi_modifycircprop('-loop', 1, 0)
                                    femm.mi_modifycircprop('+tank', 1, tank_coil_current)
                                    femm.mi_modifycircprop('-tank', 1, -tank_coil_current)
                                    femm.mi_modifycircprop('+rc_test', 1, 0)
                                    femm.mi_modifycircprop('-rc_test', 1, 0)

                                    #display and save
                                    femm.mi_zoomnatural()
                                    femm.mi_saveas('coil.fem')
                                    #input("Press Enter to continue...")
                                    
                                    femm.mi_analyze()
                                    femm.mi_loadsolution()

                                    #Finding Tank self inductance
                                    total_tank_flux = nff.loop_flux_integral_planar(tank_turn_radii, 0)
                                    L_tank = total_tank_flux / tank_coil_current
                                    L_tanks.append(L_tank)
                                    L_tank_display = Quantity(L_tank, 'H')
                                    print('L_tank: ' + str(L_tank_display))
                                    #input("Press Enter to continue...")

                                    #Finding drive loop mutual inductance
                                    induced_loop_flux = nff.loop_flux_integral_planar(loop_turn_radii,0)
                                    M21 = induced_loop_flux / tank_coil_current
                                    print('M21: ' + str(M21))
                                    #input("Press Enter to continue...")

                                    #Finding RC mutual inductance with tank
                                    induced_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii,20)
                                    M23 = induced_rc_flux / tank_coil_current
                                    print('M23: ' + str(M23))
                                    k23 = M23 / (np.sqrt(L_tank*L_rc))
                                    k23s.append(k23)
                                    print("k23 @ 20mm: " + str(k23))

                                    #Finding RC mutual inductance with tank
                                    k23_tests = []
                                    rc_test_distances = range(10, 100, 10)

                                    for rc_test_distance in rc_test_distances:    
                                        induced_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii,rc_test_distance)
                                        M23 = induced_rc_flux / tank_coil_current
                                        #print('M23: ' + str(M23))
                                        k23 = M23 / (np.sqrt(L_tank*L_rc))
                                        k23_tests.append(k23)
                                    


                                    #second test case: just loop current
                                    femm.mi_modifycircprop('+loop', 1, loop_current)
                                    femm.mi_modifycircprop('-loop', 1, -loop_current)
                                    femm.mi_modifycircprop('+tank', 1, 0)
                                    femm.mi_modifycircprop('-tank', 1, 0)
                                    femm.mi_modifycircprop('+rc_test', 1, 0)
                                    femm.mi_modifycircprop('-rc_test', 1, 0)

                                    femm.mi_zoomnatural()
                                    femm.mi_saveas('coil.fem')
                                    femm.mi_analyze()
                                    femm.mi_loadsolution()
                                    femm.mo_clearcontour()

                                    #Finding drive loop self inductance
                                    loop_flux = nff.loop_flux_integral_planar(loop_turn_radii, -PCB_thickness)
                                    L_loop = loop_flux / loop_current
                                    L_loop_display = Quantity(L_loop, 'H')
                                    L_loops.append(L_loop)
                                    print('L_loop: ' + str(L_loop_display))
                                    #input("Press Enter to continue...")


                                    #Finding tank mutual inductance as induced by the drive coil
                                    induced_tank_flux = nff.loop_flux_integral_planar(tank_turn_radii, 0)
                                    M12 = induced_tank_flux / loop_current
                                    print('M12: ' + str(M12))
                                    k12 = M12 / (np.sqrt(L_loop*L_tank))
                                    k12s.append(k12)
                                    print("k12: " + str(k12))

                                    #Finding RC mutual inductance with loop
                                    induced_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii,20)
                                    M13 = induced_rc_flux / loop_current
                                    print('M13: ' + str(M23))
                                    k13 = M13 / (np.sqrt(L_loop*L_rc))
                                    k13s.append(k13)
                                    print('k13 @20mm: ' + str(k13))


                                    femm.mi_modifycircprop('+loop', 1, loop_current)
                                    femm.mi_modifycircprop('-loop', 1, -loop_current)

                                    femm.mi_modifycircprop('+tank', 1, tank_coil_current)
                                    femm.mi_modifycircprop('-tank', 1, -tank_coil_current)

                                    femm.mi_analyze()
                                    femm.mi_loadsolution()
                                    
                                    #input("Press Enter to continue...")



                                    bx, by = femm.mo_getb(aggregate_turn_radii[loop_position], -(ferrite_y_distance+ferrite_bar_thickness/10))
                        
                        
                                    #upper_b = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                    upper_b = .0005
                                    #femm.mo_showdensityplot(1, 0, upper_b, 0, 'bmag')
                        
                                    config = 'qty:'+str(ferrite_num)+'-'+str(int(ferrite_bar_length))+'x'+str(ferrite_bar_thickness)+'_z'+str(int(ferrite_y_distance))+'_r'+str(int(ferrite_outer_r))+'_u'+str(mu)
                                    plot_color = cm(1.*run_count/num_configs)
                                    colors.append(plot_color)
                                    configs.append(config)
                                    flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                    if (ferrite_num == 0):
                                        flux_density = 0
                                    ferrite_bs.append(flux_density)
                                    #ferrite_bs.append(flux_density*flux_concentration_factor) #flux density * the flux concentration due to imperfect ferrite coverage
                                    
                                    femm.mo_savebitmap('img/ferrite_'+config+'.bmp')
                        
                                    zee=[]
                                    bee=[]
                                    for n100 in range(-1500,1510):
                                        n = n100/10
                                        bx,by=femm.mo_getb(0,n);
                                        flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                        zee.append(n)   
                                        bee.append(flux_density);       
                                    top.plot(zee,bee, label=config, color=plot_color)
                                    
                                    ferrite_end = -(ferrite_y_distance+ferrite_bar_thickness)
                                    #print(ferrite_end)

                                    bx,by=femm.mo_getb(0,ferrite_end);
                                    flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                    
                                    top.plot([ferrite_end],[flux_density], "D", color=plot_color)

                                    top_right.plot(rc_test_distances, k23_tests, label=config, color=plot_color)

                                    
                                    femm.mi_selectrectangle(-280, -280,280,280,4)
                                    femm.mi_deleteselected()
                                    femm.mi_clearselected()



top.set_xlabel('Z distance; X=0 (mm)')
top.set_ylabel('Flux Density (T)')
top.set_title('Plot of flux density along the z-axis')
top.legend()

#Set k23 vs distance chart
top_right.set_xlabel('Z distance; X=0 (mm)')
top_right.set_ylim(0, 1)
top_right.set_title('RC to TC-resonant tank coupling coefficient (k23) vs RC distance')
top_right.legend()

#Saturation Current Check Chart 
bot.set_title('Max flux density within ferrite')
p = bot.bar(configs, ferrite_bs, color=colors)
bot.bar_label(p, fmt='{:.5f}T')
bot.set_xlabel('Ferrite config')
plt.setp(bot.xaxis.get_majorticklabels(), rotation=-45, ha="left" )
bot_right_axis = bot.twinx()
mn, mx = bot.get_ylim()
bot_right_axis.set_ylim(mn*10000, mx*10000) #10000G per T
bot_right_axis.set_ylabel('G')
bot.set_ylabel('Max Flux Density within Ferrite (T)')


#Coupling Coefficient Chart
x = np.arange(num_configs) # the label locations
width = 0.25  # the width of the bars
rects = bot_right.bar(x, k13s, width, label='k13 @ 20mm', color=colors, edgecolor='black')
bot_right.bar_label(rects, padding=3, fmt='k13:{:.2f}')
rects = bot_right.bar(x + (width), k23s, width, label='k23 @ 20mm', color=colors, edgecolor='black')
bot_right.bar_label(rects, padding=3, fmt='k23:{:.2f}')
rects = bot_right.bar(x + (width*2), k12s, width, label='k12', color=colors, edgecolor='black')
bot_right.bar_label(rects, padding=3, fmt='k12:{:.2f}')

bot_right.set_title('Coupling coefficients by config')
bot_right.set_xticks(x + width, configs)
bot_right.legend(loc='upper left', ncols=3)
bot_right.set_ylim(0, 1)




#top.set_ylabel('Flux Density underneath center turn of coil (T)')

plt.show()



input("Press Enter to continue...")

#
## Draw a rectangle for the coil;
#
#
## Define an "open" boundary condition using the built-in function:
#
#
## Add block labels, one to each the steel, coil, and air regions.
#femm.mi_addblocklabel(5,0);
#femm.mi_addblocklabel(75,0);
#femm.mi_addblocklabel(30,100);
#
## Add some block labels materials properties
#femm.mi_addmaterial('Air', 1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0);
#femm.mi_addmaterial('Coil', 1, 1, 0, 0, 58*0.65, 0, 0, 1, 0, 0, 0);
#femm.mi_addmaterial('LinearIron', 2100, 2100, 0, 0, 0, 0, 0, 1, 0, 0, 0);
#
## A more interesting material to add is the iron with a nonlinear
## BH curve.  First, we create a material in the same way as if we 
## were creating a linear material, except the values used for 
## permeability are merely placeholders.
#
#femm.mi_addmaterial('Iron', 2100, 2100, 0, 0, 0, 0, 0, 1, 0, 0, 0);
#
## A set of points defining the BH curve is then specified.
#bdata = [ 0.,0.3,0.8,1.12,1.32,1.46,1.54,1.62,1.74,1.87,1.99,2.046,2.08]; 
#hdata = [ 0, 40, 80, 160, 318, 796, 1590, 3380, 7960, 15900, 31800, 55100, 79600];
#for n in range(0,len(bdata)):
#	femm.mi_addbhpoint('Iron', bdata[n],hdata[n]);
#
## Add a "circuit property" so that we can calculate the properties of the
## coil as seen from the terminals.
#femm.mi_addcircprop('icoil', 20, 1);
#
## Apply the materials to the appropriate block labels
#femm.mi_selectlabel(5,0);
#femm.mi_setblockprop('Iron', 0, 1, '<None>', 0, 0, 0);
#femm.mi_clearselected()
#
#femm.mi_selectlabel(75,0);
#femm.mi_setblockprop('Coil', 0, 1, 'icoil', 0, 0, 200);
#femm.mi_clearselected()
#
#femm.mi_selectlabel(30,100);
#femm.mi_setblockprop('Air', 0, 1, '<None>', 0, 0, 0);
#femm.mi_clearselected()
#
## Now, the finished input geometry can be displayed.
#femm.mi_zoomnatural()
#
## We have to give the geometry a name before we can analyze it.
#femm.mi_saveas('coil.fem');
#
#
## Now,analyze the problem and load the solution when the analysis is finished
#femm.mi_analyze()
#femm.mi_loadsolution()
#
## If we were interested in the flux density at specific positions, 
## we could inquire at specific points directly:
#b0=femm.mo_getb(0,0);
#print('Flux density at the center of the bar is %g T' % b0[1]);
#b1=femm.mo_getb(0,50);
#print('Flux density at r=0,z=50 is %g T' % b1[1]);
#
## The program will report the terminal properties of the circuit:
## current, voltage, and flux linkage 
#vals = femm.mo_getcircuitproperties('icoil');
#
## [i, v, \[Phi]] = MOGetCircuitProperties["icoil"]
#
## If we were interested in inductance, it could be obtained by
## dividing flux linkage by current
#L = 1000*vals[2]/vals[0];
#print('The self-inductance of the coil is %g mH' % L);
#
## Or we could, for example, plot the results along a line using 
#zee=[]
#bee=[]
#for n in range(-100,101):
#	b=femm.mo_getb(0,n);
#	zee.append(n)
#	bee.append(b[1]);
#
#plt.plot(zee,bee)
#plt.ylabel('Flux Density, Tesla')
#plt.xlabel('Distance along the z-axis, mm')
#plt.title('Plot of flux density along the axis')
#plt.show()
## When the analysis is completed, FEMM can be shut down.
femm.closefemm()

