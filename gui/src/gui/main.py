"""Entry point: one window, one fixed-panel dashboard (no tabs), wired
straight to coil_simulation's engine and repositories.

Layout is 3 fixed columns, sized each frame from the window's available
content region (not a docking system - see docs/gui.md for why):
  left    - config list (configs_panel.draw_list)
  center  - the selected config's edit form (configs_panel.draw_form)
  right   - stacked top-to-bottom: coil diagram, run controls, run history

HelloImGui (via immapp.run) provides an implicit full-screen background
window by default (DefaultImGuiWindowType.provide_full_screen_window), so
the dashboard is drawn directly - no extra imgui.begin()/end() wrapper needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui import _sim_path  # noqa: F401 - must run before any coil_simulation import
from gui.panels import coil_diagram, configs_panel, runs_panel

from imgui_bundle import imgui, immapp

_LEFT_WIDTH = 260.0
_DIAGRAM_HEIGHT = 360.0
_CONTROLS_HEIGHT = 130.0


class AppState:
    def __init__(self) -> None:
        self.configs_panel = configs_panel.ConfigsPanelState()
        self.runs_panel = runs_panel.RunsPanelState()


def _gui(state: AppState) -> None:
    state.runs_panel.job_runner.poll()

    avail = imgui.get_content_region_avail()
    # The right column carries the run-result table (3 columns of 6-sig-fig
    # numbers) next to the history list, so it gets the larger share; the
    # form only needs enough width for a label + input per row.
    right_w = max(520.0, avail.x * 0.40)
    center_w = max(avail.x - _LEFT_WIDTH - right_w - 16.0, 300.0)

    configs_panel.draw_list(state.configs_panel, imgui.ImVec2(_LEFT_WIDTH, 0))
    imgui.same_line()
    configs_panel.draw_form(state.configs_panel, imgui.ImVec2(center_w, 0))
    imgui.same_line()

    imgui.begin_group()
    coil_diagram.draw(state.configs_panel.buffer, imgui.ImVec2(right_w, _DIAGRAM_HEIGHT))
    runs_panel.draw_controls(state.runs_panel, imgui.ImVec2(right_w, _CONTROLS_HEIGHT))
    runs_panel.draw_history(state.runs_panel, imgui.ImVec2(right_w, 0))
    imgui.end_group()


def main() -> None:
    state = AppState()
    immapp.run(
        gui_function=lambda: _gui(state),
        window_title="FEMM Coil Automation",
        window_size=(1600, 900),
    )


if __name__ == "__main__":
    main()
