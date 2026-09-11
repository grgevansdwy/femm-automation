"""Shared scrolling feed widget for a growing list of (label, text) rows -
backs both the AI Agent chat transcript and its search-progress feed
(gui/src/gui/panels/agent_panel.py). Built on runs_panel.draw_history()'s
selectable-row skeleton (same fix for the "bare cursor move with nothing
drawn after it asserts" gotcha), extended with real text wrapping and a
row height computed from the wrapped text's own size instead of a fixed
two-line height, since chat/reasoning text is free-form and variable-length
unlike a run-history row's two fixed lines.
"""

from imgui_bundle import imgui

_LABEL_COLOR = imgui.ImVec4(0.65, 0.65, 0.7, 1.0)
_ROW_PAD = 8.0


def draw_feed(child_id: str, rows: list[tuple[str, str]], size: imgui.ImVec2, auto_scroll: bool = True) -> None:
    imgui.begin_child(child_id, size, imgui.ChildFlags_.borders.value)
    row_width = imgui.get_content_region_avail().x

    for label, text in rows:
        row_top = imgui.get_cursor_screen_pos()
        imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + _ROW_PAD, row_top.y + 4))
        imgui.begin_group()
        imgui.text_colored(_LABEL_COLOR, label)
        imgui.push_text_wrap_pos(0.0)  # wrap at this child window's right edge
        imgui.text_wrapped(text or "")
        imgui.pop_text_wrap_pos()
        imgui.end_group()
        content_h = imgui.get_item_rect_size().y

        # A real item (not just a bare cursor move) is required here so the
        # child window's content bounds actually grow to include the gap -
        # the same fix runs_panel.draw_history()'s row loop needed.
        imgui.set_cursor_screen_pos(row_top)
        imgui.dummy(imgui.ImVec2(row_width, content_h + 4 + _ROW_PAD))
        imgui.separator()

    if auto_scroll and imgui.get_scroll_y() >= imgui.get_scroll_max_y():
        # Only re-pin to the bottom if we were ALREADY there before this
        # frame's rows were added - an unconditional set_scroll_here_y()
        # every frame fights any attempt to scroll up to read earlier
        # content, since it overrides the user's scroll position on the
        # very next frame (confirmed live: scrolling up did nothing). This
        # is Dear ImGui's own standard log-window idiom for "stick to the
        # bottom unless the user scrolled away."
        imgui.set_scroll_here_y(1.0)
    imgui.end_child()
