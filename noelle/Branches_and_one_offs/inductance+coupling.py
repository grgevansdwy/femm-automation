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
import datetime
from pathlib import Path



#specified tank properties
tank_inner_r = 20 #mm 61.928
tank_outer_r = 90.376 #mm
tank_coil_current = 10 #A
num_tank_turns = 6


#specified loop properties
loop_r = 90 #mm
loop_current = 20 #A
#loop_offset = 5 #mm

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
trace_thickness = .07 #mm
PCB_thickness = 1.6 #mm

#Ferrite Properties
ferrite_bar_length = 115#mm #63mm - existing
ferrite_bar_thickness = 6 #mm
ferrite_inner_r = 5 #mm
ferrite_y_distance = 10 #mm

#Backplane properties
backplane_length = 200 #mm
backplane_thickness = 1 #mm
backplane_y_distance = 60 #mm

#plot and image size
neg_x_dim = -250
pos_x_dim = 250 

neg_y_dim = -10
pos_y_dim = 10


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
femm.mi_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 15*pos_y_dim)

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
num_discrete_ferrites = [0,1]
loop_radii = [90]
loop_offsets = [1.6]
tank_inner_rs = [61.928]
backplane_y_distances = [115, 10]

loop_config = 'trace' #trace or wire

#only used if loop_config is 'wire'
wire_diameter = 1.628 #mm   

num_configs = len(distances)*len(ferrite_thicknesses)*len(permeabilities)*len(ferrite_gaps)*len(ferrite_outer_radii)*len(wall_heights)*len(wall_y_distances)*len(num_discrete_ferrites)*len(loop_radii)*len(loop_offsets)*len(tank_inner_rs )*len(backplane_y_distances)

cm = plt.get_cmap('gist_rainbow')

timestamp = '{:%d%H%M}'.format(datetime.datetime.today())
Path("./ID"+timestamp).mkdir(parents=True, exist_ok=True)

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

fig, axs = plt.subplots(3, 2)
top = axs[0,0]
bot = axs[1,0]
top_right = axs[0,1]
bot_right = axs[1,1]

gs = axs[2, 0].get_gridspec()

axs[2,0].remove()
axs[2,1].remove()

disp_pane = fig.add_subplot(gs[2:, 0:]) 



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
                                for loop_radius in loop_radii:
                                    for loop_offset in loop_offsets:
                                        for tank_inner_r in tank_inner_rs:
                                            for backplane_y_distance in backplane_y_distances:
                                        
                                                loop_r = loop_radius
                                                
                                                run_count+=1
                                                plot_color = cm(1.*run_count/num_configs)
                                                colors.append(plot_color)

                                                #modify the ferrite to have the x,y permeability for this run
                                                femm.mi_modifymaterial('Ferrite', 1, mu)
                                                femm.mi_modifymaterial('Ferrite', 2, mu)
                                                femm.mi_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 15*pos_y_dim)

                                                ferrite_y_distance = f_y+PCB_thickness+loop_offset+trace_thickness+.1
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
                                                    nff.draw_and_plot_coil_traces(rc_turn_radii, rc_distance, rc_trace_width, trace_thickness, 'planar', 'trace', 'rc_test', disp_pane, plot_color, "")
                                                

                                                #DRAWING TANK                    
                                                tank_turn_radii = nff.get_winding_radii(tank_inner_r, tank_outer_r, num_tank_turns)
                                                #draw_coil_traces(winding_radii, y_coord, winding_width, winding_thickness, planar_or_axisymmetric, wire_or_trace, femm_circuit_name):
                                                nff.draw_and_plot_coil_traces(tank_turn_radii, trace_thickness, trace_width, trace_thickness, 'planar', 'trace', 'tank', disp_pane, plot_color, "-")
                                                
                                                #DRAWING LOOP
                                                nff.draw_and_plot_coil_traces([loop_r], -PCB_thickness-loop_offset, trace_width, trace_thickness, 'planar', 'trace', 'loop', disp_pane, plot_color,  "--")   

                                                
                                            

                                                #ferrite generation
                                                if (ferrite_num > 0):
                                                    ferrite_bar_length = ((2*ferrite_outer_r)-((ferrite_num-1)*ferrite_gap))/ferrite_num 
                                                    for nb in range(ferrite_num):
                                                        neg_x = -ferrite_outer_r+(nb*(ferrite_bar_length+ferrite_gap))
                                                        neg_y = -ferrite_y_distance-ferrite_bar_thickness
                                                        pos_x = -ferrite_outer_r+((nb+1)*ferrite_bar_length)+(nb*ferrite_gap)
                                                        pos_y = -ferrite_y_distance


                                                        femm.mi_drawrectangle(neg_x, pos_y, pos_x,neg_y)
                                                        femm.mi_addblocklabel(-ferrite_outer_r+((nb+.5)*ferrite_bar_length)+(nb*ferrite_gap),-ferrite_y_distance-(ferrite_bar_thickness/2))
                                                        femm.mi_selectlabel(-ferrite_outer_r+((nb+.5)*ferrite_bar_length)+(nb*ferrite_gap),-ferrite_y_distance-(ferrite_bar_thickness/2))
                                                        femm.mi_setblockprop('Ferrite', 1, 1, 0);
                                                        femm.mi_clearselected()

                                                        xs = [neg_x, neg_x, pos_x, pos_x, neg_x]
                                                        ys = [neg_y, pos_y, pos_y, neg_y, neg_y]
                                                        disp_pane.plot(xs, ys, color=plot_color, alpha=0.3, ls="-")


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
                                                


                                                #Copper Backplane
                                                femm.mi_drawrectangle(-(backplane_length/2), -backplane_y_distance, (backplane_length/2), -backplane_y_distance-backplane_thickness);
                                                femm.mi_addblocklabel(0, -backplane_y_distance-(backplane_thickness/2));
                                                femm.mi_selectlabel(0, -backplane_y_distance-(backplane_thickness/2));
                                                femm.mi_setblockprop('Copper', 1, 1, 0);
                                                femm.mi_clearselected()
                                                
                                                #Drawing    ^
                                                #------------------------------------------------------------------------------------------------------------#
                                                #Simulation v
                                                femm.mi_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 15*pos_y_dim)
                                                #measure rc inductance
                                                if(run_count == 1):
                                                    femm.mi_modifycircprop('+loop', 1, 0)
                                                    femm.mi_modifycircprop('-loop', 1, 0)
                                                    femm.mi_modifycircprop('+tank', 1, 0)
                                                    femm.mi_modifycircprop('-tank', 1, 0)
                                                    femm.mi_modifycircprop('+rc_test', 1, rc_test_current)
                                                    femm.mi_modifycircprop('-rc_test', 1, -rc_test_current)

                                                    femm.mi_saveas('coil.fem');
                                                    femm.mi_analyze()
                                                    femm.mi_loadsolution()
                                                    femm.mi_zoom(neg_x_dim, neg_y_dim, pos_x_dim, pos_y_dim)


                                                    #Finding rc self inductance
                                                    total_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii, rc_distance)
                                                    L_rc = total_rc_flux / rc_test_current
                                                    L_rc_display = Quantity(L_rc, 'H')
                                                    L_rcs.append(L_rc)
                                                    
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
                                                femm.mi_saveas('coil.fem')
                                                #input("Press Enter to continue...")
                                                
                                                femm.mi_analyze()
                                                femm.mi_loadsolution()
                                                femm.mi_zoom(neg_x_dim, neg_y_dim, pos_x_dim, pos_y_dim)

                                                #Finding Tank self inductance
                                                total_tank_flux = nff.loop_flux_integral_planar(tank_turn_radii, 0)
                                                L_tank = total_tank_flux / tank_coil_current
                                                L_tanks.append(L_tank)
                                                L_tank_display = Quantity(L_tank, 'H')
                                                #input("Press Enter to continue...")

                                                #Finding drive loop mutual inductance
                                                induced_loop_flux = nff.loop_flux_integral_planar([loop_r],-PCB_thickness-loop_offset)
                                                M21 = induced_loop_flux / tank_coil_current
                                                #nff.print_and_log('M21: ' + str(M21), timestamp)
                                                #input("Press Enter to continue...")

                                                #Finding RC mutual inductance with tank
                                                induced_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii,20)
                                                M23 = induced_rc_flux / tank_coil_current
                                                #nff.print_and_log('M23: ' + str(M23), timestamp)
                                                k23 = M23 / (np.sqrt(L_tank*L_rc))
                                                k23s.append(k23)
                                                

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

                                                
                                                femm.mi_saveas('coil.fem')
                                                femm.mi_analyze()       
                                                femm.mi_loadsolution()
                                                femm.mo_clearcontour()
                                                femm.mi_zoom(neg_x_dim, neg_y_dim, pos_x_dim, pos_y_dim)

                                                #Finding drive loop self inductance
                                                loop_flux = nff.loop_flux_integral_planar([loop_r], -PCB_thickness-loop_offset)
                                                L_loop = loop_flux / loop_current
                                                L_loop_display = Quantity(L_loop, 'H')
                                                L_loops.append(L_loop)
                                                
                                                #input("Press Enter to continue...")


                                                #Finding tank mutual inductance as induced by the drive coil
                                                induced_tank_flux = nff.loop_flux_integral_planar(tank_turn_radii, 0)
                                                M12 = induced_tank_flux / loop_current
                                                #nff.print_and_log('M12: ' + str(M12), timestamp)
                                                k12 = M12 / (np.sqrt(L_loop*L_tank))
                                                k12s.append(k12)
                                                

                                                #Finding RC mutual inductance with loop
                                                induced_rc_flux = nff.loop_flux_integral_planar(rc_turn_radii,20)
                                                M13 = induced_rc_flux / loop_current
                                                #nff.print_and_log('M13: ' + str(M23), timestamp)
                                                k13 = M13 / (np.sqrt(L_loop*L_rc))
                                                k13s.append(k13)

                                                


                                                femm.mi_modifycircprop('+loop', 1, loop_current)
                                                femm.mi_modifycircprop('-loop', 1, -loop_current)

                                                femm.mi_modifycircprop('+tank', 1, tank_coil_current)
                                                femm.mi_modifycircprop('-tank', 1, -tank_coil_current)

                                                femm.mi_analyze()
                                                femm.mi_loadsolution()
                                                femm.mi_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 15*pos_y_dim)
                                                #input("Press Enter to continue...")



                                                bx, by = femm.mo_getb(loop_r, -(ferrite_y_distance+ferrite_bar_thickness/10))
                                    
                                    
                                                #upper_b = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                                upper_b = .0005
                                                #femm.mo_showdensityplot(1, 0, upper_b, 0, 'bmag')
                                    
                                                run_num = "run-" + str(run_count)
                                                configs.append(run_num)
                                                flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                                if (ferrite_num == 0):
                                                    flux_density = 0
                                                ferrite_bs.append(flux_density)
                                                #ferrite_bs.append(flux_density*flux_concentration_factor) #flux density * the flux concentration due to imperfect ferrite coverage

                                                nff.print_and_log("\n\n\n" + run_num+"\n---------------------------------", timestamp)
                                                nff.print_and_log("Ferrite  Thickness: "+str(ferrite_thicc)+"mm, Ferrite Permeability: "+str(mu)+", Ferrite Outer Radius: "+str(ferrite_outer_r)+"mm, Number of Ferrites: "+str(ferrite_num)+", Loop Radius: "+str(loop_radius)+"mm, Loop Y Offset: "+str(loop_offset)+"mm, Tank innermost radius: "+str(tank_inner_r)+"mm, Metal Backplane Y Offset:"+str(backplane_y_distance)+"mm", timestamp)
                                                nff.print_and_log("---------------------------------", timestamp)
                                                nff.print_and_log("Inductances:", timestamp)
                                                nff.print_and_log('L_rc: ' + str(L_rc_display), timestamp)
                                                nff.print_and_log('\nL_loop: ' + str(L_loop_display), timestamp)
                                                nff.print_and_log('L_tank: ' + str(L_tank_display), timestamp)
                                                nff.print_and_log('\nCoupling Coefficients:', timestamp)
                                                nff.print_and_log('k13 @20mm: ' + str(k13), timestamp)
                                                nff.print_and_log("k23 @ 20mm: " + str(k23), timestamp)
                                                nff.print_and_log("k12: " + str(k12), timestamp)




                                                
                                                femm.mo_zoom(neg_x_dim, 15*neg_y_dim, pos_x_dim, 15*pos_y_dim)
                                                femm.mo_savebitmap(timestamp+'/'+timestamp+"_"+run_num+'.bmp')
                                    
                                                zee=[]
                                                bee=[]
                                                for n100 in range(-1500,1510):
                                                    n = n100/10
                                                    bx,by=femm.mo_getb(0,n);
                                                    flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                                    zee.append(n)   
                                                    bee.append(flux_density);
                                                top.plot(zee,bee, label=run_num, color=plot_color)
                                                
                                                ferrite_end = -(ferrite_y_distance+ferrite_bar_thickness)
                                                #print(ferrite_end)

                                                bx,by=femm.mo_getb(0,ferrite_end);
                                                flux_density = np.sqrt((abs(bx)**2)+(abs(by)**2))
                                                
                                                top.plot([ferrite_end],[flux_density], "D", color=plot_color)

                                                top_right.plot(rc_test_distances, k23_tests, label=run_num, color=plot_color)

                                                #input()
                                                femm.mi_selectrectangle(-280, -280,280,280,4)
                                                femm.mi_deleteselected()
                                                femm.mi_clearselected()


fig.suptitle('FEMM Analysis ' + ("{:%m-%d-%y}".format(datetime.datetime.today()))+'\nTest ID: '+timestamp, fontsize=12)

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
plt.setp(bot.xaxis.get_majorticklabels(), rotation=0, ha="left" )
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

disp_pane.set_ylim(neg_y_dim, pos_y_dim)
disp_pane.set_xlim(neg_x_dim, pos_x_dim)



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

