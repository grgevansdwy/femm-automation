"""Toasts: a short notification in the top right corner that fades away by itself,
for the result of an action (an import, an export) that needs no text left on the screen.

Anything calls success() or error(); main.py calls draw() once a frame. A click
on a toast closes it early. An error stays a little longer than a success.
"""

import time

from imgui_bundle import imgui

SUCCESS_SECONDS = 5.0
ERROR_SECONDS = 9.0
_FADE_SECONDS = 0.6
_WIDTH = 380.0
_MARGIN = 16.0
_GAP = 8.0
_PAD = 10.0
_MAX_SHOWN = 4

_SUCCESS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_ERROR_COLOR = imgui.ImVec4(0.95, 0.4, 0.4, 1.0)

_toasts: list[dict] = []  # oldest first: {"text", "error", "until"}


def success(text: str) -> None:
    _toasts.append({"text": text, "error": False, "until": time.monotonic() + SUCCESS_SECONDS})


def error(text: str) -> None:
    _toasts.append({"text": text, "error": True, "until": time.monotonic() + ERROR_SECONDS})


def active() -> list[tuple[str, bool]]:
    """(text, is_error) of every toast still showing, oldest first."""
    _drop_expired()
    return [(t["text"], t["error"]) for t in _toasts]


def clear() -> None:
    _toasts.clear()


def _drop_expired() -> None:
    now = time.monotonic()
    _toasts[:] = [t for t in _toasts if t["until"] > now]


def draw() -> None:
    _drop_expired()
    viewport = imgui.get_main_viewport()
    right = viewport.pos.x + viewport.size.x - _MARGIN
    y = viewport.pos.y + _MARGIN
    flags = (imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.always_auto_resize.value
             | imgui.WindowFlags_.no_saved_settings.value | imgui.WindowFlags_.no_focus_on_appearing.value
             | imgui.WindowFlags_.no_nav.value | imgui.WindowFlags_.no_move.value)
    now = time.monotonic()
    for index, toast in enumerate(reversed(_toasts[-_MAX_SHOWN:])):  # newest on top
        fade = min(max((toast["until"] - now) / _FADE_SECONDS, 0.0), 1.0)
        text_w = _WIDTH - 2 * _PAD
        height = imgui.calc_text_size(toast["text"], wrap_width=text_w).y + 2 * _PAD
        imgui.set_next_window_pos(imgui.ImVec2(right, y), imgui.Cond_.always.value, imgui.ImVec2(1.0, 0.0))
        imgui.set_next_window_size_constraints(imgui.ImVec2(_WIDTH, 0), imgui.ImVec2(_WIDTH, 10_000))
        imgui.set_next_window_bg_alpha(0.96)
        imgui.push_style_var(imgui.StyleVar_.alpha.value, fade)
        imgui.push_style_var(imgui.StyleVar_.window_padding.value, imgui.ImVec2(_PAD, _PAD))
        if imgui.begin(f"##toast_{index}", None, flags):
            imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + text_w)
            imgui.text_colored(_ERROR_COLOR if toast["error"] else _SUCCESS_COLOR, toast["text"])
            imgui.pop_text_wrap_pos()
            if imgui.is_window_hovered() and imgui.is_mouse_clicked(0):
                toast["until"] = 0.0  # a click closes it
        imgui.end()
        imgui.pop_style_var(2)
        y += height + _GAP
