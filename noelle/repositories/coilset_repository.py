# =============================================================================
# coilset_repository.py
#
# Named CoilSet templates: JSON-backed. Each design used to be a ~40-line
# Python literal here (Gen3, base_coil, ...), plus a handful of variants
# built by copy+override in Python. Both now live as fully-resolved JSON
# files under data/templates/ - one per template, mirroring the pattern
# webapp/data/configs/ already uses for user-saved configs. This module is
# the one place that touches that directory.
# =============================================================================
import dataclasses
import json
from pathlib import Path

from models.coilset import CoilSet

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / 'data' / 'templates'


def list_templates():
    """Every named template as {name: CoilSet}, keyed by its filename stem."""
    return {
        path.stem: CoilSet(**json.loads(path.read_text()))
        for path in sorted(TEMPLATES_DIR.glob('*.json'))
    }


def get_template(name):
    path = TEMPLATES_DIR / f'{name}.json'
    if not path.exists():
        raise KeyError(f"No such template: {name!r}")
    return CoilSet(**json.loads(path.read_text()))


def copy_coilset(coilset_to_copy):
    return dataclasses.replace(coilset_to_copy)


def create_iter_base_coil(variables):
    #variables = [loop_turns, tank_turns, rc_turns, loop_r, tank_r, rc_r, loop_turn_spacing, tank_turn_spacing, rc_turn_spacing, loop_trace_width, tank_trace_width, rc_trace_width]
    new_coil = copy_coilset(get_template('base_coil'))
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
