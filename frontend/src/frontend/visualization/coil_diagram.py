"""Live coil-geometry schematic, redrawn every frame straight from the
config form's current buffer - no caching needed since it's cheap pure
geometry, so a value edited in the form is reflected here on the very next
frame. Two views share the panel:

- Top-down (compute_layout/draw's left half): concentric rings sized by
  each coil's actual radius - answers "how do the coil radii compare."
- Axial cross-section (compute_axial_layout/draw's right half): a direct
  port of backend/engine/core.py's real stacking math (build_script(),
  not re-derived) - Loop below Tank (Tank at y=0) on the transmit side, RC
  drawn `distance` mm above them facing down, ferrite/shield/backplane
  stacking further outward on each side. Answers "how are they actually
  arranged in space," which the top-down view alone can't - it collapses
  the whole axial arrangement onto one plane.

v1/v2 scope (per plan): a proportional schematic in both views - sized/
positioned by real geometry, but turn count, trace width, winding path, and
the sub-mm per-PCB-layer offset inside draw_coil_traces() are not modeled -
not a literal render. See docs/GUI.md for the reasoning.
"""

from imgui_bundle import imgui

from . import femm_open

# Every CoilSet field this module's geometry math actually reads (grep-
# confirmed against compute_layout()/compute_axial_layout() below) - radii,
# presence flags, and the axial offsets/thicknesses of ferrite/shield/
# backplane/PCB. Used elsewhere (agent_candidate_card.py's finished-search
# summary) to tell a real user which of a candidate's changed fields would
# even show up here - winding detail (turns, trace width/spacing, parallel
# traces) is deliberately NOT in this set: per this module's own docstring,
# it was never modeled, at any scale.
DIAGRAM_FIELDS = frozenset({
    "loop_r", "tank_center_r", "rc_radius",
    "tank_ferrite_present", "tank_ferrite_outer_r", "tank_ferrite_thickness", "tank_ferrite_y_offset",
    "rc_ferrite_present", "rc_ferrite_outer_r", "rc_ferrite_thickness", "rc_ferrite_y_offset",
    "tc_shield", "tc_shield_radius", "tc_shield_thickness", "tc_shield_y_distance",
    "rc_shield", "rc_shield_radius", "rc_shield_thickness", "rc_shield_y_distance",
    "backplane", "backplane_y_distance", "rc_backplane", "rc_backplane_y_distance",
    "loop_offset", "loop_PCB_thickness", "tank_PCB_thickness", "rc_PCB_thickness",
    "loop_trace_thickness", "tank_trace_thickness", "rc_trace_thickness",
})

_LOOP_COLOR = imgui.ImVec4(0.35, 0.65, 0.95, 1.0)
_TANK_COLOR = imgui.ImVec4(0.95, 0.65, 0.25, 1.0)
_RC_COLOR = imgui.ImVec4(0.75, 0.45, 0.95, 1.0)
_FERRITE_COLOR = imgui.ImVec4(0.55, 0.75, 0.55, 0.35)
_SHIELD_COLOR = imgui.ImVec4(0.6, 0.6, 0.65, 1.0)
_BACKPLANE_COLOR = imgui.ImVec4(0.8, 0.55, 0.35, 0.8)
_CORE_COLOR = imgui.ImVec4(0.85, 0.85, 0.85, 1.0)
_MIN_BAND_PX = 3.0  # a coil/ferrite/shield/backplane band never renders thinner than this


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

    return {"scale": scale, "center": center, "elements": elements}


def compute_axial_layout(buffer: dict, distance: float, canvas_w: float, canvas_h: float, margin: float = 20.0) -> dict:
    """Pure geometry for the axial (side/cross-section) view - a direct port
    of backend/engine/core.py's build_script() real stacking math (not
    re-derived), so this actually matches what FEMM solves: TC (Loop below
    Tank, Tank at y=0) on one side, RC drawn `distance` mm above it facing
    down, ferrite/shield/backplane stacking further outward on each side.
    Each element is mirrored +-x around the axis of revolution at draw time.

    Every element carries top_mm/bottom_mm (top_mm >= bottom_mm) and
    half_width_mm in real geometry units, plus their already-scaled/
    positioned y_top_px/y_bottom_px/x_half_width_px - same "elements ready
    to draw" convention compute_layout() already returns.
    """
    from engine.constants import backplane_length, backplane_thickness

    elements = []

    # ---- TC side: walks downward (more negative y) from the loop stack,
    # exactly mirroring engine/core.py's tc_bottom_y walk - ferrite closest to
    # the coils, then shield, then backplane furthest out. ----
    tc_bottom_y = (
        -buffer["tank_PCB_thickness"] - buffer["loop_offset"] - buffer["loop_PCB_thickness"]
        - buffer["tank_trace_thickness"] - (2 * buffer["loop_trace_thickness"])
    )
    if buffer["tank_ferrite_present"]:
        tc_bottom_y -= buffer["tank_ferrite_y_offset"]
        elements.append({"kind": "tank_ferrite", "top_mm": tc_bottom_y, "bottom_mm": tc_bottom_y - buffer["tank_ferrite_thickness"],
                          "half_width_mm": buffer["tank_ferrite_outer_r"], "color": _FERRITE_COLOR})
        tc_bottom_y -= buffer["tank_ferrite_thickness"]
    if buffer["tc_shield"]:
        tc_bottom_y -= buffer["tc_shield_y_distance"]
        elements.append({"kind": "tc_shield", "top_mm": tc_bottom_y, "bottom_mm": tc_bottom_y - buffer["tc_shield_thickness"],
                          "half_width_mm": buffer["tc_shield_radius"], "color": _SHIELD_COLOR})
        tc_bottom_y -= buffer["tc_shield_thickness"]
    if buffer["backplane"]:
        tc_bottom_y -= buffer["backplane_y_distance"] - 3  # 3 = enclosure thickness, same as engine/core.py
        elements.append({"kind": "backplane", "top_mm": tc_bottom_y, "bottom_mm": tc_bottom_y - backplane_thickness,
                          "half_width_mm": backplane_length / 2, "color": _BACKPLANE_COLOR})
        tc_bottom_y -= backplane_thickness

    # ---- RC side: walks upward (more positive y) from `distance`, mirroring
    # engine/core.py's rc_top_y walk. ----
    rc_top_y = buffer["rc_PCB_thickness"] + distance + (buffer["rc_trace_thickness"] * 2) + buffer["rc_ferrite_y_offset"]
    if buffer["rc_ferrite_present"]:
        rc_top_y += buffer["rc_ferrite_thickness"]
        elements.append({"kind": "rc_ferrite", "top_mm": rc_top_y, "bottom_mm": rc_top_y - buffer["rc_ferrite_thickness"],
                          "half_width_mm": buffer["rc_ferrite_outer_r"], "color": _FERRITE_COLOR})
    if buffer["rc_shield"]:
        rc_top_y += buffer["rc_shield_y_distance"]
        rc_top_y += buffer["rc_shield_thickness"]
        elements.append({"kind": "rc_shield", "top_mm": rc_top_y, "bottom_mm": rc_top_y - buffer["rc_shield_thickness"],
                          "half_width_mm": buffer["rc_shield_radius"], "color": _SHIELD_COLOR})
    if buffer["rc_backplane"]:
        rc_top_y += buffer["rc_backplane_y_distance"] + backplane_thickness + 3  # 3 = enclosure thickness
        elements.append({"kind": "backplane", "top_mm": rc_top_y, "bottom_mm": rc_top_y - backplane_thickness,
                          "half_width_mm": backplane_length / 2, "color": _BACKPLANE_COLOR})
        rc_top_y += backplane_thickness

    # ---- The three coils themselves - thin bands at their real y (the same
    # top_of_trace values engine/core.py's draw_coil_traces() calls use). Tank's
    # true offset is +tank_trace_thickness (~0.07mm) above 0 - negligible at
    # any diagram scale, so it's simplified to the documented y=0 reference. ----
    loop_y = -buffer["tank_PCB_thickness"] - buffer["loop_offset"]
    for kind, y, radius, color in (
        ("loop", loop_y, buffer["loop_r"], _LOOP_COLOR),
        ("tank", 0.0, buffer["tank_center_r"], _TANK_COLOR),
        ("rc", distance, buffer["rc_radius"], _RC_COLOR),
    ):
        elements.append({"kind": kind, "top_mm": y, "bottom_mm": y, "half_width_mm": radius, "color": color})

    all_y = [v for el in elements for v in (el["top_mm"], el["bottom_mm"])]
    y_span_mm = max(max(all_y) - min(all_y), 1.0)
    # `or 1.0` guards a blank "New Config" buffer (all-zero radii) the same
    # way compute_layout()'s max_radius_mm does.
    x_max_mm = max((el["half_width_mm"] for el in elements), default=0.0) or 1.0

    scale = min((canvas_h - 2 * margin) / y_span_mm, (canvas_w / 2 - margin) / x_max_mm)
    y_top_bound = max(all_y)
    center_x = canvas_w / 2

    for el in elements:
        el["x_half_width_px"] = el["half_width_mm"] * scale
        el["y_top_px"] = margin + (y_top_bound - el["top_mm"]) * scale
        el["y_bottom_px"] = margin + (y_top_bound - el["bottom_mm"]) * scale

    return {"scale": scale, "center_x": center_x, "elements": elements}


def _legend_entries(buffer: dict) -> list[tuple[str, imgui.ImVec4]]:
    entries = [("Loop", _LOOP_COLOR), ("Tank", _TANK_COLOR), ("RC", _RC_COLOR)]
    if buffer["tank_ferrite_present"] or buffer["rc_ferrite_present"]:
        entries.append(("Ferrite", _FERRITE_COLOR))
    if buffer["tc_shield"] or buffer["rc_shield"]:
        entries.append(("Shield", _SHIELD_COLOR))
    if buffer["backplane"] or buffer["rc_backplane"]:
        entries.append(("Backplane", _BACKPLANE_COLOR))
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


def _draw_top_down(buffer: dict, draw_list, origin: imgui.ImVec2, canvas_w: float, canvas_h: float) -> None:
    layout = compute_layout(buffer, canvas_w, canvas_h)
    cx, cy = layout["center"]
    center_px = imgui.ImVec2(origin.x + cx, origin.y + cy)

    draw_list.add_circle_filled(center_px, 4.0, imgui.get_color_u32(_CORE_COLOR))
    for el in layout["elements"]:
        col = imgui.get_color_u32(el["color"])
        r = max(el["radius_px"], 1.0)
        if el["style"] == "fill":
            draw_list.add_circle_filled(center_px, r, col)
        else:
            draw_list.add_circle(center_px, r, col, 0, 2.0)


def _draw_cross_section(buffer: dict, distance: float, draw_list, origin: imgui.ImVec2, canvas_w: float, canvas_h: float) -> None:
    layout = compute_axial_layout(buffer, distance, canvas_w, canvas_h)
    cx = layout["center_x"]

    # A thin vertical line marks the axis of revolution every element mirrors around.
    draw_list.add_line(imgui.ImVec2(origin.x + cx, origin.y), imgui.ImVec2(origin.x + cx, origin.y + canvas_h),
                        imgui.get_color_u32(_CORE_COLOR), 1.0)

    for el in layout["elements"]:
        col = imgui.get_color_u32(el["color"])
        half_w = max(el["x_half_width_px"], 1.0)
        top_px, bottom_px = el["y_top_px"], el["y_bottom_px"]
        if bottom_px - top_px < _MIN_BAND_PX:
            mid = (top_px + bottom_px) / 2.0
            top_px, bottom_px = mid - _MIN_BAND_PX / 2.0, mid + _MIN_BAND_PX / 2.0
        draw_list.add_rect_filled(
            imgui.ImVec2(origin.x + cx - half_w, origin.y + top_px),
            imgui.ImVec2(origin.x + cx + half_w, origin.y + bottom_px),
            col,
        )


def draw(buffer: dict, distance: float, size: imgui.ImVec2, child_id: str = "coil_diagram_pane",
         compact: bool = False, title: str = "Coil Geometry", femm_label: str | None = None) -> None:
    """compact=True drops the title, legend and radius footer so the
    drawing itself fills a small box - for the AI agent's per-candidate
    cards, where the full panel's ~130px of chrome left almost no room for
    the geometry (and made the box scroll).

    femm_label names this design for the "Open in FEMM" button placed on the
    title row (a config name, "Baseline", "Proposed step 3", ...). Passing it
    is what puts the button there; None leaves the panel exactly as it was.
    The button is deliberately part of THIS panel rather than each caller's
    own layout: it opens `buffer` at `distance` - the very geometry drawn in
    this box - so the two can't get out of step. compact=True has no title row
    to hang it on, so those callers place their own button (see
    agent_candidate_card.draw_candidate_card)."""
    imgui.begin_child(child_id, size, imgui.ChildFlags_.borders.value,
                      imgui.WindowFlags_.no_scrollbar.value if compact else 0)
    if not compact:
        if femm_label is not None:
            # Without this the title sits at the top of the taller button's
            # row instead of centred against it.
            imgui.align_text_to_frame_padding()
        imgui.text(title)
        if femm_label is not None:
            # Right-aligned on the title row rather than under the drawing:
            # below the canvas the panel's height is already fully accounted
            # for (legend + radius footer, see chrome_h), so a button there
            # would push the footer out of the box.
            button_w = 120.0
            imgui.same_line()
            imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + max(imgui.get_content_region_avail().x - button_w, 0.0))
            femm_open.button(f"{child_id}_femm", buffer, distance, femm_label, width=button_w,
                             show_success=False)
        imgui.separator()

    if not buffer:
        imgui.text_disabled("Select or create a config to see its diagram.")
        imgui.end_child()
        return

    avail = imgui.get_content_region_avail()
    # Below the canvas come exactly two lines (legend, radius footer) -
    # reserve their real height rather than a flat 130px, which left a
    # large blank gap under the footer in every size of this panel.
    chrome_h = 2 * imgui.get_text_line_height_with_spacing() + imgui.get_style().item_spacing.y
    canvas_h = max(avail.y - 4.0, 40.0) if compact else max(avail.y - chrome_h, 80.0)
    canvas_w = max(avail.x, 80.0 if compact else 160.0)
    canvas_origin = imgui.get_cursor_screen_pos()
    half_w = canvas_w / 2.0

    draw_list = imgui.get_window_draw_list()
    _draw_top_down(buffer, draw_list, canvas_origin, half_w, canvas_h)
    separator_x = canvas_origin.x + half_w
    draw_list.add_line(imgui.ImVec2(separator_x, canvas_origin.y), imgui.ImVec2(separator_x, canvas_origin.y + canvas_h),
                        imgui.get_color_u32(_SHIELD_COLOR), 1.0)
    _draw_cross_section(buffer, distance, draw_list, imgui.ImVec2(separator_x, canvas_origin.y), half_w, canvas_h)

    # Reserve the canvas as a real item before drawing widgets below it - a
    # bare cursor move with nothing following asserts once content is short
    # enough not to need scrolling (the same fix runs_panel.draw_history()
    # needed for its row loop).
    imgui.set_cursor_screen_pos(canvas_origin)
    imgui.dummy(imgui.ImVec2(canvas_w, canvas_h))

    if compact:
        imgui.end_child()
        return

    _draw_legend(_legend_entries(buffer))

    imgui.text_disabled(
        f"Loop r={buffer['loop_r']:.3g}mm   Tank r={buffer['tank_center_r']:.3g}mm   "
        f"RC r={buffer['rc_radius']:.3g}mm   Distance={distance:.3g}mm"
    )
    imgui.end_child()
