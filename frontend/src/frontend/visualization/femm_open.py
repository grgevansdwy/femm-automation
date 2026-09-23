"""The "Open in FEMM" button, shared by every panel that draws a geometry.

One module-level launch at a time, run on its own single worker thread:
backend/engine/services/femm_launcher.open_in_femm() shells out to
`winepath` and then `wine`, and a cold wineserver makes that take a second or
two - long enough to visibly stall the frame loop if it ran inline. The FEMM
process itself is detached and outlives both the launch and this app, so the
future completes as soon as FEMM has been handed the script, not when the
user closes the window.

Every button routes through the same launcher, which redraws the model with
backend/engine/core.py's own build_geometry() - so "what the button opens"
and "what the numbers beside it came from" cannot drift apart. This module
only handles the threading, the CoilSet rebuild from a panel's buffer dict,
and the button/status rendering.
"""

from concurrent.futures import ThreadPoolExecutor

from imgui_bundle import imgui

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_STATUS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="femm-open")

# Which button started the in-flight launch, its Future, and the result of the
# last finished one - module-level rather than per-panel state because the
# buttons are scattered across five panels that share no state object, and
# only one launch is allowed at a time anyway.
_owner_id: str | None = None
_future = None
_status: str = ""
_error: str = ""


def busy() -> bool:
    return _future is not None


def coilset_from(buffer: dict):
    """A real CoilSet from a panel's plain field dict (a config form buffer, a
    candidate's stored geometry, ...). Raises TypeError/ValueError if the dict
    doesn't fit today's CoilSet - e.g. a historical AI run saved before a field
    was added; button() turns that into a message rather than a traceback."""
    from engine.models.coilset import CoilSet

    return CoilSet(**buffer)


def poll() -> None:
    """Call once per frame (main.py does): moves a finished launch's outcome
    into the status/error line the owning button renders."""
    global _future, _status, _error
    if _future is None or not _future.done():
        return
    try:
        fem_path = _future.result()
        # The model's own folder + filename, not the absolute path - enough to
        # find it under backend/data/femm_models/, short enough not to
        # wrap three times in a narrow panel.
        _status, _error = f"Opened in FEMM - model saved as {fem_path.parent.name}/{fem_path.name}", ""
    except Exception as exc:
        _status, _error = "", str(exc)
    _future = None


def launch(coilset, distance: float, label: str, owner_id: str) -> None:
    global _owner_id, _future, _status, _error
    from engine.services.femm_launcher import open_in_femm

    _owner_id, _status, _error = owner_id, "", ""
    _future = _EXECUTOR.submit(open_in_femm, coilset, distance, label)


def button(button_id: str, buffer: dict, distance: float, label: str,
           text: str = "Open in FEMM", width: float = 0.0, show_success: bool = True) -> None:
    """The button plus, underneath it, the result of the launch it started.

    buffer: the same field dict the panel drew its diagram from, so the button
    can only ever open the design shown next to it.
    label: names the folder the .fem is written to (a config name, a search
    step) - see femm_launcher.slugify().
    show_success: False drops the success line but keeps the failure one - for
    a button sitting inside a fixed-height box (the diagram panels), where a
    wrapped line of confirmation costs canvas the drawing needs and says
    nothing the FEMM window appearing doesn't. A failure still has to be said
    out loud: nothing else in the app reports one.
    """
    global _owner_id, _status, _error

    in_flight = busy() and _owner_id == button_id
    imgui.begin_disabled(busy() or not buffer)
    clicked = imgui.button(("Opening FEMM..." if in_flight else text) + f"##{button_id}",
                           imgui.ImVec2(width, 0))
    imgui.end_disabled()
    if clicked:
        try:
            coilset = coilset_from(buffer)
        except (TypeError, ValueError) as exc:
            _owner_id, _status, _error = button_id, "", f"Can't rebuild this geometry: {exc}"
        else:
            launch(coilset, distance, label, button_id)

    if _owner_id != button_id:
        return
    if _error or (_status and show_success):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_ERROR_COLOR if _error else _STATUS_COLOR, _error or _status)
        imgui.pop_text_wrap_pos()
