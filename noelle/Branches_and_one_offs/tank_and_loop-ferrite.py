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



#tank properties
tank_inner_r = 61.928 #mm
tank_outer_r = 90.376 #mm
tank_coil_current = 10 #A
num_turns = 5

#loop properties
loop_r = 50 #mm
loop_current = 20 #A


trace_width = 3.175 #mm
trace_spacing = 3.937 #mm
trace_thickness = .07 #mm

ferrite_bar_length = 115#mm #63mm - existing
ferrite_bar_thickness = 6 #mm
ferrite_inner_r = 5 #mm

ferrite_y_distance = 10 #mm

backplane_length = 300 #mm
backplane_thickness = 1 #mm

backplane_y_distance = 60 #mm

PCB_thickness = 1.6 #mm



# The package must be initialized with the openfemm command.
femm.openfemm();
    
# We need to create a new Magnetostatics document to work on.
femm.newdocument(0);

# Define the problem type.  Magnetostatic; Units of mm; Axisymmetric; 
# Precision of 10^(-8) for the linear solver; a placeholder of 0 for 
# the depth dimension, and an angle constraint of 30 degrees
femm.mi_probdef(6780000 , 'millimeters', 'planar', 1.e-8, 0, 30);

#FEMM Materials and Circuits 
femm.mi_getmaterial('Air')
femm.mi_getmaterial('Copper')

mu = 1   
femm.mi_addmaterial('Ferrite', mu, mu, 0, 0, 0, 0, 0, 1, 0, 0, 0);


femm.mi_addcircprop('+loop', loop_current, 1)
femm.mi_addcircprop('-loop', -loop_current, 1)

femm.mi_addcircprop('+tank', tank_coil_current, 1)
femm.mi_addcircprop('-tank', -tank_coil_current, 1)

#Boundary & Boundary Conditions
#femm.mi_drawrectangle(-500, -500, 500, 500);
femm.mi_addblocklabel(-300, -300)
femm.mi_selectlabel(-300, -300);
femm.mi_setblockprop('Air', 1, 1, 0);
femm.mi_clearselected()

femm.mi_makeABC(4, 600, 0,0)
#femm.mi_addboundprop('A=0', 0, 0, 0)

# femm.mi_selectsegment(500, 0)
# femm.mi_setsegmentprop('A=0', 1, 0,0,0)
# femm.mi_clearselected()

# femm.mi_selectsegment(-500, 0)
# femm.mi_setsegmentprop('A=0', 1, 0,0,0)
# femm.mi_clearselected()

# femm.mi_selectsegment(0, 500)
# femm.mi_setsegmentprop('A=0', 1, 0,0,0)
# femm.mi_clearselected()

# femm.mi_selectsegment(0, -500)
# femm.mi_setsegmentprop('A=0', 1, 0,0,0)
# femm.mi_clearselected()

#distances = [1.6, 10, 20, 30, 40, 50, 100, 150, 200]
#ferrite_lengths = [63, 90, 120]
#inner_radii = [0, 15, 30, 63]

distances = [0]
ferrite_thicknesses = [2,5]
permeabilities = [180]
ferrite_outer_radii = [125]
wall_heights = [0]
wall_y_distances = [0] 
ferrite_gaps = [20, 10, 1] #mm
num_discrete_ferrites = [3]

num_configs = len(distances)*len(ferrite_thicknesses)*len(permeabilities)*len(ferrite_gaps)*len(ferrite_outer_radii)*len(wall_heights)*len(wall_y_distances)*len(num_discrete_ferrites)

cm = plt.get_cmap('gist_rainbow')


colors = []
configs = []
ferrite_bs = []

fig, (top, bot) = plt.subplots(2, 1)


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
                            
                                
                                # Draw coil traces;
                                coil_width = tank_outer_r - tank_inner_r
                                radial_increment = coil_width / (num_turns - 1)
                                
                                coil_midpoint = tank_inner_r+(coil_width/2)
                                #ferrite packing efficiency
                                #flux_concentration_factor = 2*np.pi*coil_midpoint / (bar_width * ferrite_qty) #ratio of arc length at coil midpoint to total ferrite width
                                
                    
                                #Tank Coil Turns
                                for turn in range(0,num_turns):
                                    turn_center = (turn*radial_increment)+tank_inner_r
                                    femm.mi_drawrectangle((turn_center - (trace_width/2)), 0, (turn_center + (trace_width/2)), trace_thickness);
                                    femm.mi_addblocklabel(turn_center, trace_thickness/2)
                                    femm.mi_selectlabel(turn_center, trace_thickness/2)
                                    femm.mi_setblockprop('Copper', 1, 1, '+tank');
                                    femm.mi_clearselected()
                                    
                                    femm.mi_drawrectangle((-turn_center - (trace_width/2)), 0, (-turn_center + (trace_width/2)), trace_thickness);
                                    femm.mi_addblocklabel(-turn_center, trace_thickness/2)
                                    femm.mi_selectlabel(-turn_center, trace_thickness/2)
                                    femm.mi_setblockprop('Copper', 1, 1, '-tank');
                                    femm.mi_clearselected()
                                
                                #Loop Traces
                                loop_trace_center = loop_r - (trace_width/2)
                                femm.mi_drawrectangle((loop_trace_center - (trace_width/2)), -PCB_thickness, (loop_trace_center + (trace_width/2)), -PCB_thickness-trace_thickness);
                                femm.mi_addblocklabel(loop_trace_center, -PCB_thickness-trace_thickness/2)
                                femm.mi_selectlabel(loop_trace_center, -PCB_thickness-trace_thickness/2)
                                femm.mi_setblockprop('Copper', 1, 1, '+loop');
                                femm.mi_clearselected()
                                
                                femm.mi_drawrectangle((-loop_trace_center - (trace_width/2)), -PCB_thickness, (-loop_trace_center + (trace_width/2)), -PCB_thickness-trace_thickness);
                                femm.mi_addblocklabel(-loop_trace_center, -PCB_thickness-trace_thickness/2)
                                femm.mi_selectlabel(-loop_trace_center, -PCB_thickness-trace_thickness/2)
                                femm.mi_setblockprop('Copper', 1, 1, '-loop');
                                femm.mi_clearselected()

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
                    

                                #display and save
                                femm.mi_zoomnatural()
                                femm.mi_saveas('coil.fem');
                                #input("Press Enter to continue...")

                                femm.mi_analyze()
                                femm.mi_loadsolution()

                                bx, by = femm.mo_getb(loop_trace_center, -(ferrite_y_distance+ferrite_bar_thickness/10))
                    
                    
                                #upper_b = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                upper_b = .0005
                                femm.mo_showdensityplot(1, 0, upper_b, 0, 'bmag')
                    
                                config = 'qty:'+str(ferrite_num)+'-'+str(int(ferrite_bar_length))+'x'+str(ferrite_bar_thickness)+'_z'+str(int(ferrite_y_distance))+'_r'+str(int(ferrite_outer_r))+'_u'+str(mu)
                                plot_color = cm(1.*run_count/num_configs)
                                colors.append(plot_color)
                                configs.append(config)
                                flux_density = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                ferrite_bs.append(flux_density)
                                #ferrite_bs.append(flux_density*flux_concentration_factor) #flux density * the flux concentration due to imperfect ferrite coverage
                                
                                femm.mo_savebitmap('img/ferrite_'+config+'.bmp')
                    
                                zee=[]
                                bee=[]
                                for n100 in range(-1500,1510):
                                    n = n100/10
                                    bx,by=femm.mo_getb(0,n);
                                    flux_density = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                    zee.append(n)   
                                    bee.append(flux_density);
                                top.plot(zee,bee, label=config, color=plot_color)
                                
                                ferrite_end = -(ferrite_y_distance+ferrite_bar_thickness)
                                print(ferrite_end)

                                bx,by=femm.mo_getb(0,ferrite_end);
                                flux_density = ((abs(bx)**2)+(abs(by)**2))**(1/2)
                                
                                top.plot([ferrite_end],[flux_density], "D", color=plot_color)

                                
                                femm.mi_selectrectangle(-280,-280,280,280,4)
                                femm.mi_deleteselected()
                                femm.mi_clearselected()


top.set_xlabel('Z distance; X=0 (mm)')
top.set_ylabel('Flux Density (T)')


top.set_title('Plot of flux density along the z-axis')
bot.set_title('Max flux density within ferrite')
#bot.tick_params(axis='x', rotation=-45, ha="left")

top.legend()

bot.bar(configs, ferrite_bs, color=colors)
bot.set_xlabel('Ferrite config')
plt.setp(bot.xaxis.get_majorticklabels(), rotation=-45, ha="left" )
bot_right_axis = bot.twinx()
mn, mx = bot.get_ylim()
bot_right_axis.set_ylim(mn*10000, mx*10000) #10000G per T
bot_right_axis.set_ylabel('G')
bot.set_ylabel('Max Flux Density within Ferrite (T)')






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

