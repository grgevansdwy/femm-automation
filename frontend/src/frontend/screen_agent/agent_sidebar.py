"""The AI Agent screen's left column: every saved chat (agent/repositories/
ai_run_repository.py's ai_runs), whatever its status - one still being
planned, a cancelled one and an interrupted search included - with a search
box and New. Clicking a row opens that chat in the conversation view
(agent_panel.AgentPanelState.open_chat()); Delete is the only way a chat
leaves the list.

Also draws a stored run's results for the right column (draw_stored_run()):
for a finished run with a winner the same Search Summary the live panel
shows when a search ends (agent_candidate_card.draw_search_summary()), with
each round as the live feed's cards (agent_candidate_card.draw_round_block())
collapsed underneath - from the stored rows, plain dicts, never a live run
object.
"""

import dataclasses
import time

from imgui_bundle import imgui, imspinner

from . import agent_candidate_card

WIDTH = 260.0            # the same width as the Manual Runs config list
COLLAPSED_WIDTH = 34.0
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


def chat_title(row: dict) -> str:
    """What a chat is called in the list: its plan's goal text, else the
    user's first message - stored text, on one line. "" when it has neither."""
    return " ".join((row.get("objective_text") or row.get("first_message") or "").split())


class AgentSidebarState:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.search_text: str = ""
        self.collapsed: bool = False
        # The chat whose Delete is waiting on the confirmation popup.
        self.pending_delete_id: str | None = None
        self._open_delete_popup: bool = False
        self._refreshed_at: float | None = None
        self.refresh()

    def refresh(self) -> None:
        from agent.repositories.ai_run_repository import list_chat_rows
        self.rows = list_chat_rows()
        self._refreshed_at = time.monotonic()

    def refresh_if_stale(self) -> None:
        if self._refreshed_at is None or time.monotonic() - self._refreshed_at >= _REFRESH_S:
            self.refresh()

    def visible_rows(self, config_names: dict | None = None) -> list[dict]:
        """The rows to list, newest first: every chat that has something to
        show (a row with neither a goal nor a message is a blank left by an
        older version), narrowed by search_text - matched against the title,
        the baseline's name or id, and the run id."""
        config_names = config_names or {}
        query = self.search_text.strip().lower()
        rows = []
        for row in self.rows:
            title = chat_title(row)
            if not title:
                continue
            baseline = row["baseline_config_id"] or ""
            if query and not any(query in text.lower() for text in
                                 (title, row["id"], baseline, config_names.get(baseline) or "")):
                continue
            rows.append(row)
        return rows

    def request_delete(self, run_id: str) -> None:
        self.pending_delete_id = run_id
        self._open_delete_popup = True


def width(state: AgentSidebarState) -> float:
    return COLLAPSED_WIDTH if state.collapsed else WIDTH


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
        imgui.set_tooltip(f"{chat_title(row)}\n{baseline} - {label}\n{_format_dt(row['created_at'])}\n"
                          f"ID: {row['id']}\nRight-click to delete")
    if imgui.begin_popup_context_item(f"chat_menu_{row['id']}"):
        imgui.begin_disabled(row["status"] == "running")
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
    imgui.text_colored(color, f"{label} - {baseline}")
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
        # The run's own copy of the Search fields table, or - for a run stored
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
