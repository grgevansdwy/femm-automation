"""Live coil-geometry schematic: concentric rings sized by each coil's
actual radius, redrawn every frame straight from the config form's current
buffer - no caching needed since it's cheap pure geometry, so a value
edited in the form is reflected here on the very next frame.

v1 scope (per plan): a proportional schematic - rings sized by radius, with
ferrite/shield/backplane shown when their presence flag is set - not a
literal turn-by-turn winding render. See docs/gui.md for the reasoning.
"""

from imgui_bundle import imgui

_LOOP_COLOR = imgui.ImVec4(0.35, 0.65, 0.95, 1.0)
_TANK_COLOR = imgui.ImVec4(0.95, 0.65, 0.25, 1.0)
_RC_COLOR = imgui.ImVec4(0.75, 0.45, 0.95, 1.0)
_FERRITE_COLOR = imgui.ImVec4(0.55, 0.75, 0.55, 0.35)
_SHIELD_COLOR = imgui.ImVec4(0.6, 0.6, 0.65, 1.0)
_CORE_COLOR = imgui.ImVec4(0.85, 0.85, 0.85, 1.0)


def compute_layout(buffer: dict, canvas_w: float, canvas_h: float, margin: float = 20.0) -> dict:
    """Pure geometry - no imgui calls, so this is unit-testable without a
    live frame. Radii are read with buffer[name], not .get: a renamed
    CoilSet field should fail loudly here, not silently draw a wrong ring
    (same "fail loudly on drift" convention field_layout.py already uses).
    """
    candidates = [buffer["loop_r"], buffer["tank_center_r"], buffer["rc_radius"]]
    if buffer["tank_ferrite_present"]:
        candidates.append(buffer["tank_ferrite_outer_r"])
    if buffer["rc_ferrite_present"]:
        candidates.append(buffer["rc_ferrite_outer_r"])
    if buffer["tc_shield"]:
        candidates.append(buffer["tc_shield_radius"])
    if buffer["rc_shield"]:
        candidates.append(buffer["rc_shield_radius"])

    # `or 1.0` guards a blank "New Config" buffer (all-zero radii) from a
    # divide-by-zero - the diagram just renders as a tiny dot instead.
    max_radius_mm = max(candidates) or 1.0
    scale = (min(canvas_w, canvas_h) / 2 - margin) / max_radius_mm
    center = (canvas_w / 2, canvas_h / 2)

    elements = []
    # Ferrite/shields first (background layers), coils drawn on top.
    if buffer["tank_ferrite_present"]:
        elements.append({"kind": "tank_ferrite", "radius_px": buffer["tank_ferrite_outer_r"] * scale,
                          "color": _FERRITE_COLOR, "style": "fill"})
    if buffer["rc_ferrite_present"]:
        elements.append({"kind": "rc_ferrite", "radius_px": buffer["rc_ferrite_outer_r"] * scale,
                          "color": _FERRITE_COLOR, "style": "fill"})
    if buffer["tc_shield"]:
        elements.append({"kind": "tc_shield", "radius_px": buffer["tc_shield_radius"] * scale,
                          "color": _SHIELD_COLOR, "style": "stroke"})
    if buffer["rc_shield"]:
        elements.append({"kind": "rc_shield", "radius_px": buffer["rc_shield_radius"] * scale,
                          "color": _SHIELD_COLOR, "style": "stroke"})

    elements.append({"kind": "loop", "radius_px": buffer["loop_r"] * scale, "color": _LOOP_COLOR, "style": "stroke"})
    elements.append({"kind": "tank", "radius_px": buffer["tank_center_r"] * scale, "color": _TANK_COLOR, "style": "stroke"})
    elements.append({"kind": "rc", "radius_px": buffer["rc_radius"] * scale, "color": _RC_COLOR, "style": "stroke"})

    backplanes = []
    if buffer["backplane"]:
        backplanes.append("Backplane")
    if buffer["rc_backplane"]:
        backplanes.append("RC Backplane")

    return {"scale": scale, "center": center, "elements": elements, "backplanes": backplanes}


def _legend_entries(buffer: dict) -> list[tuple[str, imgui.ImVec4]]:
    entries = [("Loop", _LOOP_COLOR), ("Tank", _TANK_COLOR), ("RC", _RC_COLOR)]
    if buffer["tank_ferrite_present"] or buffer["rc_ferrite_present"]:
        entries.append(("Ferrite", _FERRITE_COLOR))
    if buffer["tc_shield"] or buffer["rc_shield"]:
        entries.append(("Shield", _SHIELD_COLOR))
    return entries


def _draw_legend(entries: list[tuple[str, imgui.ImVec4]]) -> None:
    draw_list = imgui.get_window_draw_list()
    swatch = 10.0
    for i, (label, color) in enumerate(entries):
        if i > 0:
            imgui.same_line(0.0, 16.0)
        imgui.begin_group()
        pos = imgui.get_cursor_screen_pos()
        draw_list.add_rect_filled(pos, imgui.ImVec2(pos.x + swatch, pos.y + swatch), imgui.get_color_u32(color))
        imgui.dummy(imgui.ImVec2(swatch, swatch))
        imgui.same_line(0.0, 4.0)
        imgui.text(label)
        imgui.end_group()


def draw(buffer: dict, size: imgui.ImVec2, child_id: str = "coil_diagram_pane") -> None:
    imgui.begin_child(child_id, size, imgui.ChildFlags_.borders.value)
    imgui.text("Coil Geometry")
    imgui.separator()

    if not buffer:
        imgui.text_disabled("Select or create a config to see its diagram.")
        imgui.end_child()
        return

    avail = imgui.get_content_region_avail()
    canvas_h = max(avail.y - 110.0, 80.0)
    canvas_w = max(avail.x, 80.0)
    canvas_origin = imgui.get_cursor_screen_pos()

    layout = compute_layout(buffer, canvas_w, canvas_h)
    draw_list = imgui.get_window_draw_list()
    cx, cy = layout["center"]
    center_px = imgui.ImVec2(canvas_origin.x + cx, canvas_origin.y + cy)

    draw_list.add_circle_filled(center_px, 4.0, imgui.get_color_u32(_CORE_COLOR))
    for el in layout["elements"]:
        col = imgui.get_color_u32(el["color"])
        r = max(el["radius_px"], 1.0)
        if el["style"] == "fill":
            draw_list.add_circle_filled(center_px, r, col)
        else:
            draw_list.add_circle(center_px, r, col, 0, 2.0)

    # Reserve the canvas as a real item before drawing widgets below it - a
    # bare cursor move with nothing following asserts once content is short
    # enough not to need scrolling (the same fix runs_panel.draw_history()
    # needed for its row loop).
    imgui.set_cursor_screen_pos(canvas_origin)
    imgui.dummy(imgui.ImVec2(canvas_w, canvas_h))

    if layout["backplanes"]:
        imgui.text_disabled(" / ".join(layout["backplanes"]))

    _draw_legend(_legend_entries(buffer))

    imgui.text_disabled(
        f"Loop r={buffer['loop_r']:.3g}mm   Tank r={buffer['tank_center_r']:.3g}mm   "
        f"RC r={buffer['rc_radius']:.3g}mm"
    )
    imgui.end_child()
