# =============================================================================
# test_notebook.py
#
# Checks agent/femm_agent/services/notebook.py - the lever notebook the search
# model is shown each round. Pure functions of the tried list: no LLM, no FEMM.
#     python3 -m pytest agent/femm_agent/tests/test_notebook.py  (run from backend/)
# =============================================================================
import dataclasses
from types import SimpleNamespace

from agent.femm_agent.services.notebook import NOTEBOOK_MAX_LINES, fields_without_a_line, notebook_block
from engine.repositories.coilset_repository import get_coilset


def _design(label, baseline, changes, esr_pct, base=None, value=None):
    """One simulated design as the search remembers it: `changes` are its values
    that differ from the baseline, `esr_pct` its tank_esr % change vs. baseline."""
    objective = {"metric": "tank_esr", "worst_change_pct": esr_pct, "target_value": None,
                 "value": 0.4, "baseline_value": 0.427}
    if value is not None:  # an absolute target instead
        objective = {"metric": "tank_outer_diameter", "worst_change_pct": 0.0, "target_value": 150.0,
                     "value": value, "baseline_value": 160.0}
    return SimpleNamespace(label=label, base_label=base, result={"objectives": [objective]},
                           scored=SimpleNamespace(coilset=dataclasses.replace(baseline, **changes)))


def _lines(tried, baseline, fixed=None):
    return [line.strip() for line in notebook_block(tried, baseline, fixed or {})[1:]]


def test_a_one_value_step_from_its_starting_design_is_a_single_line():
    b = get_coilset("base_coil")
    tried = [
        _design("C1", b, {"tank_center_r": 70.0}, -4.0),
        _design("C2", b, {"tank_center_r": 70.0, "loop_r": b.loop_r + 1}, -6.5, base="C1"),
    ]
    assert _lines(tried, b) == [
        f"single   baseline -> C1: tank_center_r {b.tank_center_r:g} -> 70 | tank_esr -4.0 pts",
        f"single   C1 -> C2: loop_r {b.loop_r:g} -> {b.loop_r + 1:g} | tank_esr -2.5 pts",
    ]


def test_a_single_line_is_found_against_a_design_that_was_not_its_base():
    b = get_coilset("base_coil")
    four = {"tank_center_r": 70.0, "loop_r": b.loop_r + 1, "rc_radius": b.rc_radius + 1, "num_tank_turns": b.num_tank_turns + 1}
    tried = [
        _design("C1", b, four, -10.0),                              # 4 values from the baseline: no line
        _design("C2", b, {**four, "tank_center_r": 69.0}, -12.0),   # also 4 from the baseline, but 1 from C1
    ]
    assert _lines(tried, b) == [f"single   C1 -> C2: tank_center_r 70 -> 69 | tank_esr -2.0 pts"]


def test_two_or_three_values_are_a_combined_line_and_four_get_none():
    b = get_coilset("base_coil")
    tried = [
        _design("C1", b, {"tank_center_r": 70.0, "loop_r": b.loop_r + 1}, -3.0),
        _design("C2", b, {"tank_center_r": 60.0, "loop_r": b.loop_r + 2, "rc_radius": b.rc_radius + 2}, -5.0),
        _design("C3", b, {"tank_center_r": 50.0, "loop_r": b.loop_r + 3, "rc_radius": b.rc_radius + 3,
                          "num_tank_turns": b.num_tank_turns + 1}, -9.0),
    ]
    lines = _lines(tried, b)
    assert [line.split(":")[0] for line in lines] == ["combined baseline -> C1", "combined baseline -> C2"]
    assert "loop_r" in lines[0] and "rc_radius" in lines[1] and lines[1].endswith("| tank_esr -5.0 pts")


def test_a_pinned_start_is_never_simulated_so_it_gets_no_line_against_it():
    b = get_coilset("base_coil")
    pins = {"loop_r": b.loop_r + 1}
    tried = [
        _design("C1", b, {**pins, "tank_center_r": 70.0}, -4.0),
        _design("C2", b, {**pins, "tank_center_r": 70.0, "rc_radius": b.rc_radius + 1}, -5.0, base="C1"),
    ]
    assert [line.split(":")[0] for line in _lines(tried, b, pins)] == ["single   C1 -> C2"]


def test_identical_steps_merge_and_rejected_proposals_are_ignored():
    b = get_coilset("base_coil")
    rejected = SimpleNamespace(label=None, base_label=None, result={"error": "bad"}, scored=None)
    tried = [_design("C1", b, {"tank_center_r": 70.0}, -4.0), rejected, _design("C2", b, {"tank_center_r": 70.0}, -4.0)]
    assert _lines(tried, b) == [f"single   baseline -> C1=C2: tank_center_r {b.tank_center_r:g} -> 70 | tank_esr -4.0 pts"]


def test_an_absolute_target_shows_its_value_before_and_after():
    b = get_coilset("base_coil")
    tried = [_design("C1", b, {"tank_center_r": 70.0}, 0.0, value=152.0)]
    assert _lines(tried, b)[0].endswith("| tank_outer_diameter 160 -> 152")


def test_only_the_newest_lines_are_kept_however_long_the_search_runs():
    b = get_coilset("base_coil")
    tried = [_design(f"C{i}", b, {"tank_center_r": 20.0 + i * 0.1}, -float(i)) for i in range(1, 126)]  # 25 rounds x 5
    block = notebook_block(tried, b, {})
    assert len(block) == NOTEBOOK_MAX_LINES + 1
    assert f"Newest {NOTEBOOK_MAX_LINES} of 125 lines." in block[0]
    assert "-> C125:" in block[-1] and "-> C86:" in block[1]
    assert notebook_block([], b, {}) == []


def test_fields_without_a_line_are_the_free_fields_no_step_has_changed():
    b = get_coilset("base_coil")
    four = {"tank_center_r": 70.0, "loop_r": b.loop_r + 1, "rc_radius": b.rc_radius + 1, "num_tank_turns": b.num_tank_turns + 1}
    tried = [_design("C1", b, {"tank_center_r": 70.0}, -4.0), _design("C2", b, four, -9.0)]
    free = ["tank_center_r", "loop_r", "rc_radius"]
    assert fields_without_a_line(tried, b, {}, free) == ["loop_r", "rc_radius"], \
        "a field changed only inside a 4-value step has no measured effect yet"
