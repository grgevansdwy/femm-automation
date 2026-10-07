"""AI-run history browser - lists every persisted ai_run (agent/repositories/
ai_run_repository.py), whatever its status - a chat still being planned, a
cancelled one and an interrupted search included - and, on selection,
replays the run: the interview as
a short collapsible transcript, then - for a finished run with a winner -
the same Search Summary the live panel shows when a search ends
(agent_candidate_card.draw_search_summary()), with each optimize-phase round
as the live feed's geometry + input/output table cards
(agent_candidate_card.draw_round_block()) collapsed underneath. Mirrors runs_panel.draw_history()'s
list+detail split for the plain-solve `runs` table; reached via
agent_panel.py's "History" toggle rather than a separate always-visible panel.
"""

from imgui_bundle import imgui

from . import agent_candidate_card

_GOAL_MET_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_REJECTED_COLOR = imgui.ImVec4(0.9, 0.5, 0.35, 1.0)
_ASSISTANT_COLOR = imgui.ImVec4(0.7, 0.7, 0.7, 1.0)
_DEFAULT_COLOR = imgui.ImVec4(1.0, 1.0, 1.0, 1.0)
_ID_COLOR = imgui.ImVec4(0.55, 0.55, 0.6, 1.0)
_OPEN_COLOR = imgui.ImVec4(0.55, 0.7, 0.95, 1.0)
_MUTED_COLOR = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)
_DELETE_POPUP_ID = "confirm_delete_ai_run"

# Every ai_runs status has its own label: what happened must stay readable.
STATUS_LABELS = {
    "interviewing": ("Planning", _OPEN_COLOR),
    "plan_ready": ("Plan ready, not started", _OPEN_COLOR),
    "running": ("Searching", _OPEN_COLOR),
    "goal_met": ("Goal met", _GOAL_MET_COLOR),
    "budget_exhausted": ("Target not achieved (budget)", _REJECTED_COLOR),
    "stopped_by_model": ("Target not achieved (AI stopped)", _REJECTED_COLOR),
    "stalled": ("Target not achieved (stalled)", _REJECTED_COLOR),
    "failed": ("Failed", _REJECTED_COLOR),
    "interrupted": ("Interrupted", _MUTED_COLOR),
    "cancelled": ("Cancelled", _MUTED_COLOR),
}


def _status_label(status: str) -> tuple[str, imgui.ImVec4]:
    return STATUS_LABELS.get(status, (status, _MUTED_COLOR))


def _format_dt(iso: str) -> str:
    """A real date/time, not the raw ISO-with-microseconds-and-offset the
    database stores (e.g. "2026-09-11T17:58:01.625093+00:00")."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y - %H:%M UTC")
    except (TypeError, ValueError):
        return iso or ""


class AgentHistoryState:
    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.selected_id: str | None = None
        self.search_text: str = ""
        self.sort_newest_first: bool = True
        # Which distance the selected run's Search Summary Result table is
        # showing - reset to None (falls back to distances[0]) whenever a
        # different run is selected, in _draw_list()'s click handler.
        self.summary_distance: float | None = None
        self.refresh()

    def refresh(self) -> None:
        from agent.repositories.ai_run_repository import list_ai_runs
        self.runs = list_ai_runs()

    def delete(self, run_id: str) -> None:
        """The explicit user action that removes a run and its messages. A
        search still running is never deleted from here (stop it first)."""
        from agent.repositories.ai_run_repository import delete_ai_run, get_ai_run
        record = get_ai_run(run_id)
        if record is None or record["status"] == "running":
            return
        delete_ai_run(run_id)
        if self.selected_id == run_id:
            self.selected_id = None
        self.refresh()

    def visible_runs(self) -> list[dict]:
        """self.runs filtered by search_text (matches the run id or the
        baseline config name/id - a real user's two handles on a run) and
        sorted by created_at in the chosen direction."""
        rows = self.runs
        query = self.search_text.strip().lower()
        if query:
            rows = [r for r in rows if query in r["id"].lower() or query in (r["baseline_config_id"] or "").lower()]
        return sorted(rows, key=lambda r: r["created_at"] or "", reverse=self.sort_newest_first)


def _draw_toolbar(state: AgentHistoryState) -> None:
    """Search-by-id-or-coilset-name + a newest/oldest-first sort toggle,
    filtering state.runs down to visible_runs() for both the list and (via
    fewer candidates to scroll) the search itself."""
    sort_label = "Newest first" if state.sort_newest_first else "Oldest first"
    sort_w = imgui.calc_text_size(sort_label).x + 24.0
    imgui.set_next_item_width(max(imgui.get_content_region_avail().x - sort_w - 8.0, 80.0))
    changed, text = imgui.input_text_with_hint("##hist_search", "Search by run ID or coilset name...", state.search_text)
    if changed:
        state.search_text = text
    imgui.same_line()
    if imgui.button(sort_label, imgui.ImVec2(sort_w, 0)):
        state.sort_newest_first = not state.sort_newest_first


def _draw_list(state: AgentHistoryState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_history_list", size, imgui.ChildFlags_.borders.value)
    row_height = imgui.get_text_line_height_with_spacing() * 3 + 8
    row_width = imgui.get_content_region_avail().x
    runs = state.visible_runs()
    if not runs:
        imgui.text_disabled("No runs match." if state.search_text.strip() else "No runs yet.")
    for run in runs:
        selected = run["id"] == state.selected_id
        label, color = _status_label(run["status"])

        row_top = imgui.get_cursor_screen_pos()
        clicked, _ = imgui.selectable(f"##hist_{run['id']}", selected, 0, imgui.ImVec2(row_width, row_height))
        if clicked:
            state.selected_id = run["id"]
            state.summary_distance = None  # a different run may not even have this distance

        imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + 8, row_top.y + 4))
        imgui.begin_group()
        imgui.text_colored(color, f"{run['baseline_config_id']}  [{label}]")
        imgui.text_colored(_ID_COLOR, f"ID: {run['id']}")
        imgui.text_disabled(_format_dt(run["created_at"]))
        imgui.end_group()

        # A real item (not just a bare cursor move) is required here so the
        # child window's content bounds actually grow to include the gap -
        # the same fix runs_panel.draw_history()'s row loop needed.
        imgui.set_cursor_screen_pos(row_top)
        imgui.dummy(imgui.ImVec2(row_width, row_height + 4))
    imgui.end_child()


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


def _baseline_buffer(baseline_config_id: str) -> dict:
    import dataclasses
    from engine.repositories.coilset_repository import get_coilset
    try:
        return dataclasses.asdict(get_coilset(baseline_config_id))
    except KeyError:
        return {}  # baseline config since deleted - input table shows "?" for its old values


def _coilsets(record: dict, baseline_config_id: str):
    """The real baseline/best CoilSet objects, rebuilt from what the run
    stored - used for the structural audit and for looking up each one's
    full solve in the shared cache. (None, None) if the baseline config was
    since deleted, or the saved geometry no longer fits today's CoilSet
    (e.g. a field added since)."""
    from engine.models.coilset import CoilSet
    from engine.repositories.coilset_repository import get_coilset
    try:
        baseline = get_coilset(baseline_config_id)
        best = CoilSet(**record["best_coilset"]) if record.get("best_coilset") else None
        return baseline, best
    except (KeyError, TypeError):
        return None, None


def _audit(record: dict, baseline, best) -> dict | None:
    """The live summary's structural check, rebuilt from what the run
    stored. None (plain diff, no check line) if the saved spec doesn't fit
    today's VariableSpec, or either coilset couldn't be rebuilt."""
    from agent.input_chat.models.variables import VariableSpec, allowed_fields, audit_changed_fields
    if baseline is None or best is None:
        return None
    try:
        raw = dict((record["spec"] or {}).get("variable_spec") or {})
        for key in ("enabled_groups", "excluded_fields"):
            raw[key] = set(raw.get(key) or [])
        return audit_changed_fields(baseline, best, allowed_fields(baseline, VariableSpec(**raw)))
    except (KeyError, TypeError):
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


def _draw_detail(state: AgentHistoryState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_history_detail", size, imgui.ChildFlags_.borders.value)
    if state.selected_id is None:
        imgui.text_wrapped("Select a run above to replay its transcript.")
        imgui.end_child()
        return

    from agent.repositories.ai_run_repository import get_ai_run, list_messages
    record = get_ai_run(state.selected_id)
    if record is None:
        imgui.text_disabled("This run no longer exists.")
        imgui.end_child()
        return
    spec = _normalize_spec(record["spec"])
    baseline_buffer = _baseline_buffer(record["baseline_config_id"])
    distances = spec.get("distances") or [5.0]
    # Round-by-round detail below still uses the first distance - see
    # agent_panel.py's _draw_summary() for why that stays separate from the
    # summary's own distance dropdown.
    round_distance = distances[0]
    messages = list_messages(state.selected_id)

    imgui.text_colored(_ID_COLOR, f"ID: {record['id']}")
    imgui.text_disabled(_format_dt(record["created_at"]))
    label, color = _status_label(record["status"])
    imgui.text_colored(color, f"Status: {label}" + (f" - ended by: {record['stop_reason']}" if record.get("stop_reason") else ""))
    _draw_delete(state, record)

    # The conversation stays a short text transcript; the search rounds get
    # the same geometry + table cards the live Search Progress feed shows.
    interview = [m for m in messages if m["phase"] == "interview" and m["role"] in ("user", "assistant") and m["content"]]
    if interview and imgui.collapsing_header("Conversation##hist_convo"):
        for m in interview:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_DEFAULT_COLOR if m["role"] == "user" else _ASSISTANT_COLOR,
                                f"{m['role']}: {m['content']}")
            imgui.pop_text_wrap_pos()

    if spec.get("objective_text"):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_wrapped(f"Goal: {spec['objective_text']}")
        imgui.pop_text_wrap_pos()

    optimize = [m for m in messages if m["phase"] == "optimize"]
    # A finished run with a winner replays as the same Search Summary the
    # live panel showed when it ended; the per-round cards stay available,
    # collapsed, underneath. Runs without one (failed/cut off) keep the
    # plain round list - those rounds are what explain why.
    has_summary = (record["status"] not in ("running", "interviewing", "plan_ready")
                   and record["best_coilset"] and record["best_tool_result"] and optimize)
    if has_summary:
        imgui.spacing()
        baseline_coilset, best_coilset = _coilsets(record, record["baseline_config_id"])
        state.summary_distance = agent_candidate_card.draw_search_summary(
            f"hist_{state.selected_id}_summary", record["status"],
            [(m["round_num"], (m["payload"] or {}).get("candidates") or []) for m in optimize],
            _total_timing_text(optimize), baseline_buffer, record["best_coilset"], record["best_tool_result"],
            spec, distances, state.summary_distance, _audit(record, baseline_coilset, best_coilset),
            baseline_coilset=baseline_coilset, best_coilset=best_coilset, report=record["error"],
        )
        imgui.spacing()
        show_rounds = imgui.tree_node_ex(f"Round-by-round details##hist_{state.selected_id}_rounds", 0)
    else:
        show_rounds = True

    if show_rounds:
        for m in optimize:
            payload = m["payload"] or {}
            attempt = payload.get("search_attempt") or 1
            header = f"Round {m['round_num']}" + (
                f" - restart {attempt - 1}, round {payload.get('attempt_round')} of the new attempt" if attempt > 1 else "")
            agent_candidate_card.draw_round_block(
                f"hist_{state.selected_id}_r{m['round_num']}", header,
                _timing_text(payload), m["content"] or "", payload.get("candidates") or [],
                baseline_buffer, spec, round_distance, failures=payload.get("llm_failures"),
            )
        if has_summary:
            imgui.tree_pop()

    if not has_summary:
        if record["error"]:
            agent_candidate_card.draw_outcome_report(record["error"])
    imgui.end_child()


def _draw_delete(state: AgentHistoryState, record: dict) -> None:
    """Delete, behind a confirmation - the only way a run leaves History."""
    imgui.begin_disabled(record["status"] == "running")
    if imgui.small_button(f"Delete##del_{record['id']}"):
        imgui.open_popup(_DELETE_POPUP_ID)
    imgui.end_disabled()
    is_open, _ = imgui.begin_popup_modal(_DELETE_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if is_open:
        imgui.text("Delete this run and its whole conversation?")
        imgui.text_disabled("This can't be undone. FEMM solves stay in the run cache.")
        imgui.separator()
        if imgui.button("Cancel##del"):
            imgui.close_current_popup()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.button.value, _REJECTED_COLOR)
        if imgui.button("Delete##confirm_del"):
            state.delete(record["id"])
            imgui.close_current_popup()
        imgui.pop_style_color()
        imgui.end_popup()


def draw(state: AgentHistoryState, size: imgui.ImVec2) -> None:
    state.refresh()
    imgui.begin_child("agent_history_pane", size, imgui.ChildFlags_.borders.value)
    imgui.text("AI Run History")
    imgui.separator()
    _draw_toolbar(state)
    imgui.spacing()

    avail = imgui.get_content_region_avail()
    list_h = min(200.0, avail.y * 0.45) if avail.y > 0 else 200.0
    _draw_list(state, imgui.ImVec2(0, list_h))
    _draw_detail(state, imgui.ImVec2(0, max(avail.y - list_h - 8.0, 60.0)))
    imgui.end_child()
