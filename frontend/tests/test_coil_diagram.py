"""Tests compute_layout() only - pure geometry, no imgui import, no repo
writes, so no self-cleanup is needed for any of these."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.visualization.coil_diagram import compute_axial_layout, compute_layout
from frontend.screen_manual.configs_panel import ConfigsPanelState


def _blank_buffer_with(**overrides):
    state = ConfigsPanelState()
    state.new()
    state.buffer.update(overrides)
    return state.buffer


def test_the_largest_core_radius_drives_the_scale():
    buffer = _blank_buffer_with(loop_r=10.0, tank_center_r=50.0, rc_radius=20.0)

    layout = compute_layout(buffer, canvas_w=400.0, canvas_h=400.0, margin=20.0)

    expected_scale = (200.0 - 20.0) / 50.0  # min(canvas)/2 - margin, over tank_center_r (the largest)
    assert layout["scale"] == expected_scale


def test_ferrite_and_shield_radii_excluded_when_their_flag_is_off():
    buffer = _blank_buffer_with(
        loop_r=10.0, tank_center_r=10.0, rc_radius=10.0,
        tank_ferrite_present=0, tank_ferrite_outer_r=999.0,
        tc_shield=0, tc_shield_radius=999.0,
    )

    layout = compute_layout(buffer, canvas_w=400.0, canvas_h=400.0, margin=20.0)

    # If the huge ferrite/shield radii had been included, scale would be tiny.
    expected_scale = (200.0 - 20.0) / 10.0
    assert layout["scale"] == expected_scale
    kinds = {el["kind"] for el in layout["elements"]}
    assert "tank_ferrite" not in kinds
    assert "tc_shield" not in kinds


def test_ferrite_and_shield_radii_included_when_their_flag_is_on():
    buffer = _blank_buffer_with(
        loop_r=10.0, tank_center_r=10.0, rc_radius=10.0,
        tank_ferrite_present=1, tank_ferrite_outer_r=80.0,
        tc_shield=1, tc_shield_radius=60.0,
    )

    layout = compute_layout(buffer, canvas_w=400.0, canvas_h=400.0, margin=20.0)

    expected_scale = (200.0 - 20.0) / 80.0  # the ferrite radius is now the largest candidate
    assert layout["scale"] == expected_scale
    kinds = {el["kind"] for el in layout["elements"]}
    assert "tank_ferrite" in kinds
    assert "tc_shield" in kinds


def test_a_blank_new_config_buffer_does_not_raise():
    buffer = _blank_buffer_with()  # ConfigsPanelState.new() -> all radii 0

    layout = compute_layout(buffer, canvas_w=400.0, canvas_h=400.0, margin=20.0)

    assert layout["scale"] > 0  # the `or 1.0` fallback kicked in, no ZeroDivisionError


def test_a_real_loaded_config_fits_within_the_canvas():
    state = ConfigsPanelState()
    state.load("base_coil")

    layout = compute_layout(state.buffer, canvas_w=400.0, canvas_h=400.0, margin=20.0)

    max_radius_px = 400.0 / 2
    for el in layout["elements"]:
        assert 0 <= el["radius_px"] <= max_radius_px


def _elements_by_kind(elements):
    by_kind = {}
    for el in elements:
        by_kind.setdefault(el["kind"], []).append(el)
    return by_kind


def test_loop_sits_below_tank_which_sits_below_rc_when_distance_is_positive():
    """The core physical fact the axial view exists to make visible: these
    three coils are NOT concentric - Loop is below Tank (the TC side), and
    RC is a separate receiver `distance` mm above them, facing down."""
    buffer = _blank_buffer_with(loop_r=10.0, tank_center_r=10.0, rc_radius=10.0,
                                 loop_offset=10.0, tank_PCB_thickness=1.6)

    layout = compute_axial_layout(buffer, distance=25.0, canvas_w=400.0, canvas_h=400.0)
    by_kind = _elements_by_kind(layout["elements"])

    loop_y = by_kind["loop"][0]["top_mm"]
    tank_y = by_kind["tank"][0]["top_mm"]
    rc_y = by_kind["rc"][0]["top_mm"]
    assert loop_y < tank_y < rc_y


def test_rc_sits_exactly_at_the_run_distance():
    buffer = _blank_buffer_with(loop_r=10.0, tank_center_r=10.0, rc_radius=10.0)

    for distance in (5.0, 50.0):
        layout = compute_axial_layout(buffer, distance=distance, canvas_w=400.0, canvas_h=400.0)
        rc_el = _elements_by_kind(layout["elements"])["rc"][0]
        assert rc_el["top_mm"] == distance


def test_axial_ferrite_shield_backplane_excluded_when_their_flag_is_off():
    buffer = _blank_buffer_with(
        loop_r=10.0, tank_center_r=10.0, rc_radius=10.0,
        tank_ferrite_present=0, rc_ferrite_present=0,
        tc_shield=0, rc_shield=0, backplane=0, rc_backplane=0,
    )

    layout = compute_axial_layout(buffer, distance=5.0, canvas_w=400.0, canvas_h=400.0)

    kinds = {el["kind"] for el in layout["elements"]}
    assert kinds == {"loop", "tank", "rc"}


def test_axial_ferrite_shield_backplane_included_and_ordered_when_on():
    """Bottom-to-top physical order on the TC side: backplane (furthest out)
    -> shield -> ferrite (closest to the coils) -> Loop -> Tank - mirroring
    engine/core.py's own processing order."""
    buffer = _blank_buffer_with(
        loop_r=10.0, tank_center_r=10.0, rc_radius=10.0,
        tank_ferrite_present=1, tank_ferrite_outer_r=80.0, tank_ferrite_thickness=2.0, tank_ferrite_y_offset=1.0,
        tc_shield=1, tc_shield_radius=90.0, tc_shield_thickness=1.0, tc_shield_y_distance=2.0,
        backplane=1, backplane_y_distance=100.0,
    )

    layout = compute_axial_layout(buffer, distance=5.0, canvas_w=400.0, canvas_h=400.0)
    by_kind = _elements_by_kind(layout["elements"])

    assert set(by_kind) == {"loop", "tank", "rc", "tank_ferrite", "tc_shield", "backplane"}
    backplane_y = by_kind["backplane"][0]["top_mm"]
    shield_y = by_kind["tc_shield"][0]["top_mm"]
    ferrite_y = by_kind["tank_ferrite"][0]["top_mm"]
    loop_y = by_kind["loop"][0]["top_mm"]
    assert backplane_y < shield_y < ferrite_y < loop_y


def test_axial_layout_of_a_blank_buffer_does_not_raise():
    buffer = _blank_buffer_with()  # ConfigsPanelState.new() -> all radii/offsets 0

    layout = compute_axial_layout(buffer, distance=0.0, canvas_w=400.0, canvas_h=400.0)

    assert layout["scale"] > 0


def test_a_real_loaded_config_stays_within_the_axial_canvas_bounds():
    state = ConfigsPanelState()
    state.load("base_coil")

    for distance in (5.0, 50.0):
        layout = compute_axial_layout(state.buffer, distance=distance, canvas_w=400.0, canvas_h=400.0, margin=20.0)
        for el in layout["elements"]:
            assert 0 <= el["x_half_width_px"] <= 200.0
            assert 20.0 - 1e-6 <= el["y_top_px"] <= el["y_bottom_px"] <= 400.0 - 20.0 + 1e-6
