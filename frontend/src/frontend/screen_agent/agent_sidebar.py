"""The AI Agent screen's left column: every saved chat (agent/repositories/
ai_run_repository.py's ai_runs), whatever its status - one still being
planned, a cancelled one and an interrupted search included - with a search
box and New. Clicking a row opens that chat in the conversation view
(agent_panel.AgentPanelState.open_chat()); Delete is the only way a chat
leaves the list. Import brings in a chat exported to a file, as a new chat;
a row's right-click menu exports that chat to JSON or PDF
(files_backup/chat_io.py).

Also draws a stored run's results for the right column (draw_stored_run()):
for a finished run with a winner the same Search Summary the live panel
shows when a search ends (agent_candidate_card.draw_search_summary()), with
each round as the live feed's cards (agent_candidate_card.draw_round_block())
collapsed underneath - from the stored rows, plain dicts, never a live run
object.
"""

import dataclasses
import time
from pathlib import Path

from imgui_bundle import imgui, imspinner, portable_file_dialogs as pfd

from ..files_backup import chat_io
from . import agent_candidate_card

WIDTH = 260.0            # the same width as the Manual Runs config list
COLLAPSED_WIDTH = 34.0
MIN_WIDTH = 160.0        # how narrow the list can be dragged; narrower than this, collapse it instead
SPLITTER_WIDTH = 8.0     # the draggable gap between the list and the chat
_REFRESH_S = 1.0         # the list is re-read at most this often (and at once after a change made here)

_GOAL_MET_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_REJECTED_COLOR = imgui.ImVec4(0.9, 0.5, 0.35, 1.0)
_DEFAULT_COLOR = imgui.ImVec4(1.0, 1.0, 1.0, 1.0)
_ID_COLOR = imgui.ImVec4(0.55, 0.55, 0.6, 1.0)
_OPEN_COLOR = imgui.ImVec4(0.55, 0.7, 0.95, 1.0)
_MUTED_COLOR = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)
_DELETE_POPUP_ID = "Delete chat"  # also the popup's title bar

# Every ai_runs status has its own label: what happened must stay readable.
# Kept short - a sidebar row shows it beside the baseline name and the date.
STATUS_LABELS = {
    "interviewing": ("Planning", _OPEN_COLOR),
    "plan_ready": ("Plan ready", _OPEN_COLOR),
    "running": ("Searching", _OPEN_COLOR),
    "goal_met": ("Goal met", _GOAL_MET_COLOR),
    "budget_exhausted": ("Not achieved (budget)", _REJECTED_COLOR),
    "stopped_by_model": ("Not achieved (AI stopped)", _REJECTED_COLOR),
    "stalled": ("Not achieved (stalled)", _REJECTED_COLOR),
    "failed": ("Failed", _REJECTED_COLOR),
    "interrupted": ("Interrupted", _MUTED_COLOR),
    "cancelled": ("Cancelled", _MUTED_COLOR),
}


def status_label(status: str) -> tuple[str, imgui.ImVec4]:
    return STATUS_LABELS.get(status, (status, _MUTED_COLOR))


def _format_dt(iso: str, fmt: str = "%b %d, %Y - %H:%M UTC") -> str:
    """A real date/time, not the raw ISO-with-microseconds-and-offset the
    database stores (e.g. "2026-09-11T17:58:01.625093+00:00")."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except (TypeError, ValueError):
        return iso or ""


def chat_text(row: dict) -> str:
    """What a chat is about, in full: its plan's goal text, else the user's
    first message - stored text, on one line. "" when it has neither."""
    return " ".join((row.get("objective_text") or row.get("first_message") or "").split())


def chat_title(row: dict) -> str:
    """What a chat is called in the list: its short title
    (agent/services/chat_title.py) once it has one, else chat_text()."""
    return " ".join((row.get("title") or "").split()) or chat_text(row)


class AgentSidebarState:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        # agent.services.chat_title.ChatTitler, set by the app (main.py): asks
        # the AI for a short title for each chat that has none. None = no
        # titling, and no AI request ever leaves this list (tests).
        self.titler = None
        self.search_text: str = ""
        self.collapsed: bool = False
        self.open_width: float = WIDTH  # the list's width while not collapsed; the splitter changes it
        # The chat whose Delete is waiting on the confirmation popup.
        self.pending_delete_id: str | None = None
        self._open_delete_popup: bool = False
        self._refreshed_at: float | None = None
        # File dialogs in flight: a pfd.open_file() handle, and (pfd.save_file()
        # handle, run id, "json" or "pdf") - None while none is open.
        self._import_dialog = None
        self._export_dialog: tuple | None = None
        self.io_message: str = ""  # the last import/export, in one line
        self.io_error: str = ""
        self.refresh()

    def refresh(self) -> None:
        from agent.repositories.ai_run_repository import list_chat_rows
        self.rows = list_chat_rows()
        self._refreshed_at = time.monotonic()
        self._request_title()

    def _request_title(self) -> None:
        """Asks for one title: the newest chat that needs one. One at a time,
        and none while a search runs - a title takes the AI server most of a
        minute, and the search and the chat being typed in come first."""
        if self.titler is None or not self.titler.idle or any(row["status"] == "running" for row in self.rows):
            return
        for row in self.rows:
            # request() turns down short text and a chat that just failed; then the next one is tried.
            if not row.get("title") and self.titler.request(row["id"], chat_text(row)):
                return

    def title_of(self, run_id: str | None) -> str:
        """The listed title of this chat, "" if it isn't in the list."""
        return next((chat_title(row) for row in self.rows if row["id"] == run_id), "")

    def refresh_if_stale(self) -> None:
        if self._refreshed_at is None or time.monotonic() - self._refreshed_at >= _REFRESH_S:
            self.refresh()

    def visible_rows(self, config_names: dict | None = None) -> list[dict]:
        """The rows to list, newest first: every chat that has something to
        show (a row with neither a goal nor a message is a blank left by an
        older version), narrowed by search_text - matched against the title,
        the full text behind it, the baseline's name or id, and the run id."""
        config_names = config_names or {}
        query = self.search_text.strip().lower()
        rows = []
        for row in self.rows:
            title = chat_title(row)
            if not title:
                continue
            baseline = row["baseline_config_id"] or ""
            if query and not any(query in text.lower() for text in
                                 (title, chat_text(row), row["id"], baseline, config_names.get(baseline) or "")):
                continue
            rows.append(row)
        return rows

    def request_delete(self, run_id: str) -> None:
        self.pending_delete_id = run_id
        self._open_delete_popup = True

    def export(self, run_id: str, kind: str, path) -> None:
        """Writes one chat to `path` as "json" (complete) or "pdf" (a report)."""
        self.io_message = self.io_error = ""
        try:
            (chat_io.export_pdf if kind == "pdf" else chat_io.export_json)(run_id, path)
        except chat_io.ChatIOError as exc:
            self.io_error = str(exc)
            return
        self.io_message = f"Exported to {Path(path).name}"

    def import_from(self, path, panel) -> None:
        """Brings the chat file at `path` in as a new chat and opens it."""
        self.io_message = self.io_error = ""
        try:
            new_id = chat_io.import_json(path)
        except chat_io.ChatIOError as exc:
            self.io_error = str(exc)
            return
        self.io_message = f"Imported {Path(path).name}"
        self.refresh()
        panel.open_chat(new_id)

    def open_export_dialog(self, row: dict, kind: str) -> None:
        filters = ["PDF files", "*.pdf"] if kind == "pdf" else ["JSON files", "*.json"]
        dialog = pfd.save_file("Export chat", default_path=chat_io.default_name(row, kind), filters=filters)
        self._export_dialog = (dialog, row["id"], kind)

    def poll_dialogs(self, panel) -> None:
        if self._export_dialog is not None and self._export_dialog[0].ready():
            dialog, run_id, kind = self._export_dialog
            path = dialog.result()  # save_file() -> a single path string, "" if cancelled
            if path:
                self.export(run_id, kind, path)
            self._export_dialog = None
        if self._import_dialog is not None and self._import_dialog.ready():
            paths = self._import_dialog.result()  # open_file() -> a list of paths, [] if cancelled
            if paths:
                self.import_from(paths[0], panel)
            self._import_dialog = None


def width(state: AgentSidebarState) -> float:
    return COLLAPSED_WIDTH if state.collapsed else state.open_width


def resize(state: AgentSidebarState, delta: float, max_width: float) -> None:
    """Drags the list's right edge by `delta` pixels, kept between MIN_WIDTH
    and `max_width` (what the chat next to it can give up)."""
    if not state.collapsed:
        state.open_width = max(MIN_WIDTH, min(state.open_width + delta, max(max_width, MIN_WIDTH)))


def draw_splitter(state: AgentSidebarState, max_width: float) -> None:
    """The gap between the list and the chat, as a handle: dragging it makes
    the list narrower and the chat wider, or the other way round. Call between
    the two with same_line(0, 0) on both sides."""
    height = max(imgui.get_content_region_avail().y, 1.0)
    pos = imgui.get_cursor_screen_pos()
    imgui.invisible_button("##sidebar_splitter", imgui.ImVec2(SPLITTER_WIDTH, height))
    if state.collapsed:
        return
    hovered, active = imgui.is_item_hovered(), imgui.is_item_active()
    if hovered or active:
        imgui.set_mouse_cursor(imgui.MouseCursor_.resize_ew.value)
        color = imgui.get_color_u32((imgui.Col_.separator_active if active else imgui.Col_.separator_hovered).value)
        x = pos.x + SPLITTER_WIDTH / 2.0
        imgui.get_window_draw_list().add_line(imgui.ImVec2(x, pos.y), imgui.ImVec2(x, pos.y + height), color, 2.0)
    if active:
        resize(state, imgui.get_io().mouse_delta.x, max_width)


def _draw_row(state: AgentSidebarState, panel, row: dict, config_names: dict, row_width: float) -> None:
    line_h = imgui.get_text_line_height_with_spacing()
    row_height = line_h * 2 + 8
    label, color = status_label(row["status"])
    baseline = config_names.get(row["baseline_config_id"]) or row["baseline_config_id"] or "?"

    row_top = imgui.get_cursor_screen_pos()
    clicked, _ = imgui.selectable(f"##chat_{row['id']}", row["id"] == panel.chat.ai_run_id, 0,
                                  imgui.ImVec2(row_width, row_height))
    if clicked:
        panel.open_chat(row["id"])
    if imgui.is_item_hovered():
        # The full goal text, wrapped - the row itself shows only the short title.
        imgui.begin_tooltip()
        imgui.push_text_wrap_pos(imgui.get_font_size() * 30)
        imgui.text(chat_text(row))
        imgui.pop_text_wrap_pos()
        imgui.text(f"{baseline} - {label}\n{_format_dt(row['created_at'])}\n"
                   f"ID: {row['id']}\nRight-click to export or delete")
        imgui.end_tooltip()
    if imgui.begin_popup_context_item(f"chat_menu_{row['id']}"):
        imgui.begin_disabled(row["status"] == "running")  # a running search's rows are still changing
        if imgui.menu_item_simple("Export JSON..."):
            state.open_export_dialog(row, "json")
        if imgui.menu_item_simple("Export PDF report..."):
            state.open_export_dialog(row, "pdf")
        imgui.separator()
        if imgui.menu_item_simple("Delete..."):
            state.request_delete(row["id"])
        imgui.end_disabled()
        imgui.end_popup()

    # Line 1: the title. Line 2: status and baseline on the left, the date
    # kept whole on the right. Text is cut off at its edge instead of
    # wrapping or running under the date or the scrollbar.
    right = row_top.x + row_width - 6
    date = _format_dt(row["created_at"], "%b %d")
    date_x = right - imgui.calc_text_size(date).x
    line2_y = row_top.y + 4 + line_h
    imgui.push_clip_rect(row_top, imgui.ImVec2(right, row_top.y + row_height), True)
    imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + 8, row_top.y + 4))
    imgui.text_colored(_DEFAULT_COLOR, chat_title(row))
    imgui.set_cursor_screen_pos(imgui.ImVec2(date_x, line2_y))
    imgui.text_colored(_MUTED_COLOR, date)
    imgui.pop_clip_rect()
    imgui.push_clip_rect(imgui.ImVec2(row_top.x, line2_y), imgui.ImVec2(date_x - 8, row_top.y + row_height), True)
    imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + 8, line2_y))
    if row["status"] == "running":
        imspinner.spinner_ang(f"##chat_spinner_{row['id']}", 5.0, 2.0)
        imgui.same_line()
    imgui.text_colored(color, ("Imported - " if row.get("imported_at") else "") + f"{label} - {baseline}")
    imgui.pop_clip_rect()

    # A real item (not just a bare cursor move) is required here so the
    # child window's content bounds actually grow to include the gap -
    # the same fix runs_panel.draw_history()'s row loop needed.
    imgui.set_cursor_screen_pos(row_top)
    imgui.dummy(imgui.ImVec2(row_width, row_height + 4))


def _draw_delete_confirmation(state: AgentSidebarState, panel) -> None:
    """Delete, behind a confirmation - the only way a chat leaves the list."""
    if state._open_delete_popup:
        imgui.open_popup(_DELETE_POPUP_ID)
        state._open_delete_popup = False
    # begin_popup_modal() returns a tuple - unpack it (see configs_panel.py).
    is_open, _ = imgui.begin_popup_modal(_DELETE_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if not is_open:
        return
    imgui.text("Delete this chat, its plan and every search round?")
    imgui.text_disabled("This can't be undone. FEMM solves stay in the run cache.")
    imgui.separator()
    if imgui.button("Cancel##del") or imgui.is_key_pressed(imgui.Key.escape):
        state.pending_delete_id = None
        imgui.close_current_popup()
    imgui.same_line()
    imgui.push_style_color(imgui.Col_.button.value, _REJECTED_COLOR)
    if imgui.button("Delete##confirm_del"):
        if state.pending_delete_id is not None:
            panel.delete_chat(state.pending_delete_id)
        state.pending_delete_id = None
        imgui.close_current_popup()
    imgui.pop_style_color()
    imgui.end_popup()


def draw(state: AgentSidebarState, panel, size: imgui.ImVec2) -> None:
    """`panel` is agent_panel's AgentPanelState: the sidebar calls its
    new_chat() / open_chat() / delete_chat() and reads which chat is open."""
    state.refresh_if_stale()
    state.poll_dialogs(panel)
    imgui.begin_child("agent_sidebar_pane", size, imgui.ChildFlags_.borders.value)
    if state.collapsed:
        if imgui.button(">##expand_sidebar"):
            state.collapsed = False
        if imgui.is_item_hovered():
            imgui.set_tooltip("Show past chats")
        _draw_delete_confirmation(state, panel)
        imgui.end_child()
        return

    collapse_w = imgui.get_frame_height()
    if imgui.button("+ New", imgui.ImVec2(imgui.get_content_region_avail().x - collapse_w - imgui.get_style().item_spacing.x, 0)):
        panel.new_chat()
    if imgui.is_item_hovered():
        imgui.set_tooltip("Start a new chat on the selected baseline. This one stays in the list.")
    imgui.same_line()
    if imgui.button("<##collapse_sidebar", imgui.ImVec2(collapse_w, 0)):
        state.collapsed = True
    if imgui.is_item_hovered():
        imgui.set_tooltip("Hide past chats")
    if imgui.button("Import...", imgui.ImVec2(imgui.get_content_region_avail().x, 0)):
        state._import_dialog = pfd.open_file("Import chat", filters=["JSON files", "*.json"])
    if imgui.is_item_hovered():
        imgui.set_tooltip("Open a chat exported to a JSON file, as a new chat.\n"
                          "To export one, right-click it in the list.")
    for text, color in ((state.io_error, _REJECTED_COLOR), (state.io_message, _MUTED_COLOR)):
        if text:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(color, text)
            imgui.pop_text_wrap_pos()
    imgui.set_next_item_width(imgui.get_content_region_avail().x)
    changed, text = imgui.input_text_with_hint("##chat_search", "Search chats...", state.search_text)
    if changed:
        state.search_text = text
    imgui.separator()

    config_names = {c["id"]: c["name"] for c in panel.configs}
    imgui.begin_child("agent_sidebar_list", imgui.ImVec2(0, 0), 0)
    row_width = imgui.get_content_region_avail().x
    rows = state.visible_rows(config_names)
    if not rows:
        imgui.text_disabled("No chats match." if state.search_text.strip() else "No chats yet.")
    for row in rows:
        _draw_row(state, panel, row, config_names, row_width)
    imgui.end_child()

    _draw_delete_confirmation(state, panel)
    imgui.end_child()


# --- a stored run's results, for the right column --------------------------------------------------

def _normalize_spec(spec: dict | None) -> dict:
    """Runs saved before AgentSpec gained primary_metrics stored a single
    primary_metric/m_tolerance_pct pair instead - map that onto the
    primary_metrics shape agent_candidate_card's legacy rows read, so older
    history still renders a requirement column. (Runs saved since the
    objective list carry their requirements in each tool_result - no spec
    mapping needed.)"""
    spec = dict(spec or {})
    if "primary_metrics" not in spec and "primary_metric" in spec:
        spec["primary_metrics"] = {spec["primary_metric"]: spec.get("m_tolerance_pct", 0.0)}
    return spec


def _coilsets(record: dict):
    """The real baseline/best CoilSet objects, rebuilt from what the run
    stored (the baseline copied into it at Start Search, else its config) -
    used for the structural audit and for looking up each one's full solve
    in the shared cache. (None, None) if the baseline is gone, or the saved
    geometry no longer fits today's CoilSet (e.g. a field added since)."""
    from agent.femm_agent.services.restore import RestoreError, stored_baseline
    from engine.models.coilset import CoilSet
    try:
        baseline = stored_baseline(record)
        best = CoilSet(**record["best_coilset"]) if record.get("best_coilset") else None
        return baseline, best
    except (RestoreError, TypeError):
        return None, None


def _audit(record: dict, baseline, best) -> dict | None:
    """The live summary's structural check, rebuilt from what the run
    stored. None (plain diff, no check line) if the saved spec doesn't fit
    today's VariableSpec, or either coilset couldn't be rebuilt."""
    from agent.input_chat.models.variables import audit_changed_fields, stored_allowed_fields
    if baseline is None or best is None:
        return None
    try:
        # The run's own copy of the Field Config table, or - for a run stored
        # before the table existed - the rule in force then.
        return audit_changed_fields(baseline, best, stored_allowed_fields(baseline, record["spec"] or {}))
    except (KeyError, TypeError, AttributeError):
        return None


def _total_timing_text(optimize_messages: list[dict]) -> str:
    llm_s = sum((m["payload"] or {}).get("llm_seconds") or 0 for m in optimize_messages)
    femm_s = sum((m["payload"] or {}).get("femm_seconds") or 0 for m in optimize_messages)
    return f"Time: AI {llm_s:.0f}s, FEMM {femm_s:.0f}s" if llm_s or femm_s else ""


def _timing_text(payload: dict) -> str:
    llm_s, femm_s = payload.get("llm_seconds"), payload.get("femm_seconds")
    if llm_s is None:
        return ""
    return f"[AI: {llm_s:.0f}s, FEMM: {femm_s:.0f}s]" if femm_s is not None else f"[AI: {llm_s:.0f}s]"


def stored_rounds(messages: list[dict]) -> list[dict]:
    """A stored run's search rounds (its optimize-phase messages), in order."""
    return sorted((m for m in messages if m["phase"] == "optimize"), key=lambda m: m["round_num"])


def has_stored_summary(record: dict, messages: list[dict]) -> bool:
    """A finished run with a winner replays as the Search Summary; one
    without (failed or cut off) keeps the plain round list - those rounds
    are what explain why."""
    return bool(record["status"] not in ("running", "interviewing", "plan_ready")
                and record["best_coilset"] and record["best_tool_result"] and stored_rounds(messages))


def draw_stored_run(record: dict, messages: list[dict], summary_distance: float | None,
                    size: imgui.ImVec2) -> float | None:
    """A reopened chat's search, from its stored rows. Returns which distance
    the summary's Result table is showing (the caller keeps it per chat)."""
    imgui.begin_child("agent_stored_run_pane", size, imgui.ChildFlags_.borders.value)
    spec = _normalize_spec(record["spec"])
    baseline_coilset, best_coilset = _coilsets(record)
    # No baseline any more: the input table shows "?" for its old values.
    baseline_buffer = dataclasses.asdict(baseline_coilset) if baseline_coilset is not None else {}
    distances = spec.get("distances") or [5.0]
    # Round-by-round detail below still uses the first distance - see
    # agent_panel.py's _draw_summary() for why that stays separate from the
    # summary's own distance dropdown.
    round_distance = distances[0]
    optimize = stored_rounds(messages)

    imgui.text_colored(_ID_COLOR, f"ID: {record['id']}")
    imgui.same_line()
    imgui.text_disabled(_format_dt(record["created_at"]))
    label, color = status_label(record["status"])
    imgui.text_colored(color, f"Status: {label}" + (f" - ended by: {record['stop_reason']}" if record.get("stop_reason") else ""))
    if record.get("imported_at"):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_MUTED_COLOR, f"Imported {_format_dt(record['imported_at'])}. These results come from the "
                                         "file and were not simulated on this machine.")
        imgui.pop_text_wrap_pos()

    has_summary = has_stored_summary(record, messages)
    if has_summary:
        imgui.spacing()
        summary_distance = agent_candidate_card.draw_search_summary(
            f"stored_{record['id']}_summary", record["status"],
            [(m["round_num"], (m["payload"] or {}).get("candidates") or []) for m in optimize],
            _total_timing_text(optimize), baseline_buffer, record["best_coilset"], record["best_tool_result"],
            spec, distances, summary_distance, _audit(record, baseline_coilset, best_coilset),
            baseline_coilset=baseline_coilset, best_coilset=best_coilset, report=record["error"],
        )
        imgui.spacing()
        show_rounds = imgui.tree_node_ex(f"Round-by-round details##stored_{record['id']}_rounds", 0)
    else:
        show_rounds = True

    if show_rounds:
        for m in optimize:
            payload = m["payload"] or {}
            attempt = payload.get("search_attempt") or 1
            header = f"Round {m['round_num']}" + (
                f" - restart {attempt - 1}, round {payload.get('attempt_round')} of the new attempt" if attempt > 1 else "")
            agent_candidate_card.draw_round_block(
                f"stored_{record['id']}_r{m['round_num']}", header,
                _timing_text(payload), m["content"] or "", payload.get("candidates") or [],
                baseline_buffer, spec, round_distance, failures=payload.get("llm_failures"),
            )
        if has_summary:
            imgui.tree_pop()

    if not has_summary:
        if not optimize:
            imgui.text_disabled("No search round was saved for this run.")
        if record["error"]:
            agent_candidate_card.draw_outcome_report(record["error"])
    imgui.end_child()
    return summary_distance
