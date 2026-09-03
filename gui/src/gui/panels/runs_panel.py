"""New Run + Run History tabs: submit a simulation, watch it solve, browse past results.

Shares one RunsPanelState (and its JobRunner) across both tabs, since a run
submitted from "New Run" is exactly what shows up in "Run History".
"""

from datetime import datetime

from imgui_bundle import imgui

from ..job_runner import JobRunner, JobStatus, RESULTS_ROOT

_RESULT_FIELDS = (
    ("L_loop", "H"), ("L_tank", "H"), ("L_rc", "H"),
    ("M12", "H"), ("M13", "H"), ("M23", "H"),
    ("loop_esr", "Ohm"), ("tank_esr", "Ohm"), ("rc_esr", "Ohm"),
    ("k12", ""), ("k13", ""), ("k23", ""),
)

_STATUS_COLORS = {
    JobStatus.QUEUED.value: imgui.ImVec4(0.7, 0.7, 0.7, 1.0),
    JobStatus.RUNNING.value: imgui.ImVec4(0.9, 0.75, 0.2, 1.0),
    JobStatus.DONE.value: imgui.ImVec4(0.35, 0.8, 0.45, 1.0),
    JobStatus.FAILED.value: imgui.ImVec4(0.9, 0.35, 0.35, 1.0),
}
_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)


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
        self.selected_config_id: str | None = None
        self.distance: float = 5.0
        self.selected_run_id: str | None = None
        self.new_run_error: str = ""
        self.new_run_status: str = ""
        self.refresh_configs()

    def refresh_configs(self) -> None:
        from repositories.coilset_repository import list_configs

        self.configs = list_configs()
        if self.selected_config_id is None and self.configs:
            self.selected_config_id = self.configs[0]["id"]

    def submit_run(self) -> None:
        if not self.selected_config_id:
            self.new_run_error = "Select a config first."
            return
        from repositories.coilset_repository import get_coilset

        record = next((c for c in self.configs if c["id"] == self.selected_config_id), None)
        coilset = get_coilset(self.selected_config_id)

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
            coilset, config_id=self.selected_config_id,
            config_name=record["name"] if record else self.selected_config_id,
            distance=self.distance,
        )
        self.selected_run_id = job.id
        self.new_run_error = ""
        self.new_run_status = ""

    def history_rows(self) -> list[dict]:
        """Persisted results merged with any still-in-memory job (a running
        job overrides its own eventually-written file with fresher status)."""
        from repositories.run_result_repository import RunResultRepository

        rows = {r["run_id"]: r for r in RunResultRepository.list_results(root=RESULTS_ROOT)}
        for job_id, job in self.job_runner.jobs.items():
            rows[job_id] = job.as_dict()
        ordered = list(rows.values())
        ordered.sort(
            key=lambda r: r.get("last_used_at") or r.get("finished_at") or r.get("started_at") or r.get("created_at") or "",
            reverse=True,
        )
        return ordered


def draw_new_run(state: RunsPanelState) -> None:
    # refresh_configs() used to run only once, at RunsPanelState.__init__ -
    # so this dropdown never noticed a config created/renamed/deleted on the
    # Config Library tab for the rest of the session. Re-reading it here
    # every frame the tab is open keeps it in sync, the same way
    # draw_run_history() already re-reads history_rows() every frame.
    state.refresh_configs()

    if not state.configs:
        imgui.text_wrapped("No configs yet - create one on the Config Library tab first.")
        return

    names = [c["name"] for c in state.configs]
    ids = [c["id"] for c in state.configs]
    current_index = ids.index(state.selected_config_id) if state.selected_config_id in ids else 0

    imgui.set_next_item_width(320)
    changed, new_index = imgui.combo("Config", current_index, names)
    if changed:
        state.selected_config_id = ids[new_index]

    imgui.set_next_item_width(160)
    changed, new_distance = imgui.input_float("RC separation distance (mm)", state.distance, format="%.3f")
    if changed:
        state.distance = new_distance

    if imgui.button("Run"):
        state.submit_run()

    if state.new_run_error:
        imgui.text_colored(_ERROR_COLOR, state.new_run_error)
    elif state.new_run_status:
        imgui.text_colored(_STATUS_COLORS[JobStatus.DONE.value], state.new_run_status)

    if state.selected_run_id:
        row = next((r for r in state.history_rows() if r["run_id"] == state.selected_run_id), None)
        if row is not None:
            imgui.separator()
            imgui.text("Last submitted run:")
            _draw_run_detail(row)


def draw_run_history(state: RunsPanelState) -> None:
    rows = state.history_rows()

    imgui.begin_child("run_history_list", imgui.ImVec2(420, 0), imgui.ChildFlags_.borders.value)
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

        # Two real Text widgets drawn on top of the (label-less) selectable
        # above, so each line can have its own color/weight - a plain
        # single-line selectable label can't do that.
        imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + 8, row_top.y + 5))
        imgui.begin_group()
        imgui.text_colored(color, f"{row['config_name']}  @ {_format_distance(row['distance'])}  [{status}]")
        imgui.text_disabled(_format_run_time(row))
        imgui.end_group()

        # A real item (not just a bare cursor move) is required here so the
        # child window's content bounds actually grow to include the gap -
        # a lone SetCursorScreenPos with nothing following it asserts once
        # content is short enough that the child isn't already scrolling.
        imgui.set_cursor_screen_pos(row_top)
        imgui.dummy(imgui.ImVec2(row_width, row_height + 4))
    imgui.end_child()

    imgui.same_line()

    imgui.begin_child("run_history_detail", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders.value)
    selected_row = next((r for r in rows if r["run_id"] == state.selected_run_id), None)
    if selected_row is None:
        imgui.text_wrapped("Select a run on the left to see its result.")
    else:
        _draw_run_detail(selected_row)
    imgui.end_child()


def _draw_run_detail(row: dict) -> None:
    imgui.text(f"Run {row['run_id']}")
    imgui.text(f"Config: {row['config_name']}    Distance: {_format_distance(row['distance'])}")
    imgui.text_disabled(_format_run_time(row))
    status = row.get("status", "")
    color = _STATUS_COLORS.get(status, imgui.ImVec4(1, 1, 1, 1))
    imgui.text_colored(color, f"Status: {status}")

    if row.get("error"):
        imgui.text_colored(_ERROR_COLOR, row["error"])
        return

    result = row.get("result")
    if not result:
        imgui.text_disabled("(solving...)")
        return

    imgui.separator()
    if imgui.begin_table("result_table", 3, imgui.TableFlags_.borders.value):
        imgui.table_setup_column("Field")
        imgui.table_setup_column("Value")
        imgui.table_setup_column("Unit")
        imgui.table_headers_row()
        for name, unit in _RESULT_FIELDS:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(name)
            imgui.table_next_column()
            imgui.text(f"{result[name]:.6g}")
            imgui.table_next_column()
            imgui.text(unit)
        imgui.end_table()
