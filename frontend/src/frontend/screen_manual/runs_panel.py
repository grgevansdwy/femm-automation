"""Run controls + run history - the dashboard's right column, stacked below
the coil diagram (draw_controls()/draw_history(), drawn by main.py, not tabs).

Shares one RunsPanelState (and its JobRunner) between both, since a run
submitted from the controls block is exactly what shows up in history.
"""

import dataclasses
from datetime import datetime
from pathlib import Path

from imgui_bundle import imgui, portable_file_dialogs as pfd

from ..files_backup import run_io
from ..services.job_runner import JobRunner, JobStatus, RESULTS_ROOT, coilset_signature
from ..visualization import femm_open
from ..visualization.result_table import draw_result_table

_STATUS_COLORS = {
    JobStatus.QUEUED.value: imgui.ImVec4(0.7, 0.7, 0.7, 1.0),
    JobStatus.RUNNING.value: imgui.ImVec4(0.9, 0.75, 0.2, 1.0),
    JobStatus.DONE.value: imgui.ImVec4(0.35, 0.8, 0.45, 1.0),
    JobStatus.FAILED.value: imgui.ImVec4(0.9, 0.35, 0.35, 1.0),
}
_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)


def _truncate(text: str, max_chars: int) -> str:
    """Character-count truncation, not pixel-measured - simple and good
    enough for the narrow run-history list column (only ~200px wide now
    that it shares the dashboard's right column instead of a whole tab)."""
    # Plain ASCII dots, not a Unicode ellipsis - the default ImGui font atlas
    # typically only covers Basic Latin, so "…" risks a missing-glyph box.
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def _format_distance(distance) -> str:
    if isinstance(distance, (int, float)):
        return f"{distance:.3f} mm"
    return str(distance)  # a non-numeric distance means the run failed before solving


def _format_run_time(row: dict) -> str:
    """When this run was last used, in UTC - result timestamps are already
    UTC (ISO 8601 with a +00:00 offset), just reformatted for reading
    instead of `2026-09-02T22:58:10.899672+00:00`. last_used_at matches the
    same field Run History sorts by, so this always agrees with the row's
    position in the list (a reused cache hit shows when it was reused, not
    just when it was originally solved)."""
    iso = row.get("last_used_at") or row.get("finished_at") or row.get("started_at") or row.get("created_at")
    if not iso:
        return "time unknown"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return iso


class RunsPanelState:
    def __init__(self):
        self.job_runner = JobRunner()
        self.configs: list[dict] = []
        self.distance: float = 5.0
        self.selected_run_id: str | None = None
        self.new_run_error: str = ""
        self.new_run_status: str = ""
        self.io_error: str = ""
        self.io_status: str = ""
        self._export_dialog = None  # a pfd.save_file() handle while one is open, else None
        self._import_dialog = None  # a pfd.open_file() handle while one is open, else None
        self.refresh_configs()

    def refresh_configs(self) -> None:
        from engine.repositories.coilset_repository import list_configs

        self.configs = list_configs()

    def submit_run(self, configs_panel_state) -> None:
        selected_id = configs_panel_state.selected_id
        if not selected_id:
            self.new_run_error = "Select a config first."
            return
        from engine.repositories.coilset_repository import get_coilset

        record = next((c for c in self.configs if c["id"] == selected_id), None)
        coilset = get_coilset(selected_id)

        cached = self.job_runner.find_cached(coilset, self.distance)
        if cached is not None:
            # Identical geometry + distance already solved - show that
            # result instead of re-running femmcli and writing a duplicate.
            # touch() bumps last_used_at so it also rises to the top of Run
            # History, like it was just run, without pretending it re-solved.
            cached = self.job_runner.touch(cached)
            self.selected_run_id = cached["run_id"]
            self.new_run_error = ""
            self.new_run_status = f"Reused existing result (run {cached['run_id']}) - same geometry and distance already solved."
            return

        job = self.job_runner.submit(
            coilset, config_id=selected_id,
            config_name=record["name"] if record else selected_id,
            distance=self.distance,
        )
        self.selected_run_id = job.id
        self.new_run_error = ""
        self.new_run_status = ""

    def history_rows(self) -> list[dict]:
        """Persisted results merged with any still-in-memory job (a running
        job overrides its own eventually-written file with fresher status)."""
        from engine.repositories.run_result_repository import RunResultRepository

        rows = {r["run_id"]: r for r in RunResultRepository.list_results(root=RESULTS_ROOT)}
        for job_id, job in self.job_runner.jobs.items():
            rows[job_id] = job.as_dict()
        ordered = list(rows.values())
        ordered.sort(
            key=lambda r: r.get("last_used_at") or r.get("finished_at") or r.get("started_at") or r.get("created_at") or "",
            reverse=True,
        )
        return ordered

    def export(self, path) -> None:
        if not self.selected_run_id:
            self.io_error = "Select a run to export first."
            return
        row = next((r for r in self.history_rows() if r["run_id"] == self.selected_run_id), None)
        if row is None:
            self.io_error = "Selected run no longer exists."
            return
        Path(path).write_text(run_io.export_record(row))
        self.io_status = f"Exported to {path}"
        self.io_error = ""

    def import_from(self, path) -> None:
        try:
            text = Path(path).read_text()
        except OSError as exc:
            self.io_error, self.io_status = f"Couldn't read {path}: {exc}", ""
            return
        try:
            row = run_io.parse_import(text)
        except ValueError as exc:
            self.io_error, self.io_status = str(exc), ""
            return

        row = run_io.resolve_import_id(row)
        from engine.repositories.run_result_repository import RunResultRepository

        RunResultRepository(run_id=row["run_id"]).save_result_json(row)
        self.selected_run_id = row["run_id"]
        self.io_status = f"Imported run {row['run_id']}"
        self.io_error = ""


def _poll_export_import_dialogs(state: RunsPanelState) -> None:
    if state._export_dialog is not None and state._export_dialog.ready():
        result = state._export_dialog.result()  # save_file() -> a single path string, "" if cancelled
        if result:
            state.export(result)
        state._export_dialog = None

    if state._import_dialog is not None and state._import_dialog.ready():
        results = state._import_dialog.result()  # open_file() -> a list of paths, [] if cancelled
        if results:
            state.import_from(results[0])
        state._import_dialog = None


def draw_controls(state: RunsPanelState, configs_panel_state, size: imgui.ImVec2 = imgui.ImVec2(0, 130)) -> None:
    # refresh_configs() used to run only once, at RunsPanelState.__init__ -
    # so this dropdown never noticed a config created/renamed/deleted on the
    # Config Library panel for the rest of the session. Re-reading it here
    # every frame this panel draws keeps it in sync, the same way
    # draw_history() already re-reads history_rows() every frame.
    state.refresh_configs()

    imgui.begin_child("run_controls_pane", size, imgui.ChildFlags_.borders.value)
    imgui.text("Run")
    imgui.separator()

    if not state.configs:
        imgui.text_wrapped("No configs yet - create one on the left first.")
        imgui.end_child()
        return

    names = [c["name"] for c in state.configs]
    ids = [c["id"] for c in state.configs]
    # Driven by configs_panel_state.selected_id (not a separate field here) -
    # this is the same selection the left list / center form / diagram use,
    # so picking a config anywhere keeps all of them in agreement.
    selected_id = configs_panel_state.selected_id
    current_index = ids.index(selected_id) if selected_id in ids else -1

    # -70 leaves room for the combo's own trailing "Config" label - sizing
    # this to the full available width clips that label at the panel edge.
    combo_width = (size.x - 86.0) if size.x > 0 else 190.0
    imgui.set_next_item_width(max(combo_width, 100.0))
    changed, new_index = imgui.combo("Config", current_index, names)
    if changed:
        configs_panel_state.load(ids[new_index])

    imgui.set_next_item_width(160)
    changed, new_distance = imgui.input_float("Distance (mm)", state.distance, format="%.3f")
    if changed:
        state.distance = new_distance

    if imgui.button("Run"):
        state.submit_run(configs_panel_state)

    if state.new_run_error:
        imgui.text_colored(_ERROR_COLOR, state.new_run_error)
    elif state.new_run_status:
        imgui.text_colored(_STATUS_COLORS[JobStatus.DONE.value], state.new_run_status)

    imgui.end_child()
    # Run History is always visible right below this panel now (no more
    # tabs to switch to), so there's no need for a separate "last submitted
    # run" preview here the way the old New Run tab had one.


def draw_history(state: RunsPanelState, configs_panel_state, size: imgui.ImVec2 = imgui.ImVec2(0, 0)) -> None:
    _poll_export_import_dialogs(state)

    rows = state.history_rows()
    known_config_ids = {c["id"] for c in configs_panel_state.configs}

    imgui.begin_disabled(state.selected_run_id is None)
    if imgui.button("Export run"):
        selected = next((r for r in rows if r["run_id"] == state.selected_run_id), None)
        default_name = f"{(selected or {}).get('run_id', 'run')}.json"
        state._export_dialog = pfd.save_file("Export run", default_path=default_name, filters=["JSON files", "*.json"])
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("Import run"):
        state._import_dialog = pfd.open_file("Import run", filters=["JSON files", "*.json"])

    if state.io_error:
        imgui.text_colored(_ERROR_COLOR, state.io_error)
    elif state.io_status:
        imgui.text_colored(_STATUS_COLORS[JobStatus.DONE.value], state.io_status)

    # Narrower than the old standalone Run History tab's 420px: this now
    # shares the dashboard's right column instead of a whole tab's width.
    list_width = min(240.0, size.x * 0.45) if size.x > 0 else 240.0
    detail_width = max(size.x - list_width, 0.0) if size.x > 0 else 0.0

    imgui.begin_child("run_history_list", imgui.ImVec2(list_width, size.y), imgui.ChildFlags_.borders.value)
    row_height = imgui.get_text_line_height_with_spacing() * 2 + 10
    row_width = imgui.get_content_region_avail().x
    for row in rows:
        status = row.get("status", "")
        color = _STATUS_COLORS.get(status, imgui.ImVec4(1, 1, 1, 1))
        selected = row["run_id"] == state.selected_run_id

        row_top = imgui.get_cursor_screen_pos()
        clicked, _ = imgui.selectable(f"##row_{row['run_id']}", selected, 0, imgui.ImVec2(row_width, row_height))
        if clicked:
            state.selected_run_id = row["run_id"]
            # Selecting a run also loads the config that produced it, so the
            # left list / center form / diagram stay in sync with the run
            # you're looking at - unless that config was since deleted, in
            # which case there's nothing to load and the current selection
            # (if any) is left alone.
            if row["config_id"] in known_config_ids and row["config_id"] != configs_panel_state.selected_id:
                configs_panel_state.load(row["config_id"])

        # Two real Text widgets drawn on top of the (label-less) selectable
        # above, so each line can have its own color/weight - a plain
        # single-line selectable label can't do that.
        imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + 8, row_top.y + 5))
        imgui.begin_group()
        # The status word lives on the dimmed second line rather than
        # line 1: line 1 is already colored by status, and squeezing
        # "[done]" onto it clipped the config name at this column width.
        imgui.text_colored(color, f"{_truncate(row['config_name'], 18)}  @ {_format_distance(row['distance'])}")
        imgui.text_disabled(f"{status} - {_format_run_time(row)}")
        imgui.end_group()

        # A real item (not just a bare cursor move) is required here so the
        # child window's content bounds actually grow to include the gap -
        # a lone SetCursorScreenPos with nothing following it asserts once
        # content is short enough that the child isn't already scrolling.
        imgui.set_cursor_screen_pos(row_top)
        imgui.dummy(imgui.ImVec2(row_width, row_height + 4))
    imgui.end_child()

    imgui.same_line()

    imgui.begin_child("run_history_detail", imgui.ImVec2(detail_width, size.y), imgui.ChildFlags_.borders.value)
    selected_row = next((r for r in rows if r["run_id"] == state.selected_run_id), None)
    if selected_row is None:
        imgui.text_wrapped("Select a run on the left to see its result.")
    else:
        _draw_run_detail(selected_row, state.job_runner)
    imgui.end_child()


def run_geometry(row: dict, job_runner: JobRunner) -> tuple[dict | None, bool]:
    """(the geometry this run solved, is it certainly that geometry) - what
    the run's own "Open in FEMM" button opens.

    A job still resident in this session's JobRunner carries the exact
    CoilSet object that was submitted, so that one is always exact. A row read
    back from the database carries its own `coilset` unless it predates that
    column; for those the `runs` table has only the config's id and a hash
    of the geometry, so this rebuilds it
    from that config as it stands TODAY and re-derives the hash to check. A
    mismatch means the config was edited (or replaced) since the run - the
    geometry is still worth opening, but it is no longer what produced the
    numbers above it, and the caller says so rather than quietly showing the
    wrong model. (None, False) if the config was deleted outright.
    """
    job = job_runner.jobs.get(row["run_id"])
    if job is not None:
        return dataclasses.asdict(job.coilset), True
    if row.get("coilset"):  # the run stored its own geometry - always exact, needs no config
        return row["coilset"], True

    from engine.repositories.coilset_repository import get_coilset

    try:
        coilset = get_coilset(row["config_id"])
    except (KeyError, TypeError):
        return None, False
    exact = (isinstance(row.get("distance"), (int, float))
             and coilset_signature(coilset, row["distance"]) == row.get("coilset_hash"))
    return dataclasses.asdict(coilset), exact


def _draw_run_detail(row: dict, job_runner: JobRunner) -> None:
    imgui.text(f"Run {row['run_id']}")
    imgui.text(f"Config: {row['config_name']}    Distance: {_format_distance(row['distance'])}")
    imgui.text_disabled(_format_run_time(row))
    status = row.get("status", "")
    color = _STATUS_COLORS.get(status, imgui.ImVec4(1, 1, 1, 1))
    imgui.text_colored(color, f"Status: {status}")

    geometry, exact = run_geometry(row, job_runner)
    if geometry is None:
        imgui.text_disabled("Can't open in FEMM - this run's config no longer exists.")
    else:
        distance = row["distance"] if isinstance(row.get("distance"), (int, float)) else 0.0
        femm_open.button(f"run_{row['run_id']}_femm", geometry, distance,
                         f"{row['config_name']} run {row['run_id']}")
        if not exact:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_disabled(
                f"Note: {row['config_name']!r} has been edited since this run - the FEMM button opens "
                "that config as it is now, which is not the geometry these numbers came from.")
            imgui.pop_text_wrap_pos()

    if row.get("error"):
        imgui.text_colored(_ERROR_COLOR, row["error"])
        return

    result = row.get("result")
    if not result:
        imgui.text_disabled("(solving...)")
        return

    imgui.separator()
    draw_result_table(result)
