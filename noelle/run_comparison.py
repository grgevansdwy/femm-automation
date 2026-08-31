# =============================================================================
# run_comparison.py                                              Noel Hosking
#
# Callable entry point extracted from compare_coilsets.py so the comparison
# can be triggered from other code (e.g. the web UI's job runner) as well as
# from the shell. Compares 3-coil WPT links: Loop (L1, PA-driven) -> Tank (L2,
# free resonator) -> RC (L3, receiver). Per coilset: drive FEMM (2-D
# axisymmetric, 6.78 MHz, mm) for the L/M matrix over a range of RC
# separations, solve a 3-mesh circuit model on it, write one matplotlib
# dashboard + log to <root>/ID<run_id>/.
#
# Geometry in mm: x = radius, y = axis of revolution, nothing crosses x=0.
# Currents are PEAK amps unless the name ends in _RMS.
# "TC" = transmit side (Loop + Tank), "RC" = receive side.
# =============================================================================

# ============================ IMPORTS =============================
import femm
import numpy as np
from pathlib import Path
from repositories import coilset_repository as cc
from services import femm_service as nff
from services import circuit_solver
from models.config_result import ConfigResult, RunResults, SweepPoint
from repositories.run_result_repository import RunResultRepository
from view.dashboard import Dashboard
from models.coilset import table_fields, table_labels
# LTspice path disabled: noelle/ltspiceautomator/ is an empty, unpopulated
# module (previously imported unconditionally, blocking every import of this
# file). The "NEW WAY" MPP solve below was already dead (its own call is
# commented out), so nothing downstream depends on this import.
# import ltspiceautomator.ltspice_automator as lta

# ============================ CONSTANTS =============================
from config import (
    omega,
    loop_group, tank_group, rc_group,
    tank_coil_current_target, R_tank,
    loop_current_target, R_loop,
    rc_test_current_target, R_rc,
    Z_cable_RC,
    Z_pa, Vpa, Vs,
    RL, RL_ac, Z_ml, Z_mc, ZL_eq,
    backplane_length, backplane_thickness,
)

DEFAULT_RC_TEST_DISTANCES = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 75, 100]


def run_comparison(coilsets, rc_test_distances=None, run_id=None, root=Path('./results')):
    """coilsets: list[CoilSet], one entry per table column / chart colour.

    Writes a dashboard SVG, a text log and a summary JSON under
    root/ID<run_id>/ (run_id defaults to a DDHHMM timestamp - see
    RunResultRepository) and returns (run, results): the RunResults model and
    the RunResultRepository that owns that directory.
    """
    rc_test_distances = rc_test_distances or DEFAULT_RC_TEST_DISTANCES
    coilset_array = coilsets

    # ============================ INITIALIZE OUTPUT =============================
    # Every matplotlib object lives in view/dashboard.py; this file holds only the
    # Owns ./results/ID<run_id>/ - the run directory, its log and its figure.
    results = RunResultRepository(run_id=run_id, root=root)
    dashboard = Dashboard(run_id=results.run_id) # This initializes a blank figures

    # ============================ RESULT ACCUMULATORS =============================
    # One ConfigResult per coilset, appended at the end of each main-loop iteration.
    run = RunResults(timestamp=results.run_id) # This initializes blank result model (an empty list of each config_result object)
    run_count = 0

    # ============================ CONFIG TABLE =====================================
    # Create a parameter list of lists where each label represent one list
    parameter_outputs = [[] for _ in table_fields()]

    # =============================== GEOMETRY CHECK ============================
    for coilset in coilset_array:
        print(coilset)
        failure_reason, coilset_valid = nff.check_valid_coilset(coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_spacing, coilset.loop_turn_spacing, coilset.loop_trace_width, coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_width, coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces, coilset.rc_trace_spacing, coilset.rc_turn_spacing, coilset.rc_trace_width)
        if (not coilset_valid):
            raise Exception(f"{failure_reason}")

    # ============== FERRITE & BACKPLANE ==========================
    no_tank_ferrite_present = not any(c.tank_ferrite_present for c in coilset_array)
    no_rc_ferrite_present   = not any(c.rc_ferrite_present   for c in coilset_array)
    no_backplane_present    = not any(c.backplane            for c in coilset_array)

    try:
        # ========================== Boot FEMM ===============================
        # Axisymmetric magnetostatic problem in mm at 6.78 MHz, materials + boundary.
        nff.init_femm()

        # =========================== MAIN LOOP ============================
        # one iteration per coilset = one table column, one chart
        # colour. Each iteration MUST leave the FEMM model empty: one shared document.=
        num_configs = len(list(coilset_array))
        for coilset in coilset_array:
            # ================== FEMM CIRCUIT SETUP =====================
            # Creating turn circuits for simulation ("loop0", "tank3", ...)
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

            # ==================== Labelling & Ferrite Material =======================
            # Legend/table label and a distinct colour by position in the sweep.
            run_count+=1
            run_num = "run-" + str(run_count)

            config_label = coilset.config_name + str(run_num)
            plot_color = dashboard.color_for(run_count, num_configs)

            # FEMM property slots: 1/2 = mu_x/mu_y, 5 = conductivity, 10/11 = loss tangents.
            # GOTCHA: TC and RC share this one 'Ferrite', so the RC ferrite inherits the
            # TANK's mu and loss angle - coilset.rc_mu / rc_loss_angle are never applied.
            femm.mi_modifymaterial('Ferrite', 1, coilset.tank_mu)
            femm.mi_modifymaterial('Ferrite', 2, coilset.tank_mu)
            femm.mi_modifymaterial('Ferrite', 5, 1e-11) #conductivity
            femm.mi_modifymaterial('Ferrite', 10, coilset.tc_loss_angle) #Fj7: .012544, 61: 0.0073
            femm.mi_modifymaterial('Ferrite', 11, coilset.tc_loss_angle)
            femm.mi_zoom(0, -100, 100, 100)

            # ======================= DRAW GEOMETRY =======================
            # draw_and_plot_coil_traces() draws into FEMM and sketches on dashboard.cross_section_pane,
            # returning per-trace radii and per-layer y. Last arg is the group tag.
            # Layout: RC face-down at the first distance, tank at y=0, loop below it.
            rc_turn_radii, rc_trace_ys = nff.draw_and_plot_coil_traces(coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces, coilset.rc_trace_width, coilset.rc_trace_spacing, coilset.rc_turn_spacing, rc_test_distances[0], coilset.rc_trace_thickness, 'trace', 'rc', dashboard.cross_section_pane, plot_color, "-", coilset.num_rc_layers, -coilset.rc_PCB_thickness, rc_group)

            #DRAWING TANK
            tank_turn_radii, tank_trace_ys = nff.draw_and_plot_coil_traces(coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_width, coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_thickness, coilset.tank_trace_thickness,'trace', 'tank', dashboard.cross_section_pane, plot_color, "-", coilset.num_tank_layers, coilset.tank_PCB_thickness, tank_group)

            #DRAWING LOOP
            loop_turn_radii, loop_trace_ys = nff.draw_and_plot_coil_traces(coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_width, coilset.loop_trace_spacing, coilset.loop_turn_spacing, -coilset.tank_PCB_thickness-coilset.loop_offset, coilset.loop_trace_thickness, 'trace', 'loop', dashboard.cross_section_pane, plot_color,  "-", coilset.num_loop_layers, coilset.loop_PCB_thickness, loop_group)

            # Per-turn drive current: target divided across parallel traces and layers,
            # so ampere-turns match a real coil wound from these traces.
            loop_turn_current = nff.get_turn_current(loop_current_target, coilset.num_loop_layers, coilset.num_parallel_loop_traces, coilset.num_loop_turns)
            tank_turn_current = nff.get_turn_current(tank_coil_current_target, coilset.num_tank_layers, coilset.num_parallel_tank_traces, coilset.num_tank_turns)
            rc_turn_current = nff.get_turn_current(rc_test_current_target, coilset.num_rc_layers, coilset.num_parallel_rc_traces, coilset.num_rc_turns)


            #======================== SOLO INDUCTANCE ======================
            # "Solo" inductance: drive one coil, park the other two 300 mm away. No
            # ferrite/shields yet, so these are the "just PCB" rows in the results table.
            loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0,300, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
            L_loop_solo = loop_inductances.L11

            tank_inductances = nff.get_inductances(loop_group, 0, 0, 300, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
            L_tank_solo = tank_inductances.L22

            rc_inductances = nff.get_inductances(loop_group, 0, 0, 300, coilset.num_loop_turns, tank_group, 0, 0, 300, coilset.num_tank_turns, rc_group, rc_turn_current, 0, 0, coilset.num_rc_turns)
            L_rc_solo = rc_inductances.L33

            # =============== FERRITE / SHIELD / BACKPLANE GEOMETRY ==================
            # Stack-up: tc_bottom_y walks DOWN from the loop PCB underside (PCB -> gap ->
            # ferrite -> shield -> backplane); rc_top_y mirrors it above the RC.
            # Each *_midpoint_y is kept because loss integration needs a point INSIDE the block.
            tc_bottom_y = -coilset.tank_PCB_thickness-coilset.loop_offset-coilset.loop_PCB_thickness-coilset.tank_trace_thickness-(2*coilset.loop_trace_thickness)

            #TC Ferrite
            if (coilset.tank_ferrite_present):
                tc_bottom_y -= coilset.tank_ferrite_y_offset
                ferrite_top_edge = tc_bottom_y
                tc_ferrite_midpoint_y = ferrite_top_edge - (coilset.tank_ferrite_thickness/2)
                nff.draw_rectangular_material('Ferrite', coilset.tank_ferrite_thickness, coilset.tank_ferrite_outer_r , tc_bottom_y, loop_group, dashboard.cross_section_pane, plot_color, '-')
                tc_bottom_y -= coilset.tank_ferrite_thickness

            #TC-side Metal shield
            if (coilset.tc_shield):
                tc_bottom_y -= coilset.tc_shield_y_distance
                tc_shield_midpoint_y = tc_bottom_y - (coilset.tc_shield_thickness/2)
                nff.draw_rectangular_material('AluFoil', coilset.tc_shield_thickness, coilset.tc_shield_radius , tc_bottom_y, loop_group, dashboard.cross_section_pane, plot_color, '-')
                tc_bottom_y -= coilset.tc_shield_thickness

            # RC-side stack-up, mirrored above the receive coil.
            rc_top_y = coilset.rc_PCB_thickness + rc_test_distances[0] + (coilset.rc_trace_thickness*2) + coilset.rc_ferrite_y_offset
            rc_ferrite_bottom = rc_top_y

            #RC Ferrite
            if (coilset.rc_ferrite_present):
                rc_ferrite_midpoint_y = rc_top_y + (coilset.rc_ferrite_thickness/2)
                rc_top_y += coilset.rc_ferrite_thickness
                nff.draw_rectangular_material('Ferrite', coilset.rc_ferrite_thickness, coilset.rc_ferrite_outer_r , rc_top_y, rc_group, dashboard.cross_section_pane, plot_color, '-')

            #RC-side Metal shield
            if (coilset.rc_shield):
                rc_top_y += coilset.rc_shield_y_distance
                rc_shield_midpoint_y = rc_top_y + (coilset.rc_shield_thickness/2)
                rc_top_y += coilset.rc_shield_thickness
                nff.draw_rectangular_material('AluFoil', coilset.rc_shield_thickness, coilset.rc_shield_radius , rc_top_y, rc_group, dashboard.cross_section_pane, plot_color, '-')

            # ========================== SIMULATION =====================

            # Reset per-distance accumulator
            sweep_points = []

            #### Coil Tuning Test (LoopPCB +Tank PCB+Ferrite+Backplane) and separately, (RC PCB+Ferrite+Backplane)
            # "Fully assembled" inductance: same three solves but WITH ferrite/shields and
            # the other TC coil in place - tuning caps must be sized against in-situ L.
            loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
            L_loop_fully_assembled = loop_inductances.L11


            tank_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, 300, coilset.num_rc_turns)
            L_tank_fully_assembled = tank_inductances.L22


            rc_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, rc_turn_current, 0, 200, coilset.num_rc_turns)
            L_rc_fully_assembled = rc_inductances.L33


            # Tuning caps computed ONCE per config, then held fixed while distance is
            # swept - fixed C against changing L is what produces the detuning curves.
            C1 = nff.resonant_capacitance(L_loop_fully_assembled, coilset.loop_tune_f*1e6)
            C2 = nff.resonant_capacitance(L_tank_fully_assembled, coilset.tank_tune_f*1e6)
            C3 = nff.resonant_capacitance(L_rc_fully_assembled, coilset.rc_tune_f*1e6)


            #TC-side Metal detuning plane
            # Metal detuning planes (table top / chassis stand-ins), drawn last so they sit
            # outside the stack-up. NOTE: added AFTER C1..C3, so caps are "tuned on the
            # bench, then used near metal". Move this above them to tune in place.
            if (coilset.backplane):
                tc_bottom_y -= coilset.backplane_y_distance - 3 #enclosure_thickness
                tc_backplane_midpoint_y = tc_bottom_y - (backplane_thickness/2)
                nff.draw_rectangular_material('Copper', backplane_thickness, backplane_length/2 , tc_bottom_y, loop_group, dashboard.cross_section_pane, plot_color, '-')
                tc_bottom_y -= backplane_thickness

            if (coilset.rc_backplane):
                rc_top_y += (coilset.rc_backplane_y_distance + backplane_thickness + 3) #6=enclosure thickness
                rc_backplane_midpoint_y = rc_top_y + (backplane_thickness/2)
                nff.draw_rectangular_material('Copper', backplane_thickness, backplane_length/2 , rc_top_y, rc_group, dashboard.cross_section_pane, plot_color, '-')
                rc_top_y += backplane_thickness

            ### Testing at distances with any external metal
            # Distance sweep - the RC is translated, not redrawn. UNUSED: starting_rc_distance.
            starting_rc_distance = rc_test_distances[0]
            rc_y_translations = [rc_test_distance-rc_test_distances[0] for rc_test_distance in rc_test_distances]

                # THREE solves per distance, one per driven coil - get_inductances only
                # recovers the row for the coil carrying current:
                #   loop -> L11, M12, M13 | tank -> L22, M23, M21 | RC -> L33, M31, M32
                # M12 vs M21 should agree by reciprocity, so the gap is a sanity check.
            for i, rc_y_translation in enumerate(rc_y_translations):

                loop_inductances = nff.get_inductances(loop_group, loop_turn_current, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, rc_y_translation, coilset.num_rc_turns)
                L_loop = loop_inductances.L11
                M13 = loop_inductances.M13
                M12 = loop_inductances.M12


                tank_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, tank_turn_current, 0, 0, coilset.num_tank_turns, rc_group, 0, 0, rc_y_translation, coilset.num_rc_turns)
                L_tank = tank_inductances.L22
                M23 = tank_inductances.M23
                M21 = tank_inductances.M21


                rc_inductances = nff.get_inductances(loop_group, 0, 0, 0, coilset.num_loop_turns, tank_group, 0, 0, 0, coilset.num_tank_turns, rc_group, rc_turn_current, 0, rc_y_translation, coilset.num_rc_turns)
                L_rc = rc_inductances.L33
                M31 = rc_inductances.M31
                M32 = rc_inductances.M32

                # Coupling coefficients. k12/k23 stay 0 for tank-less configs so sqrt(L_tank) never divides by zero.
                k13 = M13 / (np.sqrt(L_loop*L_rc))
                k12 = 0
                k23 = 0
                if (coilset.num_tank_turns > 0):
                    k12 = M12 / (np.sqrt(L_loop*L_tank))
                    k23 = M23 / (np.sqrt(L_tank*L_rc))

                # How far each coil has drifted off tune at this distance, given fixed C1/C2/C3.
                loop_shifted_f = nff.resonant_frequency(L_loop, C1)
                loop_tuning_shift = loop_shifted_f - (coilset.loop_tune_f*1e6)

                tank_shifted_f = nff.resonant_frequency(L_tank, C2)
                tank_tuning_shift = tank_shifted_f - (coilset.tank_tune_f*1e6)

                rc_shifted_f = nff.resonant_frequency(L_rc, C3)
                rc_tuning_shift = rc_shifted_f - (coilset.rc_tune_f*1e6)

                # Mesh impedances; the tank-less dummy Z2 lives in circuit_solver.mesh_impedances.
                Z1, Z2, Z3 = circuit_solver.mesh_impedances(
                    omega, L_loop, L_tank, L_rc, C1, C2, C3,
                    Z_pa, R_loop, R_tank, R_rc,
                    Z_cable_TC, Z_cable_RC, ZL_eq, coilset.num_tank_turns)

                # --- Two alternative operating-point solvers ------------------------
                # (a) NEW WAY: hand L/k/R to ltspiceautomator for the max power point.
                #     Disabled entirely (see import above) — was already dead since
                #     its own get_mpp() call below is commented out.
                # (b) OLD WAY: closed-form 3-mesh solve at a fixed 200 V PA. This runs.
                ###########OLD WAY
                iloop, itank, irc = circuit_solver.solve_mesh_currents(
                    Vs, omega, M12, M13, M23, Z1, Z2, Z3)

                # Power accounting. pa_efficiency is an assumption of this run, not of the circuit.
                pa_efficiency = .85
                (Vrect, PBatt, Ploss_loop, Ploss_tank, Ploss_rc, Total_Power,
                 Ipa) = circuit_solver.power_accounting(
                    iloop, itank, irc, Z1, Z2, Z3, Vpa,
                    RL, RL_ac, Z_ml, Z_mc, pa_efficiency)
                # DEAD: Admittance is never stored (its append is commented out).
                Admittance = Ipa / Vpa

                # Currents normalised to the RC current. mag_normal below is both a figure
                # of merit AND the inverse line width / font size of every plot.
                abs_Iloop, abs_Itank = circuit_solver.normalized_currents(
                    omega, M12, M13, M23, Z2, Z3)

                # ---- store this distance's results ----
                sweep_points.append(SweepPoint(
                    distance=rc_test_distances[i],
                    L_loop=L_loop, M13=M13, M12=M12,
                    L_tank=L_tank, M23=M23, M21=M21,
                    L_rc=L_rc, M31=M31, M32=M32,
                    k12=k12, k13=k13, k23=k23,
                    tank_f=tank_shifted_f,
                    iloop=iloop, itank=itank, irc=irc,
                    vpa=Vpa, ipa=Ipa, pbatt=PBatt, vrect=Vrect,
                    abs_Iloop=abs_Iloop, abs_Itank=abs_Itank,
                ))

            # Every table value is reported at the CLOSEST test distance, index 0.
            closest = sweep_points[0]
            last = sweep_points[-1]

            # ---- Text log (results.log echoes to stdout AND the run's log file) ----
            results.log_run_summary(coilset, run_count, num_configs, sweep_points, C1, C2, C3, Z1, Z2, Z3)

            # Trace radii / layer positions, for cross-checking against the real PCB layout.
            print("Tank Turn Radii:")
            print(tank_turn_radii)
            print("")

            print("Loop Turn Radii:")
            print(loop_turn_radii)
            print("")

            print("RC Turn Radii:")
            print(rc_turn_radii)
            print("")

            # Console dump in a copy-paste-friendly form.
            for sp in sweep_points:
                print(f"At {sp.distance}mm : M13: {sp.M13}, M23 : {sp.M23}, M12: {sp.M12}")

            print(f"M13s = {[sp.M13 for sp in sweep_points]}")
            print(f"M23s = {[sp.M23 for sp in sweep_points]}")
            print(f"M12 = {[sp.M12 for sp in sweep_points]}")
            print(f'distances = {rc_test_distances}')

            # ---- Field-based loss extraction -------------------------------------
            # Everything above ran on arbitrary probe currents. One more solve at the real
            # currents predicted for the closest distance (RMS -> peak), then integrate
            # losses out of that single field solution. Geometry is back at distance[0]
            # because get_inductances undoes its translations.
            # Zeros below are the defaults for configs with no ferrite / no shield.
            TC_ferrite_flux_density = 0
            RC_ferrite_flux_density = 0
            tc_ferrite_loss = 0
            rc_ferrite_loss = 0
            tc_shield_loss = 0
            rc_shield_loss = 0

            close_loop_current = closest.iloop*np.sqrt(2)
            close_tank_current = closest.itank*np.sqrt(2)
            close_rc_current = closest.irc*np.sqrt(2)
            nff.set_circuit_currents(close_loop_current, coilset.num_loop_turns, close_tank_current, coilset.num_tank_turns, close_rc_current, coilset.num_rc_turns)

            # mi_analyze throws if the mesher chokes.
            femm.mi_analyze()
            femm.mi_loadsolution()

            femm.mo_zoom(0, -100, 100, 100)

            # Peak |B| just inside the ferrite face - says whether it is nearing saturation.
            # get_block_losses integrates over the block containing the probe point.
            if (coilset.tank_ferrite_present):
                TC_ferrite_flux_density = nff.get_peak_flux_density_of_rect(coilset.tank_ferrite_outer_r, ferrite_top_edge-.01)
                tc_ferrite_loss = nff.get_block_losses(coilset.tank_ferrite_outer_r/2, tc_ferrite_midpoint_y)

            if (coilset.rc_ferrite_present):
                RC_ferrite_flux_density = nff.get_peak_flux_density_of_rect(coilset.rc_ferrite_outer_r, rc_ferrite_bottom+.01)
                rc_ferrite_loss = nff.get_block_losses(coilset.rc_ferrite_outer_r/2, rc_ferrite_midpoint_y)

            if (coilset.tc_shield):
                tc_shield_loss = nff.get_block_losses(coilset.tc_shield_radius/2, tc_shield_midpoint_y)

            if (coilset.rc_shield):
                rc_shield_loss = nff.get_block_losses(coilset.rc_shield_radius/2, rc_shield_midpoint_y)

            # These "*_losses" rows hold ESR in OHMS (get_coil_losses below gives WATTS);
            # the commented-out lines are the older watts version.
            # LABEL MISMATCH: table row labels say "mOhms" but the values are ohms.
            loop_esr = nff.loop_ESR('loop', coilset.num_loop_turns, close_loop_current)
            tank_esr = nff.loop_ESR('tank', coilset.num_tank_turns, close_tank_current)
            rc_esr = nff.loop_ESR('rc', coilset.num_rc_turns, close_rc_current)

            # Per-trace ohmic + proximity loss in watts, summed over every coil - feeds total_loss.
            rc_loss = nff.get_coil_losses(rc_turn_radii, rc_trace_ys, coilset.rc_trace_thickness)
            tank_loss = nff.get_coil_losses(tank_turn_radii, tank_trace_ys, coilset.tank_trace_thickness)
            print(f"tank loss: {tank_loss} and tank current is: {close_tank_current}")
            loop_loss = nff.get_coil_losses(loop_turn_radii, loop_trace_ys, coilset.loop_trace_thickness)

            # System efficiency: delivered DC power minus every loss FEMM can account for.
            total_loss = tc_ferrite_loss + rc_ferrite_loss + tc_shield_loss + rc_shield_loss + loop_loss + tank_loss + rc_loss
            coil_efficiency = np.abs((closest.pbatt - total_loss) / closest.pbatt)

            print(f"I_tank/I_rc: {abs_Itank}")
            print(f"I_loop/I_rc: {abs_Iloop}")

            # mag_normal: |(Iloop, Itank)| normalised to Irc. Figure of merit, and the
            # inverse plot line width - so "better" configs are drawn thicker.
            mag_normal = np.sqrt(closest.abs_Itank**2 + closest.abs_Iloop**2)

            results.log("Magnitude of normalized currents: " + str(mag_normal))

            # ---- This config's results. Raw values only; the results table formats them.
            cfg = ConfigResult(
                label=config_label,
                color=plot_color,

                k12=closest.k12, k13=closest.k13, k23=closest.k23,
                M12=closest.M12, M13=closest.M13, M23=closest.M23,

                L_loop_solo=L_loop_solo,
                L_tank_solo=L_tank_solo,
                L_rc_solo=L_rc_solo,
                L_loop_assembled=L_loop_fully_assembled,
                L_tank_assembled=L_tank_fully_assembled,
                L_rc_assembled=L_rc_fully_assembled,

                L_tank_at_closest=closest.L_tank,
                L_rc_at_closest=closest.L_rc,
                abs_Iloop=closest.abs_Iloop,
                abs_Itank=closest.abs_Itank,

                tc_ferrite_flux=TC_ferrite_flux_density,
                rc_ferrite_flux=RC_ferrite_flux_density,
                loop_esr=loop_esr, tank_esr=tank_esr, rc_esr=rc_esr,
                tc_ferrite_loss=tc_ferrite_loss, rc_ferrite_loss=rc_ferrite_loss,
                tc_shield_loss=tc_shield_loss, rc_shield_loss=rc_shield_loss,
                total_loss=total_loss,
                efficiency=coil_efficiency,
                mag_normal=mag_normal,
                sweep=sweep_points,
            )
            run.configs.append(cfg)

            # ---- Charts for this config, read from cfg.sweep.
            dashboard.plot_config(cfg, label=run_num, run_number=run_count)

            # ---- Tear down: wipe every drawn entity so the next config starts empty.
            # Rectangle starts at x=0.01 to miss the symmetry axis; trailing 4 = all types.
            femm.mi_selectrectangle(0.01, -190,220,190,4)
            femm.mi_deleteselected()
            femm.mi_clearselected()

            # Snapshot field values into the config table columns. table_fields() picks
            # exactly the labelled fields, in declaration order - no positional slice.
            for i, field in enumerate(table_fields()):
                parameter_outputs[i].append(getattr(coilset, field.name))

            # Circuits must be deleted too, or the next config's mi_addcircprop calls collide.
            for i in range(coilset.num_loop_turns):
                femm.mi_deletecircuit('loop'+str(i))

            for i in range(coilset.num_tank_turns):
                femm.mi_deletecircuit('tank'+str(i))

            for i in range(coilset.num_rc_turns):
                femm.mi_deletecircuit('rc'+str(i))

            # Crude score for hand-ranking configs: Vrect deviation from 80 V plus a
            # current-imbalance penalty.
            # DEAD: `quality` is never printed or stored - only its two halves are.
            vrect_delta = 0
            for sp in sweep_points:
                vrect_delta += np.abs(80 - sp.vrect)

            current_mag_quality = np.abs(mag_normal - 1) * 10
            quality = vrect_delta + current_mag_quality
            print(f"Current Quality: {current_mag_quality}")
            print(f"Vrect Quality: {vrect_delta}")

        # ===========================================================================
        # OUTPUT - runs ONCE, after every config has been simulated.
        # ===========================================================================
        dashboard.render_results_table(run, rc_test_distances[0])
        dashboard.render_config_table(run, parameter_outputs, table_labels(),
                                      no_tank_ferrite_present, no_rc_ferrite_present,
                                      no_backplane_present)
        dashboard.finish()
        results.save_figure(dashboard.figure)
        results.save_summary_json(run)
    finally:
        # Guarantees FEMM/Wine is released even on a mid-run failure - only
        # one FEMM process may be alive at a time, which matters once this
        # runs inside a long-lived web server rather than a one-shot script.
        femm.closefemm()

    return run, results
