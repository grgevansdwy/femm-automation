"""Tests compute_layout() only - pure geometry, no imgui import, no repo
writes, so no self-cleanup is needed for any of these."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from gui.panels.coil_diagram import compute_layout
from gui.panels.configs_panel import ConfigsPanelState


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


def test_backplane_fields_are_reported_separately_not_as_rings():
    on = _blank_buffer_with(backplane=1, rc_backplane=1)
    off = _blank_buffer_with(backplane=0, rc_backplane=0)

    assert compute_layout(on, 400.0, 400.0)["backplanes"] == ["Backplane", "RC Backplane"]
    assert compute_layout(off, 400.0, 400.0)["backplanes"] == []
    assert all(el["kind"] not in ("backplane", "rc_backplane") for el in compute_layout(on, 400.0, 400.0)["elements"])
