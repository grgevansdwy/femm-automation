# =============================================================================
# coilset.py                                      extracted from femm_model.py
#
# The CoilSet dataclass: the full geometric, material and tuning specification
# for one loop/tank/rx coil configuration.
#
# It lived in femm_model.py, which imports `femm` at module level - so reading
# the coil library used to drag in the whole FEMM package. femm_model only ever
# DEFINED CoilSet, never used it, so this is a clean lift.
#
# Plain data: no FEMM, no matplotlib.
# =============================================================================
from dataclasses import dataclass, field, fields


def _label(text):
    """Mark a field as a config-table row and give it its display label.

    These labels used to live in a separate `parameters` dict whose 55 keys had
    to stay in the same ORDER as these fields - nothing enforced it, and the two
    trailing fields had already drifted out of it. Attaching the label to the
    field makes that drift impossible.
    """
    return field(metadata={'label': text})


def table_fields():
    """The CoilSet fields shown in the config table, in declaration order.

    Replaces the old `parameter_count` slice of fields(coilset): membership is
    now 'has a label', not 'index < 55'.
    """
    return [f for f in fields(CoilSet) if 'label' in f.metadata]


def table_labels():
    """Row labels for the config table - the old list(parameters.keys())."""
    return [f.metadata['label'] for f in table_fields()]


# Full geometric, material, and tuning specification for one loop/tank/rx coil configuration.
@dataclass
class CoilSet():
    config_name: str = _label('config_name')

    tank_ferrite_present: int = _label('Tank Ferrite Present')
    tank_ferrite_y_offset: float = _label('Tank Ferrite Y offset (mm)')
    tank_ferrite_thickness: float = _label('Tank Ferrite Thickness (mm)')
    tank_mu: float = _label('Tank Ferrite Permeabilitiy')
    tank_ferrite_outer_r: float = _label('Tank Ferrite Radius')

    rc_ferrite_present: int = _label('RC Ferrite Present')
    rc_ferrite_y_offset: float = _label('RC Ferrite +Y offset (mm)')
    rc_ferrite_thickness: float = _label('RC Ferrite Thickness (mm)')
    rc_mu: float = _label('RC Ferrite Permeabilitiy')
    rc_ferrite_outer_r: float = _label('RC Ferrite Radius')

    #Backplane
    backplane: int = _label('Metal backplane present')
    backplane_y_distance: float = _label('Metal Backplane Z offset (mm)')

    #Loop
    loop_offset: float = _label('Loop Z offset (mm)')
    loop_r: float = _label('Loop Center radius')
    num_loop_turns: int = _label('Number of Loop Turns')
    num_parallel_loop_traces: int = _label('Number of Parallel Loop traces')
    num_loop_layers: int = _label('Number of Loop Layers')
    loop_trace_spacing: float = _label('Loop Trace Center-Center Spacing (mm)')
    loop_turn_spacing: float = _label('Loop Turn Center-Center Spacing (mm)')


    #Tank
    tank_center_r: float = _label('Tank Center radius (mm)')
    num_tank_turns: int = _label('Number of Tank Turns')
    num_parallel_tank_traces: int = _label('Number of Parallel Tank Traces')
    num_tank_layers: int = _label('Number of Tank Layers')
    tank_trace_spacing: float = _label('Tank Trace Center-Center Spacing (mm)')
    tank_turn_spacing: float = _label('Tank Turn Center-Center Spacing (mm)')


    #RC
    rc_radius: float = _label('RC central turn radius (mm)')
    num_rc_turns: int = _label('Number of RC Turns')
    num_parallel_rc_traces: int = _label('Number of Parallel RC Traces')
    num_rc_layers: int = _label('Number of RC Layers')
    rc_trace_spacing: float = _label('RC trace Center-Center spacing (mm)')
    rc_turn_spacing: float = _label('RC turn Center-Center spacing (mm)')
    coax_cable: float = _label('Coax Cable Config')

    loop_tune_f: float = _label('Loop Tuning Frequency (MHz)')
    tank_tune_f: float = _label('Tank Tuning Frequency (MHz)')
    rc_tune_f: float = _label('RC Tuning Frequency (MHz)')

    tank_trace_width: float = _label('Tank Trace Width (mm)')
    loop_trace_width: float = _label('Loop Trace Width (mm)')
    rc_trace_width: float = _label('RC Trace Width (mm)')

    tank_PCB_thickness: float = _label('Tank PCB thickness')
    loop_PCB_thickness: float = _label('Loop PCB thickness')
    rc_PCB_thickness: float = _label('RC PCB thickness')
    rc_backplane: int = _label('RC Metal Backplane')
    rc_backplane_y_distance: float = _label('RC Metal Backplane Z offset (mm)')

    tc_shield: int = _label('TC metal shield')
    tc_shield_y_distance: float = _label('TC metal shield Z offset (mm)')
    tc_shield_radius: float = _label('TC metal shield radius (mm)')
    tc_shield_thickness: float = _label('TC metal shield thickness (mm)')

    rc_shield: int = _label('RC metal shield')
    rc_shield_y_distance: float = _label('RC metal shield Z offset (mm)')
    rc_shield_radius: float = _label('RC metal shield radius (mm)')
    rc_shield_thickness: float = _label('RC metal shield thickness (mm)')

    loop_trace_thickness: float = _label('Loop Trace Thickness (mm)')
    tank_trace_thickness: float = _label('Tank Trace Thickness (mm)')
    rc_trace_thickness: float = _label('RC Trace Thickness (mm)')


    rc_loss_angle: float=0.0125 #FJ7 is the default
    tc_loss_angle: float=0.0125 #FJ7 is the default
