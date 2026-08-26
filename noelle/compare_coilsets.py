# =============================================================================
# compare_coilsets.py
#
# WHAT THIS SCRIPT DOES
#   Compares wireless-power "coilsets". A coilset is a 3-coil link:
#       Loop  (L1) -- the PA-driven drive loop
#       Tank  (L2) -- a free-floating resonator on the transmit side
#       RC    (L3) -- the receive coil, on the load side
#   For each coilset it drives FEMM (2-D axisymmetric magnetostatic, 6.78 MHz,
#   millimetres) to extract the full self/mutual inductance matrix at a range
#   of RC-to-transmitter separations, feeds those into a closed-form 3-mesh
#   circuit model, and renders everything into one large matplotlib dashboard
#   plus a text log under ./results/ID<timestamp>/.
#
# PIPELINE, top to bottom
#   1. Global constants: frequency, fixed impedances, matching network, plot box
#   2. Result accumulator lists (one entry appended per coilset)
#   3. Matplotlib figure: a 6x4 grid carved into 3 big panes + line charts
#   4. `parameters` full-factorial sweep dict  (LEGACY - see note there)
#   5. Named coilset variants cloned from coil_configs.py
#   6. MAIN SIMULATION LOOP over coilset_array:
#        a. one FEMM circuit per physical turn
#        b. draw RC / Tank / Loop, then ferrite, shields, backplanes
#        c. solo inductances -> assembled inductances -> tuning capacitors
#        d. sweep RC distance: 3 FEMM solves per distance -> L/M/k -> circuit math
#        e. one final solve at the real currents -> ferrite/shield/trace losses
#        f. plot the config, then delete all geometry + circuits for the next one
#   7. Axis labels, the two summary tables, save SVG, show, close FEMM
#
# CONVENTIONS
#   - All FEMM geometry is in millimetres; y is the axis of revolution direction
#     (the "distance" axis), x is radius. Nothing may cross x = 0.
#   - Currents are PEAK amps unless the name ends in _RMS.
#   - "TC" = transmit coil side (Loop + Tank), "RC" = receive coil side.
#   - Helper functions all live in femm_model.py (imported as nff).
#
# NOTE ON THE COMMENTS BELOW
#   Blocks marked UNUSED / DEAD are assigned but never read anywhere in this
#   file. They are flagged rather than removed so nothing is deleted behind
#   your back - each one is safe to delete once you have confirmed no other
#   script imports this module.
# =============================================================================
    #Noel Hosking

# --- Imports ---------------------------------------------------------------
# femm  : the FEMM python wrapper. mi_* = model/input window (pre-processor),
#         mo_* = output window (post-processor, only valid after mi_analyze).
# cc    : coil_configs.py - library of ready-made CoilSet objects (cc.Gen3, ...)
# nff   : femm_model.py - all drawing/solving/post-processing helpers
# lta   : LTspice hand-off, used only by the commented-out "New Way" MPP solve
# UNUSED IMPORTS (safe to delete): random, sys, NamedTuple, dataclass,
#   Pool, cpu_count. `fields`, `iter`(itertools) and CoilSet ARE used.
import femm
import matplotlib.pyplot as plt
import matplotlib.transforms as xfrms
from matplotlib.font_manager import FontProperties
import numpy as np
from dataclasses import fields
from quantiphy import Quantity
import coil_configs as cc
import femm_model as nff
import datetime
from pathlib import Path



import ltspiceautomator.ltspice_automator as lta


# ============================ GLOBAL CONSTANTS =============================
# Fixed for the whole run. Per-coil geometry lives in the CoilSet objects.
# VPA_CHART_Y_LIM     : y value used only to decide where to drop the run-number
#                       label on the VPA chart (not an axis limit any more).
# Admittance_FW_limit : UNUSED - leftover from a dropped admittance check.
VPA_CHART_Y_LIM = 250
Admittance_FW_limit = .034

# Operating point: 6.78 MHz ISM band. omega is reused by every impedance below.
freq = 6.78e6
omega = 2*np.pi*freq

# UNUSED: R_source, R_load and R_load_1kW are all assigned and never read. The
# load the model actually uses is ZL_eq, built from the matching network a few
# lines down. Safe to delete all three.
R_source = 0
R_load = 5.70729148134063-17.6847088416818j #complex impedance of 50Ohm load and OC matching network    
R_load_1kW = 65.9178168237154+3.56064781719504j #1kW
# FEMM "group" numbers. Every drawn entity is tagged with one of these so an
# entire coil - plus any ferrite/shield/backplane drawn into the same group -
# can be selected and translated as one unit. This is how the RC is moved to
# each test distance without redrawing it.
# UNUSED: rc_backplane_group / tc_backplane_group - the backplanes are drawn
# into rc_group / loop_group instead, so they travel with their coil.
loop_group = 1
tank_group = 2
rc_group = 3
rc_backplane_group = 4
tc_backplane_group = 5

# Lumped series resistances (measured on hardware, not simulated) and the probe
# currents used for the FEMM inductance extraction. The probe current value is
# arbitrary - inductance is flux-linkage/current so it cancels - it only has to
# be non-zero to mark "this is the driven coil" inside nff.get_inductances.
#specified tank properties
tank_coil_current_target = 10 #A
#num_tank_turns = 5  
R_tank = 2

#specified loop properties
loop_current_target = 10 #A
#loop_offset = 5 #mm
R_loop = .2

#specified receive coil properties
rc_test_current_target = 12 #A
R_rc = .5
# Receive-side open-circuit (OC) matching network: series L, shunt C.
L_match_OC = 185e-9 #nH
C_match_OC = 1380e-12 #pF

# RC feed cable, as R + jwL of an RG8x run.
Z_cable_RC = .22 + (1j*omega*139e-9) #RG8x

# Power-amplifier side.
# Z_pa : PA output impedance seen by the loop.
# Vs   : fundamental component of the PA's square-wave output (4/pi * Vpa),
#        i.e. first-harmonic approximation, since the circuit math is phasor.
# UNUSED: Pout.
# NOTE Vpa stays fixed at 200 V on the active "OLD WAY" path, which is why the
#      VPA chart comes out flat. It is only recomputed by the commented-out
#      LTspice max-power-point block further down.
Z_pa = 0.102210554024676 + 26.0367108593328j
Pout = 1000
#efficiency = .85    
Vpa = 200
Vs = Vpa * (4/np.pi) #Derived via First Harmonic Approximation
#print(Vs)
# Rectifier + battery model. RL is the DC load; RL_ac is that load reflected
# through the full-bridge rectifier (8/pi^2). ZL_eq collapses the matching
# network and the rectified load into the single impedance the RC mesh sees.
RL = 3 #1000W @ 85V Vrect
RL_ac = RL * 8/(np.pi**2)
Z_ml = (1j*omega*L_match_OC)
Z_mc = 1/(1j*omega*C_match_OC)
ZL_eq = 1/((1/Z_mc)+(1/((2*Z_ml)+(RL_ac))))

#Backplane properties
backplane_length = 400 #mm
backplane_thickness = 5 #mm


# Axis box (mm) for the coil cross-section preview pane, disp_pane.
#plot and image size
neg_x_dim = 0
pos_x_dim = 250 

neg_y_dim = -20
pos_y_dim = 30


# DEAD: loop_config and wire_diameter are never read. The round-wire drawing
# path was removed - every coil is now drawn as a rectangular PCB trace, with
# 'trace' hard-coded at each draw_and_plot_coil_traces call site.
loop_config = 'trace' #trace or wire

#only used if loop_config is 'wire'
wire_diameter = 1.628 #mm   

# Colormap: each swept config gets its own colour, shared by its chart lines
# and its column header in both tables.
cm = plt.get_cmap('gist_rainbow')

# Run ID = DDHHMM. Names the log file and the ./results/ID<timestamp>/ folder.
timestamp = '{:%d%H%M}'.format(datetime.datetime.today())
Path("./results/ID"+timestamp).mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# RESULT ACCUMULATORS - one entry appended per config by the main loop.
# Lists ending in _display(s) hold quantiphy Quantity objects (auto SI prefix)
# and feed the results table drawn at the bottom of the figure.
# ---------------------------------------------------------------------------
colors = []
configs = []
tank_ferrite_max_flux = []
rc_ferrite_max_flux = []
k13_displays = []
k12_displays = []
k23_displays = []
M12_displays = []
M13_displays = []
M23_displays = []
L_rc_displays = []
L_loop_solo_displays = []
L_tank_solo_displays = []
# COPY/PASTE SLIP: L_rc_solo_displays is declared four times in a row. Keep one.
L_rc_solo_displays = []
L_rc_solo_displays = []
L_rc_solo_displays = []
L_rc_solo_displays = []
# Per-config summary values that really are rendered in the results table.
L_tank_displays = []
Itank_displays = []
Iloop_displays = []
L_loop_fully_assembled_displays = []
L_tank_fully_assembled_displays = []
L_rc_fully_assembled_displays = []



current_mag_displays = []
loop_losses = []
tank_losses = []
rc_losses = []
tc_ferrite_losses = []
rc_ferrite_losses = []
rc_shield_losses = []
tc_shield_losses = []
total_losses = []
efficiency_tests = []

# config_results  : the rows of the bottom-left results table, filled in at the
#                   very end of the script (order must match `rows`).
config_results = []

#Window/Fig
# One 6x4 subplot grid is created, then most cells are removed and merged into
# three large panes:
#   cols 0-1, rows 0-2 -> config_pane  (table of INPUT parameters per config)
#   cols 0-1, rows 3-5 -> results_pane (table of OUTPUT results per config)
#   col 3,    rows 4-5 -> disp_pane    (coil cross-section drawing)
# The surviving single cells in columns 2 and 3 are the line charts.
fig2, bxs = plt.subplots(6, 4, num=f'Sim-{timestamp}')
vpa_pane = bxs[0,3]
ipa_pane = bxs[0,2]
Iloop_pane = bxs[1,2]
Itank_pane = bxs[2,2]
Irc_pane = bxs[3,2]
tank_tuning_pane = bxs[4,2]
tank_lateral_tuning_pane = bxs[5,2]
pbatt_pane = bxs[3,3]
# NOTE: efficiency_pane is created and fully styled further down, but nothing
# is ever plotted into it (the plot call is commented out) - it renders empty.
efficiency_pane = bxs[1,3]
vrect_pane = bxs[2,3]
for i in range(6):
    for j in range(2):
        bxs[i,j].remove()
bxs[4,3].remove()
bxs[5,3].remove()
# bs and bs2 are the SAME gridspec fetched twice; bs2 is redundant.
bs = bxs[3, 0].get_gridspec()
bs2 = bxs[3,0].get_gridspec()
config_pane = fig2.add_subplot(bs[0:3, 0:2])
results_pane = fig2.add_subplot(bs2[3:6, 0:2])
disp_pane = fig2.add_subplot(bs[4:, 3]) 

# ---------------------------------------------------------------------------
# LEGACY FULL-FACTORIAL SWEEP TABLE
# Every key is one CoilSet field, IN THE SAME ORDER AS THE CoilSet DATACLASS,
# and every value is the list of settings to try for it. itertools.product over
# these values builds one CoilSet per combination further down.
#
# IMPORTANT: that generated list is thrown away - `coilset_array` is
# reassigned to an explicit list of named configs before the simulation runs.
# So these numbers no longer drive the simulation. They are still read for
#   (a) the no_*_present flags that prune rows from the config table, and
#   (b) parameter_count and the config table's row labels.
# To change what is simulated, edit the coilset_array assignment (or the
# config in coil_configs.py), not this dict.
#
# The trailing "#0, #1, ..." numbers are stale positional indices from an older
# constructor signature; they stop matching after #29.
# ---------------------------------------------------------------------------
run_count = 0
parameters = {
    'config_name' : [""],
    #Ferrite in Enclosure
    'Tank Ferrite Present' : [0], #0
    'Tank Ferrite Y offset (mm)' : [0], #1
    'Tank Ferrite Thickness (mm)' : [1], #2
    'Tank Ferrite Permeabilitiy' : [184.5], #3
    'Tank Ferrite Radius' : [100], #4
    
    'RC Ferrite Present' : [0], #0
    'RC Ferrite +Y offset (mm)' : [0], #1
    'RC Ferrite Thickness (mm)' : [1], #2
    'RC Ferrite Permeabilitiy' : [184.5], #3
    'RC Ferrite Radius' : [70], #4
    #
    #Metal Plane for detuning tank
    'Metal backplane present' : [0], #5
    'Metal Backplane Z offset (mm)' : [20], #6
    #
    #Loop
    'Loop Z offset (mm)' : [0], #7
    'Loop Center radius' : [50], #65 is 130mm, 72.688 matches lower l coil #8
    'Number of Loop Turns' : [1], #9
    'Number of Parallel Loop traces' : [1], #10 
    'Number of Loop Layers': [1], #11
    'Loop Trace Center-Center Spacing (mm)' : [4.464], #12    
    'Loop Turn Center-Center Spacing (mm)' : [3.3], #13
    #
    #Tank
    'Tank Center radius (mm)' : [71.818], #14 #72.688
    'Number of Tank Turns' : [5], #15
    'Number of Parallel Tank Traces' : [1], #16
    'Number of Tank Layers' : [1], #17
    'Tank Trace Center-Center Spacing (mm)' : [.508], #18
    'Tank Turn Center-Center Spacing (mm)' : [7.112], #19
    #
    #RC
    'RC central turn radius (mm)' : [35], #20
    'Number of RC Turns' : [8, 9, 10], #21
    'Number of Parallel RC Traces' : [1], #22
    'Number of RC Layers' : [1], #23
    'RC trace Center-Center spacing (mm)' : [0], #24
    'RC turn Center-Center spacing (mm)' : [3], #25
    #Cable
    'Coax Cable Config' : [213], #26
    'Loop Tuning Frequency (MHz)' : [6.85], #27
    'Tank Tuning Frequency (MHz)' : [6.75], #28
    'RC Tuning Frequency (MHz)' : [6.84], #29
    
    'Tank Trace Width (mm)' : [3.175], #5.554
    'Loop Trace Width (mm)' : [2.54], #31
    'RC Trace Width (mm)' : [1.524],

    'Tank PCB thickness' : [1.6],
    'Loop PCB thickness' : [1.6],
    'RC PCB thickness' : [1.6],
    
    #RC Detuning Metal Plane
    'RC Metal Backplane' : [0],
    'RC Metal Backplane Z offset (mm)' : [20], 

    #TC Metal Shield inside enclosure
    'TC metal shield' : [0],
    'TC metal shield Z offset (mm)' : [3],
    'TC metal shield radius (mm)' : [105],
    'TC metal shield thickness (mm)' : [1],

    #RC Metal Shield inside enclosure
    'RC metal shield' : [0],
    'RC metal shield Z offset (mm)' : [0],
    'RC metal shield radius (mm)' : [60],
    'RC metal shield thickness (mm)' : [1],

    'Loop Trace Thickness (mm)' : [.07], #.035 - 1oz, .07 - 2oz
    'Tank Trace Thickness (mm)' : [.07], #.035 - 1oz, .07 - 2oz
    'RC Trace Thickness (mm)' : [.07], #.035 - 1oz, .07 - 2oz

}

# Flags that decide which rows are pruned from the config table at the bottom
# (no point printing ferrite geometry when nothing has ferrite).
# WARNING: these are derived from the `parameters` dict, NOT from the coilsets
# actually simulated, so they can disagree with reality whenever coilset_array
# is overridden below - which it currently is.
no_tank_ferrite_present = True
for ferrite_qty in parameters['Tank Ferrite Present']:
    if (ferrite_qty > 0):
        no_tank_ferrite_present = False

no_rc_ferrite_present = True
for ferrite_qty in parameters['RC Ferrite Present']:
    if (ferrite_qty > 0):
        no_rc_ferrite_present = False

no_backplane_present = True
for backplane in parameters['Metal backplane present']:
    if (backplane > 0):
        no_backplane_present = False



#create an empty array of arrays for config combination output
# parameter_count is 55 (dict keys) while CoilSet has 57 fields: rc_loss_angle
# and tc_loss_angle carry defaults and are deliberately absent from the dict.
# The main loop slices the field values with parameter_count, so those two are
# simply left out of the config table.
parameter_count = len(list(parameters.keys()))
parameter_outputs = []
for i in range(0, parameter_count):
    parameter_outputs.append([])


#risky since dicts are not guaranteed to be ordered
# (Python 3.7+ dicts are insertion-ordered, so the risk is not the dict - it is
#  that this key order must stay in lockstep with the CoilSet field order.)

# ---------------------------------------------------------------------------
# NAMED CONFIG VARIANTS
# Each block clones a baseline from coil_configs.py and tweaks a few fields.
# None of them run unless they appear in the coilset_array assignment below.
# Currently UNUSED (kept as a menu of past experiments): Gen2_4Layer,
# Gen3_backplane, Gen3_4mm_RC_ferrite, Gen3_2mm_RC_ferrite,
# Gen3_2mm_61_RC_ferrite, new_coil_6_turn, new_coil_7_turn, new_coil_8_turn.
# Delete only if you are sure you will not want to re-run those comparisons.
# ---------------------------------------------------------------------------
#Special Configs
###############################
#5-turn V1 HP tank, George Coil (3-turn RC 100mm), 130mm Loop 

# q305 = cc.create_iter_base_coil(cc.q305_vars)

Gen2_4Layer = cc.copy_coilset(cc.Gen2_coil)
Gen2_4Layer.num_tank_layers = 4

# RevA1_no_tank.num_tank_turns = 0

# NOTE: the "MAIN LOOP" banner below is misleading. This only BUILDS the swept
# coilset list from `parameters`; the real simulation loop is much further
# down, after nff.init_femm(). Because coilset_array is reassigned below, this
# loop is currently wasted work (it still validates that the dict and the
# CoilSet signature agree, which is the one reason to keep it).
#######MAIN LOOP



Gen3_backplane = cc.copy_coilset(cc.Gen3)
Gen3_backplane.rc_backplane = 1 

Gen3_4mm_RC_ferrite = cc.copy_coilset(cc.Gen3)
Gen3_4mm_RC_ferrite.config_name = "4mm RC ferrite, FJ7"
Gen3_4mm_RC_ferrite.rc_ferrite_thickness = 4
Gen3_4mm_RC_ferrite.rc_ferrite_y_offset = 1
Gen3_4mm_RC_ferrite.rc_backplane = 1

Gen3_2mm_RC_ferrite = cc.copy_coilset(cc.Gen3)
Gen3_2mm_RC_ferrite.config_name = "2mm RC ferrite, FJ7"
Gen3_2mm_RC_ferrite.rc_ferrite_thickness = 2    
Gen3_2mm_RC_ferrite.rc_ferrite_y_offset = 1
Gen3_2mm_RC_ferrite.rc_backplane = 1

Gen3_2mm_61_RC_ferrite = cc.copy_coilset(cc.Gen3)
Gen3_2mm_61_RC_ferrite.config_name = "2mm RC ferrite, 61"
Gen3_2mm_61_RC_ferrite.rc_ferrite_thickness = 2    
Gen3_2mm_61_RC_ferrite.rc_ferrite_y_offset = 1
Gen3_2mm_61_RC_ferrite.rc_backplane = 1
Gen3_2mm_61_RC_ferrite.tank_mu = 125
Gen3_2mm_61_RC_ferrite.rc_mu = 125
Gen3_2mm_61_RC_ferrite.rc_loss_angle = 0.0073
Gen3_2mm_61_RC_ferrite.tc_loss_angle = 0.0073

new_coil_6_turn = cc.copy_coilset(cc.TR302_and_225_000056)
new_coil_6_turn.rc_radius = 21.875
new_coil_6_turn.rc_trace_spacing = 1.8
new_coil_6_turn.rc_turn_spacing = 1.8
new_coil_6_turn.rc_trace_width = 1.2

new_coil_7_turn = cc.copy_coilset(new_coil_6_turn)
new_coil_7_turn.num_rc_turns = 7

new_coil_8_turn = cc.copy_coilset(cc.TR302_and_225_000056)
new_coil_8_turn.num_rc_turns = 8
new_coil_8_turn.rc_radius = 21.775
new_coil_8_turn.rc_turn_spacing = 1.6
new_coil_8_turn.rc_trace_width = 1
new_coil_8_turn.num_rc_layers = 4




# >>> THIS LINE DECIDES WHAT ACTUALLY GETS SIMULATED <<<
# It overwrites the product-generated coilset_array above. Put one config in
# the list to characterise it, or several to compare them side by side - each
# entry becomes one column in both output tables and one colour in the charts.
#coilset_array = coilset_array #+  [cc.RevA1, cc.q15]
coilset_array = [cc.TR302_and_225_000056]

#coilset_array = [cc.Gen2_coil, Gen3_4mm_RC_ferrite, Gen3_2mm_RC_ferrite, Gen3_2mm_61_RC_ferrite]#[cc.Gen3, Gen3_backplane]


# Geometry sanity check before FEMM is even opened: catches traces that would
# overlap and coils so wide they would cross the axis of revolution (x = 0).
# Raises rather than drawing something meaningless.
for coilset in coilset_array:
    print(coilset)
    failure_reason, coilset_valid = nff.check_valid_coilset(coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_spacing, coilset.loop_turn_spacing, coilset.loop_trace_width, coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_width, coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces, coilset.rc_trace_spacing, coilset.rc_turn_spacing, coilset.rc_trace_width)
    if (not coilset_valid):
        raise Exception(f"{failure_reason}")

# Boot FEMM: new magnetostatic axisymmetric problem in mm at 6.78 MHz, register
# the Air / Copper / Ferrite / AluFoil materials and the absorbing boundary.
nff.init_femm()





# ===========================================================================
# MAIN SIMULATION LOOP
# One iteration == one coilset == one column in both tables and one colour in
# every chart. Each iteration must leave the FEMM model empty again, because
# all configs share a single FEMM document.
# ===========================================================================
num_configs = len(list(coilset_array))
for coilset in coilset_array:
    #creating turn circuits for simulation
    # One FEMM circuit per PHYSICAL turn ("loop0", "tank3", "rc7", ...). Keeping
    # turns separate is what lets nff sum flux linkage turn-by-turn to get the
    # self and mutual inductances instead of a single lumped number.
    for i in range(coilset.num_loop_turns):
        femm.mi_addcircprop('loop'+str(i), 0, 0)
    
    for i in range(coilset.num_tank_turns):
        femm.mi_addcircprop('tank'+str(i), 0, 0)

    for i in range(coilset.num_rc_turns):
        femm.mi_addcircprop('rc'+str(i), 0, 0)
    
    # Transmit-side coax, selected by cable type. Adds series R + jwL to Z1.
    if (coilset.coax_cable == 214): #RG214
        Z_cable_TC = .149 + (1j*omega*157e-9)
    elif (coilset.coax_cable == 213): #RG213
        Z_cable_TC = .12 + (1j*omega*152e-9)
    else: #RG8x
        Z_cable_TC = .22 + (1j*omega*139e-9)

    # Bookkeeping for this run: label for the legend/table and a distinct
    # colour taken from the colormap by position in the sweep.
    run_count+=1
    run_num = "run-" + str(run_count)
    
    
    configs.append(coilset.config_name +str(run_num))
    plot_color = cm(1.*run_count/num_configs)
    colors.append(plot_color)

    #modify the ferrite to have the x,y permeability for this run
    # FEMM material property slots: 1/2 = mu_x / mu_y, 5 = conductivity,
    # 10/11 = loss tangents (hysteresis lag angle).
    # GOTCHA: both the TC and the RC ferrite use this one shared 'Ferrite'
    # material, so the RC ferrite silently inherits the TANK's permeability and
    # loss angle - coilset.rc_mu and coilset.rc_loss_angle are set by some
    # configs but never applied here. Add a second material if the two ferrites
    # must differ.
    femm.mi_modifymaterial('Ferrite', 1, coilset.tank_mu)
    femm.mi_modifymaterial('Ferrite', 2, coilset.tank_mu)
    femm.mi_modifymaterial('Ferrite', 5, 1e-11) #conductivity
    femm.mi_modifymaterial('Ferrite', 10, coilset.tc_loss_angle) #Fj7: .012544, 61: 0.0073
    femm.mi_modifymaterial('Ferrite', 11, coilset.tc_loss_angle)
    femm.mi_zoom(0, -100, 100, 100)

    # Dead scratch from an earlier discrete-ferrite-bar study (which is not
    # representable in an axisymmetric model anyway). Reference only.
    #ferrite_y_distance = coilset.tank_PCB_thickness + coilset.tank_ferrite_y_offset+coilset.loop_PCB_thickness+coilset.loop_offset+(2*trace_thickness)
    #ferrite_inner_r = int(bar_width / (2*np.tan(np.pi/ferrite_qty)))
    #if (ferrite_inner_r > tank_inner_r):
    #   print(ferrite_qty)
    #  print(bar_width)
    # break;
    #ferrite_bar_length = 100 - ferrite_inner_r

    # UNUSED: coil_y_shift. The "park the coil 300 mm away" offset is hard-coded
    # at each get_inductances call site instead.
    coil_y_shift = 300

    # RC-to-transmitter separations (mm) swept for this config. Index 0 is the
    # geometry actually drawn in FEMM; every other distance is reached by
    # translating rc_group by (distance - rc_test_distances[0]).
    #set distance
    #rc_test_distances = [30]

    rc_test_distances = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 75, 100]

    #DRAWING RC
    # draw_and_plot_coil_traces() draws the spiral as a stack of rectangles in
    # the FEMM model AND sketches it onto disp_pane. It returns the radius of
    # every trace and the y of every layer, both needed later for per-trace
    # loss integration. Order matters only for readability - the group tag on
    # the last argument is what binds each shape to its coil.
    # Vertical layout: RC face-down at the first test distance (hence the
    # negative PCB thickness), tank at y = 0, loop below the tank.
    rc_turn_radii, rc_trace_ys = nff.draw_and_plot_coil_traces(coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces, coilset.rc_trace_width, coilset.rc_trace_spacing, coilset.rc_turn_spacing, rc_test_distances[0], coilset.rc_trace_thickness, 'trace', 'rc', disp_pane, plot_color, "-", coilset.num_rc_layers, -coilset.rc_PCB_thickness, rc_group)

    #DRAWING TANK                    

    tank_turn_radii, tank_trace_ys = nff.draw_and_plot_coil_traces(coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_width, coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_thickness, coilset.tank_trace_thickness,'trace', 'tank', disp_pane, plot_color, "-", coilset.num_tank_layers, coilset.tank_PCB_thickness, tank_group) 

    #DRAWING LOOP
    loop_turn_radii, loop_trace_ys = nff.draw_and_plot_coil_traces(coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_width, coilset.loop_trace_spacing, coilset.loop_turn_spacing, -coilset.tank_PCB_thickness-coilset.loop_offset, coilset.loop_trace_thickness, 'trace', 'loop', disp_pane, plot_color,  "-", coilset.num_loop_layers, coilset.loop_PCB_thickness, loop_group)


    # Per-turn drive current. The target coil current is divided across all
    # parallel traces and layers, then multiplied back up by traces-per-turn,
    # so the ampere-turns match a real coil wound from these traces.
    loop_turn_current = nff.get_turn_current(loop_current_target, coilset.num_loop_layers, coilset.num_parallel_loop_traces, coilset.num_loop_turns)
    tank_turn_current = nff.get_turn_current(tank_coil_current_target, coilset.num_tank_layers, coilset.num_parallel_tank_traces, coilset.num_tank_turns)
    rc_turn_current = nff.get_turn_current(rc_test_current_target, coilset.num_rc_layers, coilset.num_parallel_rc_traces, coilset.num_rc_turns)

    #input("PAUSE")
    #### Coil self-Inductances with nothing nearby
    # "Solo" inductance: drive one coil and park the other two 300 mm away so
    # they cannot perturb the field. No ferrite or shields exist yet, so these
    # are the bare-PCB values - the "just PCB" rows in the results table.
    loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0,300, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
    L_loop_solo = loop_inductances.L11
    L_loop_solo_display =  Quantity(L_loop_solo, 'H')
    L_loop_solo_displays.append(L_loop_solo_display)

       
    tank_inductances = nff.get_inductances(loop_group, 0, 0, 300, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
    L_tank_solo = tank_inductances.L22
    L_tank_solo_display =  Quantity(L_tank_solo, 'H')
    L_tank_solo_displays.append(L_tank_solo_display)
    # DEAD: overwritten by the with-ferrite value in the tuning block below.
    L_tank_fully_assembled = tank_inductances.L22


    rc_inductances = nff.get_inductances(loop_group, 0, 0, 300, coilset.num_loop_turns, tank_group, 0, 0, 300, coilset.num_tank_turns, rc_group, rc_turn_current, 0, 0, coilset.num_rc_turns)
    L_rc_solo = rc_inductances.L33
    L_rc_solo_display =  Quantity(L_rc_solo, 'H')
    L_rc_solo_displays.append(L_rc_solo_display)
#    input("pausing at line 415")


    #### Adding Ferrite(s) and Metal Shield(s)

    # Stack-up construction. tc_bottom_y walks DOWNWARD from the underside of
    # the loop PCB, each layer subtracting its own gap and thickness, so the
    # pieces land in physical order: loop PCB -> gap -> ferrite -> gap ->
    # shield -> gap -> backplane. rc_top_y does the mirror image above the RC.
    # Each *_midpoint_y is remembered because the loss integration later needs
    # a point INSIDE the block to select it.
    tc_bottom_y = -coilset.tank_PCB_thickness-coilset.loop_offset-coilset.loop_PCB_thickness-coilset.tank_trace_thickness-(2*coilset.loop_trace_thickness)
    #TC Ferrite
    if (coilset.tank_ferrite_present):
        tc_bottom_y -= coilset.tank_ferrite_y_offset
        ferrite_top_edge = tc_bottom_y
        tc_ferrite_midpoint_y = ferrite_top_edge - (coilset.tank_ferrite_thickness/2)
        nff.draw_rectangular_material('Ferrite', coilset.tank_ferrite_thickness, coilset.tank_ferrite_outer_r , tc_bottom_y, loop_group, disp_pane, plot_color, '-')
        tc_bottom_y -= coilset.tank_ferrite_thickness

    # NOTE THE INDENTATION: this TC shield block sits INSIDE the
    # `if tank_ferrite_present` branch, so a config asking for a shield but no
    # ferrite silently gets no shield (and tc_shield_midpoint_y stays
    # undefined, which would raise later). The RC side below has the same
    # shape. De-indent one level if shields are meant to be independent.
    #TC-side Metal shield
        if (coilset.tc_shield):
            tc_bottom_y -= coilset.tc_shield_y_distance
            tc_shield_midpoint_y = tc_bottom_y - (coilset.tc_shield_thickness/2)
            nff.draw_rectangular_material('AluFoil', coilset.tc_shield_thickness, coilset.tc_shield_radius , tc_bottom_y, loop_group, disp_pane, plot_color, '-')
            tc_bottom_y -= coilset.tc_shield_thickness

    # RC-side stack-up, mirrored above the receive coil.
    rc_top_y = coilset.rc_PCB_thickness + rc_test_distances[0] + (coilset.rc_trace_thickness*2) + coilset.rc_ferrite_y_offset
    rc_ferrite_bottom = rc_top_y
    #RC Ferrite
    if (coilset.rc_ferrite_present):
        rc_ferrite_midpoint_y = rc_top_y + (coilset.rc_ferrite_thickness/2)
        rc_top_y += coilset.rc_ferrite_thickness
        nff.draw_rectangular_material('Ferrite', coilset.rc_ferrite_thickness, coilset.rc_ferrite_outer_r , rc_top_y, rc_group, disp_pane, plot_color, '-')

    #RC-side Metal shield
        if (coilset.rc_shield):
            rc_top_y += coilset.rc_shield_y_distance
            rc_shield_midpoint_y = rc_top_y + (coilset.rc_shield_thickness/2)
            rc_top_y += coilset.rc_shield_thickness
            nff.draw_rectangular_material('AluFoil', coilset.rc_shield_thickness, coilset.rc_shield_radius , rc_top_y, rc_group, disp_pane, plot_color, '-')





    #Drawing    ^
    #------------------------------------------------------------------------------------------------------------#
    #Simulation v
    # Per-distance result lists, reset for every config.
    # DEAD in this block: Ypa_tests (its only append is commented out),
    # fixed_Y_Vpas, L_loop_displays (assigned, never plotted),
    # loop/tank/rc_frequency_shifts (computed and stored, never read),
    # L_loop_vs_distance / L_tank_vs_distance / L_rc_vs_distance (the
    # *_displays_vs_distance versions are what actually get logged),
    # vratio_lateral_tests and tank_resonant_f_vs_lateral_offset (the lateral
    # sweep that fed them was removed, so tank_lateral_tuning_pane draws empty).
    itank_tests = []
    iloop_tests = []
    irc_tests = []
    Vpa_tests = []
    Ipa_tests = []
    Ypa_tests = []
    vrect_tests = []
    fixed_Y_Vpas = []
    L_loop_vs_distance = []
    M13_vs_distance = []
    M12_vs_distance = []
    M32_vs_distance = []
    M31_vs_distance = []
    L_tank_vs_distance = []
    M23_vs_distance = []
    M21_vs_distance = []
    L_rc_vs_distance = []
    k12_vs_distance = []
    k13_vs_distance = []
    k23_vs_distance = []
    loop_resonant_f_vs_distance = []
    loop_frequency_shifts = []
    tank_resonant_f_vs_distance = []
    tank_frequency_shifts = []
    rc_resonant_f_vs_distance = []
    rc_frequency_shifts = []
    L_loop_displays = []
    L_loop_displays_vs_distance = []
    L_tank_displays_vs_distance = []
    L_rc_displays_vs_distance = []
    abs_Iloop_vs_distance = []
    abs_Itank_vs_distance = []
    tank_resonant_f_vs_lateral_offset = []
    vratio_lateral_tests = []
    Pbatt_tests = []
    
    



    #### Coil Tuning Test (LoopPCB +Tank PCB+Ferrite+Backplane) and separately, (RC PCB+Ferrite+Backplane)
    # "Fully assembled" inductance: the same three solves, but now WITH the
    # ferrite and shields drawn and the other transmit-side coil left in place
    # (y-shift 0), because the tuning capacitors have to be sized against the
    # in-situ inductance, not the bare-PCB one. The far side is still parked
    # away (300 mm for the RC, 200 mm for the RC's own solve).
    loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
    L_loop_fully_assembled = loop_inductances.L11
    L_loop_fully_assembled_display = Quantity(L_loop_fully_assembled, 'H')
    L_loop_fully_assembled_displays.append(L_loop_fully_assembled_display)

    
    tank_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
    L_tank_fully_assembled = tank_inductances.L22
    L_tank_fully_assembled_display = Quantity(L_tank_fully_assembled, 'H')
    L_tank_fully_assembled_displays.append(L_tank_fully_assembled_display)


    rc_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, rc_turn_current, 0, 200, coilset.num_rc_turns)
    L_rc_fully_assembled = rc_inductances.L33
    L_rc_fully_assembled_display = Quantity(L_rc_fully_assembled, 'H')
    L_rc_fully_assembled_displays.append(L_rc_fully_assembled_display)


    # Tuning capacitors that resonate each coil at its target frequency. They
    # are computed ONCE per config and then held fixed while the RC distance is
    # swept - that fixed C against a changing L is exactly what produces the
    # detuning curves plotted later.
    C1 = nff.resonant_capacitance(L_loop_fully_assembled, coilset.loop_tune_f*1e6)
    C2 = nff.resonant_capacitance(L_tank_fully_assembled, coilset.tank_tune_f*1e6)
    C3 = nff.resonant_capacitance(L_rc_fully_assembled, coilset.rc_tune_f*1e6)

        
        
    #TC-side Metal detuning plane
    # Metal detuning planes (stand-ins for a table top / device chassis). Drawn
    # last so they sit outside the rest of the stack-up.
    # NOTE they are added AFTER C1..C3 were computed, so the tuning caps do not
    # see the backplane while every distance-sweep solve below does. That models
    # "tuned on the bench, then used near metal"; move this block above the
    # capacitance calculation if you want it tuned in place instead.
    if (coilset.backplane): 
        tc_bottom_y -= coilset.backplane_y_distance - 3 #enclosure_thickness
        tc_backplane_midpoint_y = tc_bottom_y - (backplane_thickness/2)
        nff.draw_rectangular_material('Copper', backplane_thickness, backplane_length/2 , tc_bottom_y, loop_group, disp_pane, plot_color, '-')
        tc_bottom_y -= backplane_thickness

    if (coilset.rc_backplane):
        rc_top_y += (coilset.rc_backplane_y_distance + backplane_thickness + 3) #6=enclosure thickness
        rc_backplane_midpoint_y = rc_top_y + (backplane_thickness/2)
        nff.draw_rectangular_material('Copper', backplane_thickness, backplane_length/2 , rc_top_y, rc_group, disp_pane, plot_color, '-')
        rc_top_y += backplane_thickness

    # Abandoned multiprocessing experiment. FEMM is a single global instance
    # driven through one connection, so this cannot work as written.
    # def t():
    #     # Make a dummy dictionary
    #     d = {k: k**2 for k in range(10)}

    #     pool = Pool(processes=(cpu_count() - 1))

    #     for key, value in d.items():
    #         pool.apply_async(thread_process, args=(key, value))

    #     pool.close()
    #     pool.join()

    ### Testing at distances with any external metal
    # Distance sweep. The RC is translated rather than redrawn.
    # UNUSED: starting_rc_distance (only referenced in a commented-out print).
    starting_rc_distance = rc_test_distances[0]
    rc_y_translations = [rc_test_distance-rc_test_distances[0] for rc_test_distance in rc_test_distances]

        # THREE FEMM solves per distance - one per driven coil. get_inductances
        # only recovers the row of the inductance matrix belonging to whichever
        # coil is carrying current, so:
        #   solve 1 (loop driven) -> L11, M12, M13
        #   solve 2 (tank driven) -> L22, M23, M21
        #   solve 3 (RC   driven) -> L33, M31, M32
        # M12 vs M21 (and M13 vs M31) should agree by reciprocity - they are all
        # logged so the disagreement doubles as a solver sanity check.
        # get_inductances translates the group, solves, then translates back, so
        # the model geometry is unchanged when it returns.
    for rc_y_translation in rc_y_translations:
        
        loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, rc_y_translation, coilset.num_rc_turns)
        L_loop = loop_inductances.L11
        L_loop_display =  Quantity(L_loop, 'H')
        L_loop_displays_vs_distance.append(L_loop_display)
        M13 = loop_inductances.M13
        M12 = loop_inductances.M12


        tank_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, rc_y_translation, coilset.num_rc_turns)
        L_tank = tank_inductances.L22
        L_tank_display =  Quantity(L_tank, 'H')
        L_tank_displays_vs_distance.append(L_tank_display)
        M23 = tank_inductances.M23
        M21 = tank_inductances.M21



        rc_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, rc_turn_current, 0, rc_y_translation, coilset.num_rc_turns)
        L_rc = rc_inductances.L33
        L_rc_display =  Quantity(L_rc, 'H')
        L_rc_displays_vs_distance.append(L_rc_display)
        M31 = rc_inductances.M31
        M32 = rc_inductances.M32

        # Coupling coefficients. k12 and k23 are left at 0 for tank-less configs
        # so the sqrt(L_tank) never divides by zero.
        k13 = M13 / (np.sqrt(L_loop*L_rc))
        k12 = 0
        k23 = 0
        if (coilset.num_tank_turns > 0):
            k12 = M12 / (np.sqrt(L_loop*L_tank)) 
            k23 = M23 / (np.sqrt(L_tank*L_rc))

        # How far each coil has drifted off its tuned frequency at this
        # distance, given the fixed C1/C2/C3 computed above.
        loop_shifted_f = nff.resonant_frequency(L_loop, C1)
        loop_tuning_shift = loop_shifted_f - (coilset.loop_tune_f*1e6)

        tank_shifted_f = nff.resonant_frequency(L_tank, C2)
        tank_tuning_shift = tank_shifted_f - (coilset.tank_tune_f*1e6)

        rc_shifted_f = nff.resonant_frequency(L_rc, C3)
        rc_tuning_shift = rc_shifted_f - (coilset.rc_tune_f*1e6)

        # Mesh impedances of the three loops at 6.78 MHz.
        # Z1: PA output + tuning cap + loop L + loop ESR + transmit coax.
        Z1 = Z_pa + 1/(1j*omega*C1) + 1j*omega*L_loop + R_loop + Z_cable_TC

        # The tank is a free-floating series LC. For a tank-less config Z2 is
        # set to a dummy 1 so the tank terms in the current equations below
        # collapse harmlessly instead of blowing up.
        if (coilset.num_tank_turns > 0):
            Z2 = 1 / (1j * omega * C2) + 1j * omega * L_tank + R_tank
        else:
            Z2 = 1

        Z3 = 1/(1j*omega*C3) + 1j*omega*L_rc + R_rc + Z_cable_RC + ZL_eq #Assuming R_load is actually the abs. of Z_eq of load and matching network -- no rectifier



        #print(f"###Debug\nM12: {M12}\n, M13:{M13},\n M23:{M23},\n L_loop:{L_loop},\n L_tank:{L_tank},\n L_rc:{L_rc},\n omega:{omega},\n Current_denom:{current_denominator},\n Vs:{Vs},\n Z1:{Z1}\n, Z2:{Z2}\n,Z3:{Z3}\n, iloop:{iloop}, \n itank:{itank}, \n irc:{irc}, \n C1:{C1}, \nC2:{C2}, \nC3:{C3}, \n")

        # --- Two alternative operating-point solvers ------------------------
        # (a) NEW WAY: hand L / k / R to ltspiceautomator and let LTspice find
        #     the maximum power point. Currently commented out - which makes
        #     the coilset_config object below DEAD: it is rebuilt on every
        #     iteration and never used. Keep it only if you intend to re-enable
        #     the LTspice path.
        # (b) OLD WAY: closed-form solution of the 3-mesh system at a fixed
        #     200 V PA and an assumed 85% PA efficiency. This is what runs.
        ######

        coilset_config = lta.Config(
            name=f"\n\ncoilset{run_count}:{coilset.config_name} - Distance: {rc_y_translation+rc_test_distances[0]}mm\n",
            L_TC_Loop=L_loop, L_TC_Tank=L_tank, L_RC=L_rc,
            f_TC_Loop=coilset.loop_tune_f*1e6, f_TC_Tank=coilset.tank_tune_f*1e6, f_RC=coilset.rc_tune_f*1e6,
            R_TC_Loop=0.190, R_TC_Tank=1.836, R_RC=0.512,
            k12=k12, k13=k13, k23=k23,
            L_TC_Coax=139e-9, R_TC_Coax=220e-3,
            L_RC_Coax=139e-9, R_RC_Coax=220e-3,
        )
        
        ############### New Way
        # mpp_results = coilset_config.get_mpp(range(1, 30))
        # print(f"\n\ncoilset{run_count}:{coilset.config_name} - Distance: {rc_y_translation+rc_test_distances[0]}mm:\n")
        # print(mpp_results)
        # Vrect = mpp_results.VRect
        # PBatt = mpp_results.PBatt
        # Vpa = mpp_results.VPA
        # Ipa = mpp_results.IPA
        # pa_efficiency = PBatt/(Vpa*Ipa)
        # Vs = mpp_results.VSMag
        # iloop = mpp_results.ITCLoopRMS
        # itank = mpp_results.ITCTankRMS
        # irc = mpp_results.IRCRMS



        #print(f'x={starting_rc_distance+rc_translation}, Vrect={Vrect}\n')
        
        ###########OLD WAY
        # Closed-form solve of the coupled 3-mesh system (Cramer's rule on the
        # impedance matrix): current_denominator is the determinant and each
        # current is its cofactor times the source voltage Vs. The omega^2 and
        # omega^3 terms are the mutual-coupling cross terms. All three results
        # are PEAK amp magnitudes.
        current_denominator = (((M12**2)*Z3*(omega**2))-(2j*M12*M13*M23*(omega**3))+((M13**2)*Z2*(omega**2))+((M23**2)*Z1*(omega**2))+(Z1*Z2*Z3))
        iloop = np.abs((Vs*(((M23**2)*(omega**2))+(Z2*Z3)))/current_denominator)
        itank = np.abs((Vs*((-1j*M12*Z3*omega)-(M13*M23*omega**2)))/current_denominator)
        irc = np.abs(Vs*(((-M12*M23*omega**2)-(1j*M13*Z2*omega))/current_denominator))
        
        # Power accounting.
        # Vrect : DC bus voltage the OC matching network delivers into RL.
        # PBatt : delivered DC power.
        # Ploss_*: real power burned in each mesh, from RMS current into the
        #          real part of that mesh impedance. Ploss_rc subtracts PBatt
        #          because ZL_eq (inside Z3) already contains the load.
        # Ipa   : back-solved from total power and the assumed PA efficiency,
        #         so the IPA chart is derived, not simulated.
        pa_efficiency = .85
        Vrect = np.abs((np.abs(irc)*np.sqrt(2*RL_ac*RL))/np.abs((2*(RL_ac+2*Z_ml)*((1/(RL_ac + 2*Z_ml))+(1/Z_mc)))))
        PBatt = (Vrect**2)/RL
        iloop_RMS = iloop / np.sqrt(2)
        itank_RMS = itank / np.sqrt(2)
        irc_RMS = irc / np.sqrt(2)
        Ploss_loop = ((iloop_RMS**2)*Z1).real
        Ploss_tank = ((itank_RMS**2)*Z2).real
        Ploss_rc = ((irc_RMS**2)*Z3).real - PBatt
        Total_Power = PBatt + Ploss_loop + Ploss_tank + Ploss_rc
        Ipa = (Total_Power/pa_efficiency) / (Vpa * 2*np.sqrt(2)/np.pi)
        # DEAD: Admittance is computed but never stored (its append below is
        # commented out) and the Ypa chart was dropped.
        Admittance = Ipa / Vpa





        # Currents normalised to the RC current - the shape of the link
        # independent of drive level. mag_normal below turns them into a single
        # figure of merit which, note, is ALSO used as the inverse line width
        # and font size of every plot for this config.
        Itank_normalized = (1j*M12*Z3 + M13*M23*omega) / (M12*M23*omega + 1j*M13*Z2) #I_Tank/I_RC
        Iloop_normalized = (-((M23**2)*(omega**2)) - Z2*Z3) / (omega*(M12*M23*omega + 1j*M13*Z2)) #I_Loop/I_RC
        abs_Itank = np.abs(Itank_normalized)
        abs_Iloop = np.abs(Iloop_normalized)
        # V_Ratio = float(Vrect) / float(Vpa)
        
        # ---- store this distance's results ----
        abs_Iloop_vs_distance.append(abs_Iloop)
        abs_Itank_vs_distance.append(abs_Itank)
        L_loop_vs_distance.append(L_loop)
        M13_vs_distance.append(M13)
        M12_vs_distance.append(M12)
        L_tank_vs_distance.append(L_tank)
        M23_vs_distance.append(M23)
        L_rc_vs_distance.append(L_rc)
        k12_vs_distance.append(k12)
        k13_vs_distance.append(k13)
        k23_vs_distance.append(k23)
        M32_vs_distance.append(M32)
        M31_vs_distance.append(M31)
        M21_vs_distance.append(M21)
        loop_resonant_f_vs_distance.append(loop_shifted_f)
        loop_frequency_shifts.append(loop_tuning_shift)
        tank_resonant_f_vs_distance.append(Quantity(tank_shifted_f, 'Hz'))
        tank_frequency_shifts.append(tank_tuning_shift)
        rc_resonant_f_vs_distance.append(rc_shifted_f)
        rc_frequency_shifts.append(rc_tuning_shift)
        itank_tests.append(itank)
        iloop_tests.append(iloop)
        irc_tests.append(irc)
        Vpa_tests.append(Vpa)
        Ipa_tests.append(Ipa)
        Pbatt_tests.append(PBatt)
        #Ypa_tests.append(Admittance)
        vrect_tests.append(Vrect)

        # Only true at the first test distance. Feeds the lateral-offset pane,
        # which is no longer plotted - dead.
        if(rc_y_translation == 0):
            vratio_lateral_tests.append(Vrect)
            tank_resonant_f_vs_lateral_offset.append(Quantity(tank_shifted_f, 'Hz'))

    
    # Every table value is reported at the CLOSEST test distance, index 0.
    k13_displays.append('{:.3f}'.format(k13_vs_distance[0]))
    k23_displays.append('{:.3f}'.format(k23_vs_distance[0]))
    k12_displays.append('{:.3f}'.format(k12_vs_distance[0]))
    M13_displays.append(Quantity(M13_vs_distance[0], 'H'))
    M23_displays.append(Quantity(M23_vs_distance[0], 'H'))
    M12_displays.append(Quantity(M12_vs_distance[0], 'H'))

    L_loop_displays.append(L_loop_displays_vs_distance[0])
    L_tank_displays.append(L_tank_displays_vs_distance[0])
    L_rc_displays.append(L_rc_displays_vs_distance[0])
    

    # ---- Text log for this config (print_and_log echoes to stdout AND to
    # ./results/ID<timestamp>/) ----
    nff.print_and_log(f"\n\n\nRun {run_count}/{num_configs}; {coilset.config_name}\n---------------------------------", timestamp)
    nff.print_and_log("---------------------------------", timestamp)
    nff.print_and_log(str(coilset), timestamp)
    nff.print_and_log("\n---------------------------------", timestamp)
    nff.print_and_log("Inductances:", timestamp)
    nff.print_and_log('L_rc: ' + str(L_rc_display), timestamp)
    nff.print_and_log('\nL_loop: ' + str(L_loop_display), timestamp)
    nff.print_and_log('L_tank: ' + str(L_tank_display), timestamp)
    nff.print_and_log('\nParameters vs Distance:', timestamp)
    nff.print_and_log(f"RC Test Distances (mm): {rc_test_distances}", timestamp)
    nff.print_and_log('L_loops: ' + str(L_loop_displays_vs_distance), timestamp)
    nff.print_and_log("L_tanks: " + str(L_tank_displays_vs_distance), timestamp)
    nff.print_and_log("L_rcs: " + str(L_rc_displays_vs_distance), timestamp)
    nff.print_and_log('k13s: ' + str(k13_vs_distance), timestamp)
    nff.print_and_log("k23s: " + str(k23_vs_distance), timestamp)
    nff.print_and_log("k12s: " + str(k12_vs_distance), timestamp)
    nff.print_and_log('\nMutual Inductances:', timestamp)
    nff.print_and_log("M13: " + str(M13_vs_distance), timestamp)
    nff.print_and_log("M23: " + str(M23_vs_distance), timestamp)
    nff.print_and_log("M12: " + str(M12_vs_distance), timestamp)
    nff.print_and_log('M21: ' + str(M21_vs_distance), timestamp)
    nff.print_and_log('M32: ' + str(M32_vs_distance), timestamp)
    nff.print_and_log('M31: ' + str(M31_vs_distance), timestamp)

    # Emit M and k vs distance as LTspice ".param X=table(D, ...)" directives so
    # the whole sweep can be pasted straight into a spice deck as a
    # distance-dependent coupling model.
    M12_string = ".param M12=table(D"
    k12_string = ".param k12=table(D"
    M13_string = ".param M13=table(D"
    k13_string = ".param k13=table(D"
    M23_string = ".param M23=table(D"
    k23_string = ".param k23=table(D"
    for i in range(len(rc_test_distances)):
        M12_string+=f", {rc_test_distances[i]}, {M12_vs_distance[i]}"
        k12_string+=f", {rc_test_distances[i]}, {k12_vs_distance[i]}"
        M23_string+=f", {rc_test_distances[i]}, {M23_vs_distance[i]}"
        k23_string+=f", {rc_test_distances[i]}, {k23_vs_distance[i]}"
        M13_string+=f", {rc_test_distances[i]}, {M13_vs_distance[i]}"
        k13_string+=f", {rc_test_distances[i]}, {k13_vs_distance[i]}"
    M12_string += ")"
    k12_string += ")"
    M13_string += ")"
    k13_string += ")"
    M23_string += ")"
    k23_string += ")"

    

    nff.print_and_log(M12_string, timestamp)
    nff.print_and_log(k12_string, timestamp)
    nff.print_and_log(M13_string, timestamp)
    nff.print_and_log(k13_string, timestamp)
    nff.print_and_log(M23_string, timestamp)
    nff.print_and_log(k23_string, timestamp)

    nff.print_and_log("C1: " + str(C1), timestamp)
    nff.print_and_log("C2: " + str(C2), timestamp)
    nff.print_and_log("C3: " + str(C3), timestamp)

    # Trace radii / layer positions, useful for cross-checking the FEMM drawing
    # against the real PCB layout.
    print("Tank Turn Radii:")
    print(tank_turn_radii)
    print("")

    print("Loop Turn Radii:")
    print(loop_turn_radii)
    print("")

    print("RC Turn Radii:")
    print(rc_turn_radii)
    print("")


    nff.print_and_log("Z1: " + str(Z1), timestamp)
    nff.print_and_log("Z2: " + str(Z2), timestamp)
    nff.print_and_log("Z3: " + str(Z3), timestamp)

    # Console dump in a copy-paste-friendly form.
    for i in range(0, len(rc_test_distances)):
        print(f"At {rc_test_distances[i]}mm : M13: {M13_vs_distance[i]}, M23 : {M23_vs_distance[i]}, M12: {M12_vs_distance[i]}")
    
    print(f"M13s = {M13_vs_distance}")
    print(f"M23s = {M23_vs_distance}")
    print(f"M12 = {M12_vs_distance}")
    print(f'distances = {rc_test_distances}')

    # femm.mo_zoom(0, -400, 400, 400)
    
    # ---- Field-based loss extraction -------------------------------------
    # Everything above ran on arbitrary probe currents. Now do ONE more solve
    # with the real currents the circuit model predicts at the closest test
    # distance (converted RMS -> peak), then integrate the losses out of that
    # single field solution.
    # The geometry is back at rc_test_distances[0] because get_inductances
    # undoes each of its translations, so currents and geometry are consistent.
    # The zeros below are the defaults for configs with no ferrite / no shield.
    TC_ferrite_flux_density_display = 0
    RC_ferrite_flux_density_display = 0
    tc_ferrite_loss = 0
    rc_ferrite_loss = 0
    tc_shield_loss = 0
    rc_shield_loss = 0


    close_loop_current = iloop_tests[0]*np.sqrt(2)
    close_tank_current = itank_tests[0]*np.sqrt(2)
    close_rc_current = irc_tests[0]*np.sqrt(2)
    nff.set_circuit_currents(close_loop_current, coilset.num_loop_turns, close_tank_current, coilset.num_tank_turns, close_rc_current, coilset.num_rc_turns)

    # mi_analyze throws if the mesher chokes; pause on the input() so the model
    # can be inspected in the FEMM GUI instead of losing the whole run.
    try:
        femm.mi_analyze()
        femm.mi_loadsolution()
    except BaseException as error:
        print('Caught this error: ' + repr(error))
        input("Pausing for debug")

    
    femm.mo_zoom(0, -100, 100, 100)
    #input("Ferrite Flux Check")


    # Peak |B| sampled just inside the ferrite face - the number that says
    # whether the material is heading for saturation.
    # get_block_losses integrates FEMM's total-loss result over the block that
    # contains the probe point, hence the (radius/2, midpoint_y) coordinates.
    if (coilset.tank_ferrite_present):
        TC_ferrite_flux_density = nff.get_peak_flux_density_of_rect(coilset.tank_ferrite_outer_r, ferrite_top_edge-.01)
        TC_ferrite_flux_density_display = Quantity(TC_ferrite_flux_density, 'T')
        tc_ferrite_loss = nff.get_block_losses(coilset.tank_ferrite_outer_r/2, tc_ferrite_midpoint_y)

    if (coilset.rc_ferrite_present):
        RC_ferrite_flux_density = nff.get_peak_flux_density_of_rect(coilset.rc_ferrite_outer_r, rc_ferrite_bottom+.01)
        RC_ferrite_flux_density_display = Quantity(RC_ferrite_flux_density, 'T')
        #print(f"RC ferrite flux: {RC_ferrite_flux_density_display}")
        rc_ferrite_loss = nff.get_block_losses(coilset.rc_ferrite_outer_r/2, rc_ferrite_midpoint_y)

    if (coilset.tc_shield):
        tc_shield_loss = nff.get_block_losses(coilset.tc_shield_radius/2, tc_shield_midpoint_y)

    if (coilset.rc_shield):
        rc_shield_loss = nff.get_block_losses(coilset.rc_shield_radius/2, rc_shield_midpoint_y)


    # Ferrite and shield losses into the results table.
    tc_ferrite_losses.append(Quantity(tc_ferrite_loss, 'W'))
    rc_ferrite_losses.append(Quantity(rc_ferrite_loss, 'W'))

    tc_shield_losses.append(Quantity(tc_shield_loss, 'W'))
    rc_shield_losses.append(Quantity(rc_shield_loss, 'W'))

    # The "*_losses" table rows actually hold ESR in OHMS here, derived from the
    # solved field, while the get_coil_losses calls below give WATTS. The
    # commented-out lines are the older watts-based version.
    # LABEL MISMATCH: the table row labels say "mOhms" but Quantity is given
    # 'Ohms' - the printed values are ohms, so either the label or the unit
    # should change.
    #loop_loss = nff.get_coil_losses(loop_turn_radii, loop_trace_ys, coilset.loop_trace_thickness)
    #loop_losses.append(Quantity(loop_loss, 'W'))
    loop_esr = nff.loop_ESR('loop', coilset.num_loop_turns, close_loop_current)
    loop_losses.append(Quantity(loop_esr, 'Ohms'))


    # tank_loss = nff.get_coil_losses(tank_turn_radii, tank_trace_ys, coilset.tank_trace_thickness)
    # tank_losses.append(Quantity(tank_loss, 'W'))
    tank_esr = nff.loop_ESR('tank', coilset.num_tank_turns, close_tank_current)
    tank_losses.append(Quantity(tank_esr, 'Ohms'))

    
    # rc_loss = nff.get_coil_losses(rc_turn_radii, rc_trace_ys, coilset.rc_trace_thickness)
    # rc_losses.append(Quantity(rc_loss, 'W'))
    rc_esr = nff.loop_ESR('rc', coilset.num_rc_turns, close_rc_current)
    rc_losses.append(Quantity(rc_esr, 'Ohms'))

    
    # Per-trace ohmic + proximity loss in watts, summed over every trace of
    # every coil (this is what feeds total_loss and the efficiency figure).
    rc_loss = nff.get_coil_losses(rc_turn_radii, rc_trace_ys, coilset.rc_trace_thickness)
    tank_loss = nff.get_coil_losses(tank_turn_radii, tank_trace_ys, coilset.tank_trace_thickness)
    print(f"tank loss: {tank_loss} and tank current is: {close_tank_current}")
    loop_loss = nff.get_coil_losses(loop_turn_radii, loop_trace_ys, coilset.loop_trace_thickness)


    # System efficiency estimate: delivered DC power minus every loss FEMM can
    # account for, normalised to delivered power.
    total_loss = tc_ferrite_loss + rc_ferrite_loss + tc_shield_loss + rc_shield_loss + loop_loss + tank_loss + rc_loss
    total_losses.append(Quantity(total_loss, 'W'))

    tank_ferrite_max_flux.append(TC_ferrite_flux_density_display)
    rc_ferrite_max_flux.append(RC_ferrite_flux_density_display)  
    
    coil_efficiency = np.abs((Pbatt_tests[0] - total_loss) / Pbatt_tests[0])


    print(f"I_tank/I_rc: {abs_Itank}")
    print(f"I_loop/I_rc: {abs_Iloop}")
    Itank_displays.append('{:.3f}'.format(abs_Itank_vs_distance[0]))
    Iloop_displays.append('{:.3f}'.format(abs_Iloop_vs_distance[0]))
    efficiency_tests.append('{:.3f}%'.format(coil_efficiency*100))


    # mag_normal: magnitude of the (Iloop, Itank) vector normalised to Irc. Used
    # as a figure of merit and - see the plotting below - as the inverse of line
    # width and label font size, so "better" configs are drawn thicker.
    mag_normal = np.sqrt(abs_Itank_vs_distance[0]**2 + abs_Iloop_vs_distance[0]**2)
    current_mag_displays.append('{:.3f}'.format(mag_normal))

    nff.print_and_log("Magnitude of normalized currents: " + str(mag_normal), timestamp)

    #input("Pausing for loss review")
    

    

    # ---- Plotting for this config ----------------------------------------
    # Every chart shares the same x axis (RC distance in mm) and is annotated
    # with the run number at one end so overlapping traces stay identifiable.
    #top_right.plot(rc_test_distances, vlvss, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)
    vpa_pane.plot(rc_test_distances, Vpa_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
    ipa_pane.plot(rc_test_distances, Ipa_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")

    vrect_pane.plot(rc_test_distances, vrect_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
    #efficiency_pane.plot(rc_test_distances, efficiency_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)


    Iloop_pane.plot(rc_test_distances, iloop_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
    Itank_pane.plot(rc_test_distances, itank_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
    Irc_pane.plot(rc_test_distances, irc_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)

    Iloop_pane.text(rc_test_distances[-1]+5, iloop_tests[-1], str(run_count), fontsize=(20/mag_normal), color=plot_color)
    Itank_pane.text(rc_test_distances[-1]+5, itank_tests[-1], str(run_count), fontsize=(20/mag_normal), color=plot_color)
    Irc_pane.text(rc_test_distances[-1]+5, irc_tests[-1], str(run_count), fontsize=(20/mag_normal), color=plot_color)

    #tank_lateral_tuning_pane.plot(rc_x_translations, vratio_lateral_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)
    pbatt_pane.plot(rc_test_distances, Pbatt_tests, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
            

    label_pos_x = rc_test_distances[0]-5
    label_pos_y = Vpa_tests[0]

    tank_tuning_pane.plot(rc_test_distances, tank_resonant_f_vs_distance, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75, marker=".")
 
    if (Vpa_tests[0] < VPA_CHART_Y_LIM):
        for i in range(0,len(rc_test_distances),1):
            if (Vpa_tests[i] > VPA_CHART_Y_LIM):
                vpa_pane.text(rc_test_distances[i]-5, Vpa_tests[i], str(run_count), fontsize=(11/mag_normal), color=plot_color)
                break
    
    vpa_pane.text(label_pos_x, label_pos_y, str(run_count), fontsize=(11/mag_normal), color=plot_color)
    ipa_pane.text(rc_test_distances[0]-5, Ipa_tests[0], str(run_count), fontsize=(11/mag_normal), color=plot_color)
    #if(iloop_tests[0] < 2):
    #    efficiency_pane.text(rc_test_distances[0]-5, efficiency_tests[0], str(run_count), fontsize=(20/mag_normal), color=plot_color)
    if(vrect_tests[0] > 40 and vrect_tests[0] < 100 ):
        vrect_pane.text(label_pos_x, vrect_tests[0], str(run_count), fontsize=(20/mag_normal), color=plot_color)

    # Old 3-D radiation-pattern experiment; that pane no longer exists.
    # if(radiation_pattern_testing):
        # radiation_pattern_pane.plot_surface(rad_x, rad_y, vlvs_radiated, label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)
    # else:
        # rad iation_pattern_pane.plot(rad_x, vlvs_radiated[0], label=run_num, color=plot_color, linewidth=3/mag_normal, alpha=0.75)

    # ---- Tear down ---------------------------------------------------------
    # Wipe every drawn entity so the next config starts from an empty FEMM
    # document. The rectangle starts at x = 0.01 to avoid grabbing anything
    # sitting on the symmetry axis, and the trailing 4 means "all entity types".
    #input("paused at 904")
    femm.mi_selectrectangle(0.01, -190,220,190,4)
    femm.mi_deleteselected()
    femm.mi_clearselected()
    
    # Snapshot this coilset's field values into the config table columns.
    # Relies on fields(coilset) coming back in declaration order and on
    # parameter_count matching the `parameters` dict - see the note there.
    parameter_fields = []
    for field in fields(coilset):
        #print(f'name:{field.name}, value:{}')
        parameter_fields.append(getattr(coilset, field.name))
    #print(parameter_fields)


    #skip over the name field
    for i in range(0, parameter_count):
        #print(parameter_fields[i])
        parameter_outputs[i].append(parameter_fields[i])

    # The circuits must be deleted too, or the next config's mi_addcircprop
    # calls collide with the leftover names.
    for i in range(coilset.num_loop_turns):
        femm.mi_deletecircuit('loop'+str(i))

    for i in range(coilset.num_tank_turns):
        femm.mi_deletecircuit('tank'+str(i))

    for i in range(coilset.num_rc_turns):
        femm.mi_deletecircuit('rc'+str(i))

    # Crude scalar score for hand-ranking configs: how far Vrect strays from the
    # 80 V target across the whole sweep, plus a penalty for unbalanced coil
    # currents.
    # DEAD: `quality` itself is never printed or stored - only its two halves
    # are printed. Either print it or drop the line.
    vrect_delta = 0
    for Vrect_at_distance in vrect_tests:
        vrect_delta += np.abs(80 - Vrect_at_distance)

    current_mag_quality = np.abs(mag_normal - 1) * 10
    quality = vrect_delta + current_mag_quality
    print(f"Current Quality: {current_mag_quality}")
    print(f"Vrect Quality: {vrect_delta}")

        
        
           




# ===========================================================================
# FIGURE COSMETICS, TABLES AND OUTPUT
# Everything below runs ONCE, after every config has been simulated.
# Axis styling is repeated per pane rather than looped, so each chart's ticks
# and labels can be tweaked independently.
# ===========================================================================
# #fig2.suptitle('FEMM Analysis ' + ("{:%m-%d-%y}".format(datetime.datetime.today()))+'\nTest ID: '+timestamp, fontsize=12)

vpa_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
vpa_pane.set_ylabel('VPA (V)', alpha=.2)
vpa_pane.set_title('VPA @ Max Power Point vs distance')
vpa_pane.grid(axis='y', color='0.95')
vpa_pane.grid(axis='x', color='0.95', alpha=.2)
#vpa_pane.set_ylim(0, VPA_CHART_Y_LIM)
vpa_pane.set_xticks(np.arange(0, 100.1, 5))
#vpa_pane.tick_params(axis='both', which=u'both', alpha=.3)
plt.setp(vpa_pane.get_xticklabels(), alpha=0.3)


ipa_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
ipa_pane.set_ylabel('IPA (A)', alpha=.2)
ipa_pane.set_title('IPA @ Max Power Point vs distance')
ipa_pane.grid(axis='y', color='0.95')
ipa_pane.grid(axis='x', color='0.95', alpha=.2)
ipa_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(ipa_pane.get_xticklabels(), alpha=0.3)

Iloop_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
Iloop_pane.set_title('Iloop @ Max Power Point vs distance')
Iloop_pane.set_ylabel('Iloop Peak (A)', alpha=.2)
#Iloop_pane.set_yticks(np.arange(0, 20.1, 5))
#Iloop_pane.set_xticks(np.arange(0, 100.1, 50))
Iloop_pane.grid(axis='y', color='0.95')
Iloop_pane.grid(axis='x', color='0.95')
Iloop_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(Iloop_pane.get_xticklabels(), alpha=0.3)
plt.setp(Iloop_pane.get_xticklabels(), alpha=0.3)

Itank_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
Itank_pane.set_title('Itank @ Max Power Point vs distance')
Itank_pane.set_ylabel('Itank Peak (A)', alpha=.2)
#Itank_pane.set_yticks(np.arange(0, 20.1, 5))
#Itank_pane.set_xticks(np.arange(0, 100.1, 50))
Itank_pane.grid(axis='y', color='0.95')
Itank_pane.grid(axis='x', color='0.95', alpha=.5)
Itank_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(Itank_pane.get_xticklabels(), alpha=0.3)

Irc_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
Irc_pane.set_title('Irc @ Max Power Point vs distance')
Irc_pane.set_ylabel('Irc Peak (A)', alpha=.2)
#Irc_pane.set_yticks(np.arange(0, 20.1, 5))
#Irc_pane.set_xticks(np.arange(0, 100.1, 50))
Irc_pane.grid(axis='y', color='0.95')
Irc_pane.grid(axis='x', color='0.95', alpha=.5)
Irc_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(Irc_pane.get_xticklabels(), alpha=0.3)

# efficiency_pane.set_xlabel('Longitudinal distance of RC to Tank (mm)', alpha=.2)
# efficiency_pane.set_title('Efficiency @ Max Power point vs distance')
# efficiency_pane.set_ylabel('Efficiency', alpha=.2)
# efficiency_pane.grid(axis='y', color='0.95')
# efficiency_pane.grid(axis='x', color='0.95', alpha=.5)
# efficiency_pane.set_xticks(np.arange(0, 100.1, 5))
# plt.setp(efficiency_pane.get_xticklabels(), alpha=0.3)
# #efficiency_pane.set_ylim(0, 2)

vrect_pane.set_xlabel('RC longitudinal offset (mm)', alpha=.2)
vrect_pane.set_title('Vrect @ Max Power Point vs distance')
vrect_pane.set_ylabel('Vrect', alpha=.2)
vrect_pane.grid(axis='y', color='0.95')
vrect_pane.grid(axis='x', color='0.95', alpha=.5)
vrect_pane.set_xticks(np.arange(0, 100.1, 5))
#vrect_pane.set_ylim(0, 100)
plt.setp(vrect_pane.get_xticklabels(), alpha=0.3)

tank_lateral_tuning_pane.set_xlabel('RC latitudinal offset (mm)', alpha=.2)
tank_lateral_tuning_pane.set_title('Tank De-tuned Resonant Frequency (Hz) vs RC lateral offset')
tank_lateral_tuning_pane.set_ylabel('MHz', alpha=.2)
tank_lateral_tuning_pane.grid(axis='y', color='0.95')
tank_lateral_tuning_pane.grid(axis='x', color='0.95', alpha=.5)
plt.setp(tank_lateral_tuning_pane.get_xticklabels(), alpha=0.3)

pbatt_pane.set_xlabel('RC latitudinal offset (mm)', alpha=.5)
pbatt_pane.set_title('Max Output Power vs RC Distance)')
pbatt_pane.set_ylabel('Power (W)', alpha=.2)
pbatt_pane.grid(axis='y', color='0.95')
pbatt_pane.grid(axis='x', color='0.95', alpha=.5)
pbatt_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(pbatt_pane.get_xticklabels(), alpha=0.3)

tank_tuning_pane.set_xlabel('RC longitudinal offset (mm)', alpha=.2)
tank_tuning_pane.set_title('Tank De-tuned Resonant Frequency vs RC y-distance')
tank_tuning_pane.set_ylabel('MHz', alpha=.2)
tank_tuning_pane.grid(axis='y', color='0.95')
tank_tuning_pane.grid(axis='x', color='0.95', alpha=.2)
tank_tuning_pane.set_xticks(np.arange(0, 100.1, 5))
plt.setp(tank_tuning_pane.get_xticklabels(), alpha=0.3)







#vrect_pane.set_ylim(0, 2)

# if(radiation_pattern_testing):
#     radiation_pattern_pane.set_xlabel('RC longitudinal offset (mm)')
#     #radiation_pattern_pane.set_title('Itank/Irc vs Longitudinal offset')
#     radiation_pattern_pane.set_ylabel('RC latitudinal offset (mm)')
#     radiation_pattern_pane.set_zlabel('result')
#     radiation_pattern_pane.grid(axis='z', color='0.95')
# else:
#     radiation_pattern_pane.set_ylabel('VL/VS')
#     radiation_pattern_pane.set_title(f'VL/VS @ {rc_test_distances[0]}mm offset, lateral sweep')
# radiation_pattern_pane.set_xlabel('RC lateral offset (mm)')
# radiation_pattern_pane.grid(axis='y', color='0.95')
# radiation_pattern_pane.grid(axis='x', color='0.95')



#Mutual Inductance Chart
# DEAD: x and width are leftovers from a grouped bar chart that no longer
# exists, and so is the "Mutual Inductance Chart" heading above them.
x = np.arange(num_configs) # the label locations
width = 0.25  # the width of the bars


# `rows` must stay in the SAME ORDER as the config_results.append calls below -
# the table zips them positionally. Adding a result means adding its row label
# at the matching index.
rows = [f'k13 @ {rc_test_distances[0]}mm', f'k23 @ {rc_test_distances[0]}mm', 'k12', f'M13 @ {rc_test_distances[0]}mm', f'M23 @ {rc_test_distances[0]}mm', 'M12', 'L_loop - just PCB (L1)','L_loop - full stack-up (L1)', 'L_tank  - just PCB (L2)', 'L_tank  - full stack-up (L2)','L_rc - just PCB (L3)', 'L_rc - full stack-up (L3)','Max flux in TC Ferrite (T)','Max flux in RC Ferrite (T)', 'Magnitude of normalized currents', f'Efficiency at  @ {rc_test_distances[0]}mm', 'Loop ESR (mOhms)', 'Tank ESR (mOhms)', 'RC ESR (mOhms)','TC Ferrite Losses (W)', 'TC Shield Losses (W)', 'RC Ferrite Losses (W)', 'RC Shield Losses (W)', 'Total Losses (W)'] 

config_results.append(k13_displays)
config_results.append(k23_displays)
config_results.append(k12_displays)
config_results.append(M13_displays)
config_results.append(M23_displays)
config_results.append(M12_displays)
config_results.append(L_loop_solo_displays)
config_results.append(L_loop_fully_assembled_displays)
config_results.append(L_tank_solo_displays)
config_results.append(L_tank_fully_assembled_displays)
config_results.append(L_rc_solo_displays)
config_results.append(L_rc_fully_assembled_displays)
config_results.append(tank_ferrite_max_flux)
config_results.append(rc_ferrite_max_flux)
config_results.append(current_mag_displays)
config_results.append(efficiency_tests)
config_results.append(loop_losses)
config_results.append(tank_losses)
config_results.append(rc_losses)
config_results.append(tc_ferrite_losses)
config_results.append(tc_shield_losses)
config_results.append(rc_ferrite_losses)
config_results.append(rc_shield_losses)
config_results.append(total_losses)





# Older single-table layout, superseded by results_table2 at the bottom.
# bot_right.set_axis_off()
# results_table = bot_right.table(cellText=config_results, rowLabels=rows,colColours=colors,colLabels=configs,loc='center', cellLoc='center')


#Display of traces
# Coil cross-section pane: fixed mm box so every config is drawn to the same
# scale (x = radius, y = distance along the axis).
disp_pane.set_ylim(neg_y_dim, pos_y_dim)
disp_pane.set_xlim(neg_x_dim, pos_x_dim)
disp_pane.grid(axis='y', color='0.95')

#Second Window
# DEAD: results_bbox is computed and never used - the tables are positioned
# with the pane_bbox literal further down instead.
results_pane_positions = results_pane.get_position()
#print(results_pane_positions)
results_bbox = xfrms.Bbox.from_bounds(results_pane_positions.x0, results_pane_positions.y0, results_pane_positions.width, results_pane_positions.height)


config_pane.set_axis_off()
results_pane.set_axis_off()






# ---------------------------------------------------------------------------
# CONFIG TABLE ROW PRUNING
# Drop rows that carry no information for this run: the config name (it is
# already the column header) and the ferrite/backplane geometry when nothing
# uses it. param_names and parameter_outputs must be popped IN LOCKSTEP.
# FRAGILE: the indices below are hard-coded positions into the `parameters`
# dict. Insert or reorder a key up there and these pops silently delete the
# wrong rows. The repeated pop(0)/pop(5)/pop(10) calls are each removing a
# contiguous run of 5, 10 or 2 entries one at a time.
# It also inherits the earlier caveat - the flags come from `parameters`, not
# from the coilsets actually simulated.
# ---------------------------------------------------------------------------
param_names = list(parameters.keys())

#remove config name from param list
param_names.pop(0)
parameter_outputs.pop(0)

if(no_backplane_present):
    param_names.pop(10)
    param_names.pop(10)
    parameter_outputs.pop(10) #deletes 5th element
    parameter_outputs.pop(10) #deletes what was the 6th element before the first pop

if(no_tank_ferrite_present and no_rc_ferrite_present):
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    #deletes param_names[1:6] (that's exclusive!)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
elif(no_tank_ferrite_present):
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    param_names.pop(0)
    
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
    parameter_outputs.pop(0)
elif(no_rc_ferrite_present):
    param_names.pop(5)
    param_names.pop(5)
    param_names.pop(5)
    param_names.pop(5)
    param_names.pop(5)
    
    parameter_outputs.pop(5)
    parameter_outputs.pop(5)
    parameter_outputs.pop(5)
    parameter_outputs.pop(5)
    parameter_outputs.pop(5)

# Both tables fill their whole pane rather than matplotlib's default centring.
pane_bbox = [0, 0, 1, 1]

#print(parameter_outputs)


# Config table: bold any cell whose value differs from the config to its left,
# so the deltas between compared configs pop out visually.
# CAUTION: the comparison casts cell text with float(), so a non-numeric
# parameter left in the table (anything string-valued) raises ValueError here.
config_table = config_pane.table(cellText=parameter_outputs, rowLabels=param_names,colColours=colors,colLabels=configs, cellLoc='center', bbox=pane_bbox)
for (row, col), cell in config_table.get_celld().items():
    if (col > 0 and row > 0):
        if (float(cell.get_text().get_text()) != float(config_table[row, col-1].get_text().get_text())):
            cell.set_text_props(fontproperties=FontProperties(weight='bold'))



config_table.auto_set_font_size(False)
#param_table.set_fontsize(12)

# Shrink the fonts so a wide sweep still fits; PAD tightens the cell margins.
for key,cell in config_table.get_celld().items():
    cell.PAD = 0.03
    if(key[0] > 0 and key[1] > -1): #This is a result cell
        cell.set_fontsize(5)
    else:
        cell.set_fontsize(5)


#print(config_results)
#print(rows)

results_table2 = results_pane.table(cellText=config_results, rowLabels=rows,colColours=colors,colLabels=configs, cellLoc='center', bbox=pane_bbox)

results_table2.auto_set_font_size(False)

for key,cell in results_table2.get_celld().items():
    cell.PAD = 0.03
    if(key[0] > 0 and key[1] > -1): #This is a result cell
        cell.set_fontsize(9)
    else:
        cell.set_fontsize(6)



#top.set_ylabel('Flux Density underneath center turn of coil (T)')\
# fig.set_size_inches(36, 18)
#wide monitor resolution: 3440x1440

#300dpi -> 

# Canvas sized for a wide monitor and saved as SVG so the tables stay legible
# when zoomed. subplots_adjust is hand-tuned to the 6x4 layout above.
fig2.set_size_inches(36, 12)  
# fig.tight_layout(pad=1.0, w_pad=1.0, h_pad=1.0)
# fig.savefig('./results/ID'+timestamp+'/result_chart.svg', dpi=300, format="svg")

fig2.subplots_adjust(left=0.062, bottom=0.036, right=.99, top=.979, wspace=.15, hspace=.5)
fig2.savefig(f'./results/ID{timestamp}/ID{timestamp}.svg', dpi=400, format="svg", bbox_inches="tight")



# Leave the FEMM output window on a sensible zoom, block on the matplotlib
# window, then shut FEMM down. plt.show() blocks, so closefemm() only runs once
# the figure window is closed.
femm.mo_zoom(0, -100, 100, 100)
plt.show()

#input("Press Enter to continue...")


femm.closefemm()

