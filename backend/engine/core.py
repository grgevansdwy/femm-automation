# =============================================================================
# engine/core.py                                                       Noel Hosking
#
# Callable entry point: one CoilSet in, one SimResult out. Drives FEMM (2-D
# axisymmetric, 6.78 MHz, mm) to solve self-inductance (L11/L22/L33),
# mutual inductance (M12/M13/M23), and ESR for the loop/tank/rc coils at one
# RC separation distance.
#
# Geometry in mm: x = radius, y = axis of revolution, nothing crosses x=0.
# Currents are PEAK amps unless the name ends in _RMS.
# "TC" = transmit side (Loop + Tank), "RC" = receive side.
# =============================================================================

# ============================ IMPORTS =============================
import numpy as np

from engine.services import femm_service as nff
from engine.services import xfemm_backend
from engine.models.result import SimResult

# ============================ CONSTANTS =============================
from engine.constants import (
    loop_group, tank_group, rc_group,
    loop_current_target, tank_coil_current_target, rc_test_current_target,
    backplane_length, backplane_thickness,
)

DEFAULT_RC_TEST_DISTANCE = 5.0  # mm


def turn_currents(coilset):
    """(loop, tank, rc) per-turn drive currents for `coilset`: each coil's
    target divided across its parallel traces and layers, so ampere-turns
    match a real coil wound from these traces. Pure function of the coilset -
    pulled out of build_script() so build_geometry() can stay geometry-only
    and callers that only draw the model (engine/services/femm_launcher.py) don't
    have to know how a drive current is derived."""
    return (
        nff.get_turn_current(loop_current_target, coilset.num_loop_layers, coilset.num_parallel_loop_traces, coilset.num_loop_turns),
        nff.get_turn_current(tank_coil_current_target, coilset.num_tank_layers, coilset.num_parallel_tank_traces, coilset.num_tank_turns),
        nff.get_turn_current(rc_test_current_target, coilset.num_rc_layers, coilset.num_parallel_rc_traces, coilset.num_rc_turns),
    )


def build_geometry(coilset, distance=DEFAULT_RC_TEST_DISTANCE, script=None):
    """Builds the model itself - problem definition, materials, circuits, the
    three coils' traces, and the ferrite/shield/backplane stack-up - for
    `coilset` at RC separation `distance` (mm), and stops there: no currents
    set, nothing analyzed. Returns the script it built into (a fresh
    xfemm_backend.LuaScript unless one is passed in).

    Split out of build_script() so "draw this coilset" has exactly one
    implementation: build_script() appends the solves to it for a real
    simulation, and engine/services/femm_launcher.py appends a save + zoom instead to
    open the identical model in the FEMM GUI. A separate drawing routine for
    the GUI button would be free to drift from what actually gets solved -
    this cannot.
    """
    # ========================== Build the model ===============================
    # No live FEMM/Wine session: `script` accumulates one Lua program, run once
    # via femmcli at the very end (see engine/services/xfemm_backend.py for why - it
    # only runs whole scripts, not live call/response).
    script = xfemm_backend.LuaScript() if script is None else script
    nff.init_femm(script)

    # ================== FEMM CIRCUIT SETUP =====================
    for i in range(coilset.num_loop_turns):
        script.mi_addcircprop('loop'+str(i), 0, 0)

    for i in range(coilset.num_tank_turns):
        script.mi_addcircprop('tank'+str(i), 0, 0)

    for i in range(coilset.num_rc_turns):
        script.mi_addcircprop('rc'+str(i), 0, 0)

    # ==================== Ferrite Materials =======================
    # FEMM property slots: 1/2 = mu_x/mu_y, 5 = conductivity, 10/11 = loss tangents.
    # TC and RC ferrite are separate materials, each with its own mu/loss angle.
    script.mi_modifymaterial('Ferrite', 1, coilset.tank_mu)
    script.mi_modifymaterial('Ferrite', 2, coilset.tank_mu)
    script.mi_modifymaterial('Ferrite', 5, 1e-11)  # conductivity
    script.mi_modifymaterial('Ferrite', 10, coilset.tc_loss_angle)
    script.mi_modifymaterial('Ferrite', 11, coilset.tc_loss_angle)

    script.mi_addmaterial('FerriteRC')
    script.mi_modifymaterial('FerriteRC', 1, coilset.rc_mu)
    script.mi_modifymaterial('FerriteRC', 2, coilset.rc_mu)
    script.mi_modifymaterial('FerriteRC', 5, 1e-11)  # conductivity
    script.mi_modifymaterial('FerriteRC', 10, coilset.rc_loss_angle)
    script.mi_modifymaterial('FerriteRC', 11, coilset.rc_loss_angle)
    script.mi_zoom(0, -100, 100, 100)

    # ======================= DRAW GEOMETRY =======================
    # Layout: RC face-down at the target distance, tank at y=0, loop below it.

    # DRAWING RC
    nff.draw_coil_traces(script, coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces, coilset.rc_trace_width, coilset.rc_trace_spacing, coilset.rc_turn_spacing, distance, coilset.rc_trace_thickness, 'trace', 'rc', coilset.num_rc_layers, -coilset.rc_PCB_thickness, rc_group)

    # DRAWING TANK
    nff.draw_coil_traces(script, coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces, coilset.tank_trace_width, coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_thickness, coilset.tank_trace_thickness, 'trace', 'tank', coilset.num_tank_layers, coilset.tank_PCB_thickness, tank_group)

    # DRAWING LOOP
    nff.draw_coil_traces(script, coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces, coilset.loop_trace_width, coilset.loop_trace_spacing, coilset.loop_turn_spacing, -coilset.tank_PCB_thickness-coilset.loop_offset, coilset.loop_trace_thickness, 'trace', 'loop', coilset.num_loop_layers, coilset.loop_PCB_thickness, loop_group)

    # =============== FERRITE / SHIELD / BACKPLANE GEOMETRY ==================
    # Stack-up: tc_bottom_y walks DOWN from the loop PCB underside (PCB -> gap ->
    # ferrite -> shield -> backplane); rc_top_y mirrors it above the RC.
    tc_bottom_y = -coilset.tank_PCB_thickness-coilset.loop_offset-coilset.loop_PCB_thickness-coilset.tank_trace_thickness-(2*coilset.loop_trace_thickness)

    # TC Ferrite
    if coilset.tank_ferrite_present:
        tc_bottom_y -= coilset.tank_ferrite_y_offset
        nff.draw_rectangular_material_no_plot(script, 'Ferrite', coilset.tank_ferrite_thickness, coilset.tank_ferrite_outer_r, tc_bottom_y, loop_group)
        tc_bottom_y -= coilset.tank_ferrite_thickness

    # TC-side Metal shield
    if coilset.tc_shield:
        tc_bottom_y -= coilset.tc_shield_y_distance
        nff.draw_rectangular_material_no_plot(script, 'AluFoil', coilset.tc_shield_thickness, coilset.tc_shield_radius, tc_bottom_y, loop_group)
        tc_bottom_y -= coilset.tc_shield_thickness

    # RC-side stack-up, mirrored above the receive coil.
    rc_top_y = coilset.rc_PCB_thickness + distance + (coilset.rc_trace_thickness*2) + coilset.rc_ferrite_y_offset

    # RC Ferrite
    if coilset.rc_ferrite_present:
        rc_top_y += coilset.rc_ferrite_thickness
        nff.draw_rectangular_material_no_plot(script, 'FerriteRC', coilset.rc_ferrite_thickness, coilset.rc_ferrite_outer_r, rc_top_y, rc_group)

    # RC-side Metal shield
    if coilset.rc_shield:
        rc_top_y += coilset.rc_shield_y_distance
        rc_top_y += coilset.rc_shield_thickness
        nff.draw_rectangular_material_no_plot(script, 'AluFoil', coilset.rc_shield_thickness, coilset.rc_shield_radius, rc_top_y, rc_group)

    # TC-side Metal detuning plane
    if coilset.backplane:
        tc_bottom_y -= coilset.backplane_y_distance - 3  # enclosure_thickness
        nff.draw_rectangular_material_no_plot(script, 'Copper', backplane_thickness, backplane_length/2, tc_bottom_y, loop_group)
        tc_bottom_y -= backplane_thickness

    if coilset.rc_backplane:
        rc_top_y += (coilset.rc_backplane_y_distance + backplane_thickness + 3)  # 3=enclosure thickness
        nff.draw_rectangular_material_no_plot(script, 'Copper', backplane_thickness, backplane_length/2, rc_top_y, rc_group)
        rc_top_y += backplane_thickness

    return script


def build_script(coilset, distance=DEFAULT_RC_TEST_DISTANCE):
    """The full Lua script for one simulation: build_geometry()'s model plus
    all 3 driven-coil solves, as a xfemm_backend.LuaScript - split out of
    simulate() so a test harness can run the exact same script through a
    different backend (e.g. real FEMM under Wine) for validation. Does not run
    anything by itself.
    """
    script = build_geometry(coilset, distance)
    loop_turn_current, tank_turn_current, rc_turn_current = turn_currents(coilset)

    # ========================== SIMULATION =====================
    # Everything is already at its real position (RC drawn directly at
    # `distance`), so all three solves use zero shift on every group (the
    # original mi_movetranslate(0, 0)-per-group dance is skipped entirely -
    # it was a no-op). THREE solves, one per driven coil - solve_and_extract
    # only recovers the row for the coil carrying current:
    #   loop -> L11, M12, M13, R11 | tank -> L22, M23, M21, R22 | RC -> L33, M31, M32, R33
    # M12 vs M21 should agree by reciprocity, so the gap is a sanity check.
    script.set_circuit_currents(loop_turn_current, coilset.num_loop_turns, 0, coilset.num_tank_turns, 0, coilset.num_rc_turns)
    script.solve_and_extract(loop_turn_current, coilset.num_loop_turns, 0, coilset.num_tank_turns, 0, coilset.num_rc_turns)

    script.set_circuit_currents(0, coilset.num_loop_turns, tank_turn_current, coilset.num_tank_turns, 0, coilset.num_rc_turns)
    script.solve_and_extract(0, coilset.num_loop_turns, tank_turn_current, coilset.num_tank_turns, 0, coilset.num_rc_turns)

    script.set_circuit_currents(0, coilset.num_loop_turns, 0, coilset.num_tank_turns, rc_turn_current, coilset.num_rc_turns)
    script.solve_and_extract(0, coilset.num_loop_turns, 0, coilset.num_tank_turns, rc_turn_current, coilset.num_rc_turns)

    return script


def extract_result(results, coilset, distance=DEFAULT_RC_TEST_DISTANCE):
    """Turns a raw {key: complex/float} dict (as returned by
    xfemm_backend.run_femmcli() or wine_femm_backend.run_wine_femm() - both
    parse the same result.txt format written by build_script()'s rendered
    script) into a SimResult.
    """
    def real(key):
        return results.get(key, 0.0).real

    L_loop, M12, M13, loop_esr = real('L_loop'), real('M_tank_loop'), real('M_rc_loop'), real('R_loop')
    L_tank, M23, tank_esr = real('L_tank'), real('M_rc_tank'), real('R_tank')
    L_rc, rc_esr = real('L_rc'), real('R_rc')

    # Coupling coefficients. k12/k23 stay 0 for tank-less configs so sqrt(L_tank) never divides by zero.
    # float(): np.sqrt returns numpy.float64, and anything compared against it
    # becomes numpy.bool_ - which json.dumps() rejects. That crashed an AI
    # search with a k objective ("Object of type bool is not JSON
    # serializable") on the first freshly-solved candidate.
    k13 = float(M13 / (np.sqrt(L_loop*L_rc)))
    k12 = 0.0
    k23 = 0.0
    if coilset.num_tank_turns > 0:
        k12 = float(M12 / (np.sqrt(L_loop*L_tank)))
        k23 = float(M23 / (np.sqrt(L_tank*L_rc)))

    return SimResult(
        config_name=coilset.config_name, distance=distance,
        L_loop=L_loop, L_tank=L_tank, L_rc=L_rc,
        M12=M12, M13=M13, M23=M23,
        loop_esr=loop_esr, tank_esr=tank_esr, rc_esr=rc_esr,
        k12=k12, k13=k13, k23=k23,
    )


def validate_coilset(coilset):
    """Whether `coilset`'s three coils can physically exist - instant, no FEMM.
    Returns [(coil, reason)] for each of "loop" / "tank" / "rc" that fails
    (femm_service.check_valid_coil's own reason text), empty when valid.
    simulate() runs this first; the agent runs the same check before queueing
    a solve, so an invalid candidate never reaches the solver."""
    coils = (
        ("loop", coilset.loop_r, coilset.loop_trace_width, coilset.num_loop_turns,
         coilset.loop_turn_spacing, coilset.loop_trace_spacing, coilset.num_parallel_loop_traces),
        ("tank", coilset.tank_center_r, coilset.tank_trace_width, coilset.num_tank_turns,
         coilset.tank_turn_spacing, coilset.tank_trace_spacing, coilset.num_parallel_tank_traces),
        ("rc", coilset.rc_radius, coilset.rc_trace_width, coilset.num_rc_turns,
         coilset.rc_turn_spacing, coilset.rc_trace_spacing, coilset.num_parallel_rc_traces),
    )
    problems = []
    for coil, *args in coils:
        reason, valid = nff.check_valid_coil(*args)
        if not valid:
            problems.append((coil, reason))
    return problems


def simulate(coilset, distance=DEFAULT_RC_TEST_DISTANCE):
    """Draws `coilset` in FEMM at RC separation `distance` (mm) - ferrite,
    shields and backplane all present, RC drawn directly at that distance -
    and returns a SimResult. Solves via a generated Lua script run through
    xfemm's femmcli in its own temp directory (engine/services/xfemm_backend.py) -
    safe to call concurrently, unlike the previous single-Wine-process backend.
    """
    problems = validate_coilset(coilset)
    if problems:
        raise Exception("".join(reason for _, reason in problems))

    script = build_script(coilset, distance)
    results = xfemm_backend.run_femmcli(script.render())
    return extract_result(results, coilset, distance)
