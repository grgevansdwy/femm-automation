"""Entry point: one window, one tab bar (Config Library / New Run / Run
History), wired straight to coil_simulation's engine and repositories.

HelloImGui (via immapp.run) provides an implicit full-screen background
window by default (DefaultImGuiWindowType.provide_full_screen_window), so
the tab bar is drawn directly - no extra imgui.begin()/end() wrapper needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_app import _sim_path  # noqa: F401 - must run before any coil_simulation import
from desktop_app.panels import configs_panel, runs_panel

from imgui_bundle import imgui, immapp


class AppState:
    def __init__(self) -> None:
        self.configs_panel = configs_panel.ConfigsPanelState()
        self.runs_panel = runs_panel.RunsPanelState()


def _gui(state: AppState) -> None:
    state.runs_panel.job_runner.poll()

    if imgui.begin_tab_bar("main_tabs"):
        opened, _ = imgui.begin_tab_item("Config Library")
        if opened:
            configs_panel.draw(state.configs_panel)
            imgui.end_tab_item()

        opened, _ = imgui.begin_tab_item("New Run")
        if opened:
            runs_panel.draw_new_run(state.runs_panel)
            imgui.end_tab_item()

        opened, _ = imgui.begin_tab_item("Run History")
        if opened:
            runs_panel.draw_run_history(state.runs_panel)
            imgui.end_tab_item()

        imgui.end_tab_bar()


def main() -> None:
    state = AppState()
    immapp.run(
        gui_function=lambda: _gui(state),
        window_title="FEMM Coil Automation",
        window_size=(1280, 800),
    )


if __name__ == "__main__":
    main()
