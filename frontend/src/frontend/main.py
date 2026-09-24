"""Entry point: one window, one fixed-panel dashboard (no tabs), wired
straight to backend's engine and repositories.

Mutually exclusive layouts, swapped by a toolbar toggle
(_draw_mode_toggle()) rather than a tab bar or docking system (see
docs/GUI.md for why no docking) - each is a fixed set of bordered panels,
sized each frame from the window's available content region:

Manual Runs mode (_draw_manual_mode(), 3 columns):
  left    - config list (configs_panel.draw_list)
  center  - the selected config's edit form (configs_panel.draw_form)
  right   - stacked top-to-bottom: coil diagram, run controls, run history

AI Agent mode (_draw_agent_mode(), 2 columns):
  left    - interview chat + search progress (agent_panel.draw_chat)
  right   - stacked top-to-bottom: baseline config picker, coil diagram (or
            the best-vs-baseline comparison once a search finishes) /
            AI-run history, search progress feed (agent_panel.*)

Settings mode (screen_settings/settings_panel.py): the agent's prompts and
numbers, edited and saved - see agent/settings/. Opened by the "Settings"
button beside "History" in AI Agent mode; "Back" returns there.

HelloImGui (via immapp.run) provides an implicit full-screen background
window by default (DefaultImGuiWindowType.provide_full_screen_window), so
the dashboard is drawn directly - no extra imgui.begin()/end() wrapper needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend import _sim_path  # noqa: F401 - _sim_path must run before any backend import
from frontend.screen_agent import agent_panel
from frontend.files_backup import backup_io
from frontend.screen_manual import configs_panel, runs_panel
from frontend.screen_settings import settings_panel
from frontend.visualization import coil_diagram, femm_open

from imgui_bundle import imgui, immapp, portable_file_dialogs as pfd

_LEFT_WIDTH = 260.0
_DIAGRAM_HEIGHT = 330.0
_CONTROLS_HEIGHT = 130.0

_STATUS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)


class BackupPanelState:
    """Bulk export-all/import-all - a single toolbar row above the 3-column
    dashboard rather than a whole panel, since it's just two buttons."""

    def __init__(self) -> None:
        self.error: str = ""
        self.status: str = ""
        self._export_dialog = None
        self._import_dialog = None

    def export_all(self, path) -> None:
        Path(path).write_text(backup_io.export_all())
        self.status = f"Backed up to {path}"
        self.error = ""

    def import_all(self, path, configs_panel_state) -> None:
        try:
            text = Path(path).read_text()
        except OSError as exc:
            self.error, self.status = f"Couldn't read {path}: {exc}", ""
            return
        try:
            n_configs, n_runs = backup_io.import_all(text)
        except ValueError as exc:
            self.error, self.status = str(exc), ""
            return
        self.status = f"Restored {n_configs} config(s) and {n_runs} run(s) from {path}"
        self.error = ""
        # Restore writes straight to the database, but configs_panel's own
        # list/buffer are only refreshed by its own actions (Save/Clone/
        # Import/Delete/New) - left untouched here, Restore All would report
        # success while the left list, form, and diagram kept showing
        # whatever was loaded before the restore, which read as "it didn't
        # do anything" even though the data was actually written. Run
        # History doesn't need this: it re-reads the database fresh every
        # frame already.
        configs_panel_state.refresh()
        if configs_panel_state.selected_id is not None:
            configs_panel_state.load(configs_panel_state.selected_id)

    def poll_dialogs(self, configs_panel_state) -> None:
        if self._export_dialog is not None and self._export_dialog.ready():
            result = self._export_dialog.result()
            if result:
                self.export_all(result)
            self._export_dialog = None

        if self._import_dialog is not None and self._import_dialog.ready():
            results = self._import_dialog.result()
            if results:
                self.import_all(results[0], configs_panel_state)
            self._import_dialog = None


class AppState:
    def __init__(self) -> None:
        self.configs_panel = configs_panel.ConfigsPanelState()
        self.runs_panel = runs_panel.RunsPanelState()
        self.backup_panel = BackupPanelState()
        self.agent_panel = agent_panel.AgentPanelState(self.runs_panel.job_runner)
        self.settings_panel = settings_panel.SettingsPanelState()
        self.mode = "manual"  # "manual" | "agent" | "settings" - toggled by _draw_mode_toggle()


def _draw_toolbar(state: BackupPanelState, configs_panel_state) -> None:
    state.poll_dialogs(configs_panel_state)

    if imgui.button("Backup All"):
        state._export_dialog = pfd.save_file(
            "Backup all configs + runs", default_path="femm_automation_backup.json",
            filters=["JSON files", "*.json"],
        )
    imgui.same_line()
    if imgui.button("Restore All"):
        state._import_dialog = pfd.open_file("Restore from backup", filters=["JSON files", "*.json"])

    if state.error:
        imgui.same_line()
        imgui.text_colored(_ERROR_COLOR, state.error)
    elif state.status:
        imgui.same_line()
        imgui.text_colored(_STATUS_COLOR, state.status)


def _draw_mode_toggle(state: AppState) -> None:
    """Two toolbar buttons swap the WHOLE dashboard between the manual-run
    3-column layout and the AI Agent chat layout - a fixed set of bordered
    panels either way, not a docking/tab system (see the module docstring),
    so this is a coarse either/or rather than a real tab bar. The agent's
    Settings screen is reached from a button inside AI Agent mode."""
    imgui.begin_disabled(state.mode == "manual")
    if imgui.button("Manual Runs", imgui.ImVec2(140, 0)):
        state.mode = "manual"
    imgui.end_disabled()
    imgui.same_line()
    imgui.begin_disabled(state.mode == "agent")
    if imgui.button("AI Agent", imgui.ImVec2(140, 0)):
        state.mode = "agent"
    imgui.end_disabled()

    if state.mode in ("agent", "settings"):
        # Right-aligned on the same row. In agent mode: Settings opens the
        # agent's prompts/numbers, History swaps the right column for the
        # AI-run history browser (agent_panel.draw_right_column()). In
        # settings mode a single Back returns to the agent.
        btn_w, gap = 100.0, imgui.get_style().item_spacing.x
        n_btns = 2 if state.mode == "agent" else 1
        imgui.same_line()
        imgui.set_cursor_pos_x(
            imgui.get_cursor_pos_x() + max(imgui.get_content_region_avail().x - (btn_w * n_btns + gap * (n_btns - 1)), 0.0))
        if state.mode == "agent":
            if imgui.button("Settings", imgui.ImVec2(btn_w, 0)):
                state.settings_panel.reload()  # pick up anything changed elsewhere since it was last open
                state.mode = "settings"
            imgui.same_line()
            label = "Back" if state.agent_panel.showing_history else "History"
            if imgui.button(label, imgui.ImVec2(btn_w, 0)):
                state.agent_panel.showing_history = not state.agent_panel.showing_history
        elif imgui.button("Back", imgui.ImVec2(btn_w, 0)):
            state.mode = "agent"


def _draw_manual_mode(state: AppState) -> None:
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
    # femm_label puts this panel's "Open in FEMM" button on its title row -
    # it opens the config currently in the form at the distance the Run
    # controls below are set to, i.e. exactly the run the Run button submits.
    coil_diagram.draw(state.configs_panel.buffer, state.runs_panel.distance, imgui.ImVec2(right_w, _DIAGRAM_HEIGHT),
                      femm_label=str(state.configs_panel.buffer.get("config_name") or "config"))
    runs_panel.draw_controls(state.runs_panel, state.configs_panel, imgui.ImVec2(right_w, _CONTROLS_HEIGHT))
    runs_panel.draw_history(state.runs_panel, state.configs_panel, imgui.ImVec2(right_w, 0))
    imgui.end_group()


def _draw_agent_mode(state: AppState) -> None:
    avail = imgui.get_content_region_avail()
    left_w = max(420.0, avail.x * 0.35)
    right_w = max(avail.x - left_w - 16.0, 300.0)

    agent_panel.draw_chat(state.agent_panel, imgui.ImVec2(left_w, 0))
    imgui.same_line()

    imgui.begin_group()
    agent_panel.draw_right_column(state.agent_panel, imgui.ImVec2(right_w, 0))
    imgui.end_group()


def _gui(state: AppState) -> None:
    state.runs_panel.job_runner.poll()
    state.agent_panel.poll()
    femm_open.poll()

    _draw_toolbar(state.backup_panel, state.configs_panel)
    _draw_mode_toggle(state)

    if state.mode == "agent":
        _draw_agent_mode(state)
    elif state.mode == "settings":
        settings_panel.draw(state.settings_panel, imgui.get_content_region_avail())
    else:
        _draw_manual_mode(state)


def main() -> None:
    from engine.repositories.coilset_repository import seed_starter_configs

    seed_starter_configs()  # first launch: no configs yet -> load the starter designs
    state = AppState()
    immapp.run(
        gui_function=lambda: _gui(state),
        window_title="FEMM Coil Automation",
        window_size=(1600, 900),
    )


if __name__ == "__main__":
    main()
