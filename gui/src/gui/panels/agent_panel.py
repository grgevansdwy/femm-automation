"""AI Agent mode: an interview chat (left) that gathers an objective, then
a live search-progress feed (right) once the interview hands off to the
optimize phase - the toggle-mode counterpart to configs_panel/runs_panel's
manual-run dashboard (see main.py's toolbar toggle, _draw_agent_mode()).

Reuses coil_diagram.draw() verbatim for the live baseline view and the
finished-search summary's best-vs-baseline comparison, and
agent_candidate_card.draw_round_block() for the round-by-round search feed
(geometry + input/output tables + short reasoning per candidate - shared
with agent_history.py's replay) while the search runs. Only ever
calls into coil_simulation/agent's entry points (InterviewRunner,
AgentRunner) - never reimplements orchestration here.
"""

import dataclasses
import json
import time

from imgui_bundle import imgui, imspinner

from .. import field_layout
from ..job_runner import now
from . import agent_candidate_card, agent_history, coil_diagram, runs_panel

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
# The baseline's own solve (and its diagram) - before the interview there's
# no chosen distance yet, so the manual-run default.
BASELINE_DISTANCE = 5.0


class AgentPanelState:
    def __init__(self, job_runner) -> None:
        from agent.gui_bridge import InterviewRunner
        from agent.runner import AgentRunner

        self.job_runner = job_runner
        self.interview_runner = InterviewRunner()
        self.agent_runner = AgentRunner(job_runner)
        self.history = agent_history.AgentHistoryState()

        self.configs: list[dict] = []
        self.selected_config_id: str | None = None
        self.baseline_coilset = None  # the picked config, read once at selection
        # distance (mm) -> its own solve at that distance: a cached result
        # row (dict, already done) or a live JobRunner Job still solving.
        # Starts as {BASELINE_DISTANCE: ...} the moment a config is picked
        # (before any objective is known); _sync_baseline_distances() then
        # replaces it with exactly spec.distances the moment a plan is
        # finalized - dropping BASELINE_DISTANCE if it wasn't asked for.
        self.baseline_runs: dict = {}
        self._synced_distances: tuple | None = None  # poll()'s guard against resolving every frame
        self.interview_state = None  # agent.interview.InterviewState, created the moment a config is picked
        self.run_state = None        # agent.state.AgentRunState, once the plan is confirmed
        # The model calling finalize_spec proposes a plan - it does NOT
        # start the search. plan_confirmed is the explicit human go-ahead
        # (the "Start Search" button on the rendered plan); poll() only
        # hands off to AgentRunner once both are true.
        self.plan_confirmed: bool = False
        self.chat_input: str = ""
        self.chat_input_active: bool = False
        self.error: str = ""
        self.showing_history: bool = False
        # Which distance the finished-search Search Summary's Result table
        # is showing - a dropdown lets the user switch (draw_search_summary()
        # falls back to spec.distances[0] whenever this isn't one of them,
        # e.g. still None on the very first draw).
        self.summary_distance: float | None = None
        self.refresh_configs()

    def poll(self) -> None:
        """Called once per frame from main.py, alongside job_runner.poll()."""
        self.interview_runner.poll()
        self.agent_runner.poll()
        if self.interview_state is not None and self.interview_state.finalized:
            # Re-sync only on an actual change of distances (comparing a
            # tuple, not calling sync unconditionally) - _solve_one() would
            # otherwise re-submit() a brand new duplicate job every single
            # frame while a solve is still in flight (find_cached() can't
            # see it yet - it isn't persisted until the job finishes).
            distances = tuple(self.interview_state.spec.distances)
            if distances != self._synced_distances:
                self._sync_baseline_distances(distances)
                self._synced_distances = distances
        if (self.interview_state is not None and self.interview_state.finalized
                and self.plan_confirmed and self.run_state is None):
            self.run_state = self.agent_runner.submit(
                self.interview_state.spec, ai_run_id=self.interview_runner.ai_run_id,
            )

    def refresh_configs(self) -> None:
        from repositories.coilset_repository import list_configs
        self.configs = list_configs()

    def select_config(self, config_id: str) -> None:
        """Picking a baseline immediately opens an empty chat, ready to
        type into - no separate "Start" click, no opening LLM call (see
        InterviewRunner.start())."""
        from repositories.coilset_repository import get_coilset

        self.selected_config_id = config_id
        self.run_state = None
        self.plan_confirmed = False
        self.error = ""
        self.baseline_coilset = get_coilset(config_id)
        # No objective (hence no distances) is known yet - a single solve at
        # the manual-run default, replaced by _sync_baseline_distances() the
        # moment a plan is finalized with the user's real distances.
        self.baseline_runs = {BASELINE_DISTANCE: self._solve_one(BASELINE_DISTANCE)}
        self._synced_distances = None
        self.interview_state = self.interview_runner.start(config_id)

    def _solve_one(self, distance: float):
        """Same cache-first path as a manual run (runs_panel.submit_run()):
        reuse an identical earlier solve, otherwise queue one on the shared
        JobRunner - never blocks the frame either way."""
        cached = self.job_runner.find_cached(self.baseline_coilset, distance)
        if cached is not None:
            return self.job_runner.touch(cached)
        name = next((c["name"] for c in self.configs if c["id"] == self.selected_config_id), self.selected_config_id)
        return self.job_runner.submit(self.baseline_coilset, config_id=self.selected_config_id, config_name=name,
                                      distance=distance)

    def _sync_baseline_distances(self, distances: tuple) -> None:
        """Keeps baseline_runs solving exactly the distances a finalized
        plan asks for: drops any solve (e.g. the initial 5mm default) the
        user didn't actually ask for, and starts one for any new distance -
        so the Baseline box always shows real numbers at the distances that
        matter, never a stale/irrelevant one."""
        wanted = set(distances)
        self.baseline_runs = {d: run for d, run in self.baseline_runs.items() if d in wanted}
        for d in distances:
            if d not in self.baseline_runs:
                self.baseline_runs[d] = self._solve_one(d)

    def confirm_plan(self) -> None:
        self.plan_confirmed = True

    def send_chat(self) -> None:
        text = self.chat_input.strip()
        if not text or self.interview_state is None or self.interview_runner.busy:
            return
        self.chat_input = ""
        # A message sent after a plan was already proposed is feedback to
        # revise it, not a confirmation. Clear BOTH plan_confirmed and
        # finalized - the stale plan card must disappear the instant a
        # revision is sent (it was staying on screen through the whole
        # revision exchange, confusingly showing the OLD plan while the
        # user and model discussed a new one), reappearing only once the
        # model calls finalize_spec again with the updated understanding.
        self.plan_confirmed = False
        self.interview_state.finalized = False
        self.interview_runner.send(self.interview_state, text)


def _message_display_text(msg: dict, tool_outcome: dict | None) -> str:
    """The chat shows the model's actual reply ("content") - NEVER its raw
    "reasoning_content" (some backends, e.g. the office Qwen3.8 server,
    expose that as a separate field of internal scratch-thinking). Order
    matters: on the SAME turn that calls finalize_spec, content is usually
    empty and reasoning_content is always present and non-empty - an
    `or` chain that falls through to reasoning_content before checking
    tool_calls will pick the raw reasoning dump every time on exactly the
    turn where it matters most (confirmed live: a long "Hmm, but M12/M13/
    M23..." monologue leaking into the chat). So tool_calls is checked
    BEFORE reasoning_content, not after - reasoning_content is a true
    last resort, not a fallback that wins by default. Contrast with
    agent/optimize.py's round reasoning_text, which deliberately WANTS the
    raw reasoning for the engineering progress feed - two different
    audiences, two different fields, two different priorities.

    tool_outcome: the paired "tool"-role message's parsed JSON (interview.py's
    `result` dict - {"valid": True, ...} or {"valid": False, "error": ...}),
    or None if it hasn't landed yet. A bare "Done." here was a real, reported
    bug: finalize_spec merely proposes a plan - the search itself hasn't even
    been confirmed yet, let alone run any FEMM solve - so "Done." read as the
    simulation having finished, prompting a real "wait, so when are you
    running this?". "Done" is reserved for when a solve actually completes
    (Search Progress/Summary, a completely different part of the UI) - this
    always names what the interview turn itself did instead, success or not."""
    content = msg.get("content")
    if content:
        return content
    if msg.get("tool_calls"):
        if tool_outcome is None:
            return "Working out the plan..."  # tool call made, no result yet - shouldn't normally persist mid-call
        if tool_outcome.get("valid"):
            return "I've put together a plan below - take a look."
        return f"That didn't finalize: {tool_outcome.get('error', 'unknown error')}"
    return msg.get("reasoning_content") or ""


# Chat-specific styling - deliberately its own look (rounded message bubbles,
# a pill input bar, a centered empty state), unlike the rest of this app's
# dense bordered-rectangle "engineering tool" style: this is the one panel
# people actually converse with, modeled after ChatGPT/Claude's own chat
# surface rather than the manual-run dashboard's look. Scoped to draw_chat()
# alone - the right column's panels are untouched.
_USER_BUBBLE_COLOR = imgui.ImVec4(0.20, 0.24, 0.32, 1.0)
_ASSISTANT_TEXT_COLOR = imgui.ImVec4(0.92, 0.92, 0.93, 1.0)
_USER_TEXT_COLOR = imgui.ImVec4(0.97, 0.97, 1.0, 1.0)
_THINKING_COLOR = imgui.ImVec4(0.6, 0.6, 0.65, 1.0)
_PLACEHOLDER_COLOR = imgui.ImVec4(0.5, 0.5, 0.54, 1.0)
_INPUT_BG_COLOR = imgui.ImVec4(0.22, 0.23, 0.26, 1.0)  # lighter than the window, so the box stands out
_INPUT_BORDER_COLOR = imgui.ImVec4(0.45, 0.47, 0.52, 1.0)
_INPUT_HINT_COLOR = imgui.ImVec4(0.66, 0.66, 0.70, 1.0)
_TRANSPARENT = imgui.ImVec4(0.0, 0.0, 0.0, 0.0)
_ACCENT_COLOR = imgui.ImVec4(0.30, 0.50, 0.92, 1.0)
_ACCENT_HOVER_COLOR = imgui.ImVec4(0.38, 0.58, 1.0, 1.0)
_BUBBLE_ROUNDING = 12.0
_MSG_PAD = 12.0
_MSG_GAP = 12.0


def _center_cursor_x(text: str, avail_width: float) -> None:
    text_w = imgui.calc_text_size(text).x
    offset = max((avail_width - text_w) / 2.0, 0.0)
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + offset)


_USER_BUBBLE_MAX_FRACTION = 0.72  # a user bubble never spans the full row width


def _draw_message_bubble(text: str, is_user: bool, width: float) -> None:
    """A user message gets a narrower, RIGHT-ALIGNED rounded bubble; an
    assistant message is plain full-width text on the left, no bubble.
    Real position (not just background tint) is what makes "who said
    this" unambiguous at a glance - a full-width tinted box for both
    roles read as confusing (a real user report: "what is the AI asking
    here"), since a subtle color difference alone is easy to miss reading
    quickly, especially in a fast back-and-forth exchange."""
    pad = _MSG_PAD
    bubble_w = width * _USER_BUBBLE_MAX_FRACTION if is_user else width
    text_w = max(bubble_w - 2 * pad, 40.0)
    text_h = imgui.calc_text_size(text, wrap_width=text_w).y
    box_h = text_h + 2 * pad
    x_offset = (width - bubble_w) if is_user else 0.0  # push user bubbles to the right edge

    row_top = imgui.get_cursor_screen_pos()
    if is_user:
        draw_list = imgui.get_window_draw_list()
        draw_list.add_rect_filled(
            imgui.ImVec2(row_top.x + x_offset, row_top.y), imgui.ImVec2(row_top.x + x_offset + bubble_w, row_top.y + box_h),
            imgui.get_color_u32(_USER_BUBBLE_COLOR), _BUBBLE_ROUNDING,
        )

    imgui.set_cursor_screen_pos(imgui.ImVec2(row_top.x + x_offset + pad, row_top.y + pad))
    imgui.push_text_wrap_pos(imgui.get_cursor_pos().x + text_w)
    imgui.text_colored(_USER_TEXT_COLOR if is_user else _ASSISTANT_TEXT_COLOR, text)
    imgui.pop_text_wrap_pos()

    # A real item (not just a bare cursor move) is required here so the
    # child window's content bounds actually grow - the same fix
    # text_feed.draw_feed()/runs_panel.draw_history() need for their rows.
    imgui.set_cursor_screen_pos(row_top)
    imgui.dummy(imgui.ImVec2(width, box_h))
    imgui.dummy(imgui.ImVec2(width, _MSG_GAP))


def _draw_chat_feed(state: AgentPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_chat_feed", size, 0)
    width = imgui.get_content_region_avail().x

    if not state.interview_state.transcript:
        # A static, non-LLM welcome line - not a model-generated opening
        # turn (that produced a long question wall before - see
        # InterviewRunner.start()'s docstring).
        imgui.dummy(imgui.ImVec2(0, 8))
        imgui.text_colored(_ASSISTANT_TEXT_COLOR, "What do you want to run today?")
        imgui.text_colored(_PLACEHOLDER_COLOR, "Describe the objective and any requirements for this search.")
        imgui.dummy(imgui.ImVec2(0, 8))

    transcript = state.interview_state.transcript
    for i, msg in enumerate(transcript):
        if msg.get("role") == "tool":
            continue
        # A tool_calls message's outcome lives in the very next transcript
        # entry (interview.py's run_interview_turn() always appends the
        # paired "tool"-role result message right after) - looked up here,
        # not skipped, so _message_display_text() can say what actually
        # happened instead of a content-free placeholder.
        tool_outcome = None
        if msg.get("tool_calls") and i + 1 < len(transcript) and transcript[i + 1].get("role") == "tool":
            try:
                tool_outcome = json.loads(transcript[i + 1]["content"])
            except (ValueError, TypeError):
                tool_outcome = None
        text = _message_display_text(msg, tool_outcome)
        if text:
            _draw_message_bubble(text, is_user=(msg.get("role") == "user"), width=width)

    if state.interview_runner.busy:
        started = state.interview_runner.turn_started_at
        elapsed = f" ({time.monotonic() - started:.0f}s)" if started is not None else ""
        imgui.text_colored(_THINKING_COLOR, f"Thinking{elapsed}...")
        # A real AI call, not a stall - a reasoning model works out an
        # answer internally before replying, so a short reply can still
        # take a while. Shown so this doesn't read as broken/hung.
        imgui.text_colored(_PLACEHOLDER_COLOR, "Generating a real response on the LLM server - this can take a minute.")

    # Only re-pin to the bottom if already there before this frame's
    # messages were added - see text_feed.draw_feed()'s identical fix for
    # why an unconditional call here made scrolling up impossible.
    if imgui.get_scroll_y() >= imgui.get_scroll_max_y():
        imgui.set_scroll_here_y(1.0)
    imgui.end_child()


def _draw_no_config_state(state: AgentPanelState, avail: imgui.ImVec2) -> None:
    """Shown only until a baseline is picked - select_config() immediately
    opens the chat the moment one is, so this is the sole "nothing to do
    yet" screen (no separate Start button/click)."""
    imgui.dummy(imgui.ImVec2(0, max(avail.y * 0.30, 30.0)))
    title = "Pick a baseline config on the right to begin."
    _center_cursor_x(title, avail.x)
    imgui.text(title)
    if state.error:
        imgui.dummy(imgui.ImVec2(0, 10))
        _center_cursor_x(state.error, avail.x)
        imgui.text_colored(_ERROR_COLOR, state.error)


def _plan_context_lines(spec) -> list[str]:
    """objective_text + distances + variable-spec notes - the per-objective
    detail lives in the table _draw_plan_metrics_table() draws instead of a
    bullet-per-objective text list, so a real user can see it against the
    baseline's own numbers rather than reading targets in the abstract."""
    lines = [spec.objective_text or "(no objective text given)", f"Distances: {spec.distances} mm"]
    vs = spec.variable_spec
    if vs.enabled_features:
        lines.append(f"Newly enabled: {sorted(vs.enabled_features)}")
    if vs.fixed_values:
        lines.append("Set to: " + ", ".join(f"{k} = {v:g}" for k, v in vs.fixed_values.items()))
    if vs.excluded_fields:
        lines.append(f"Kept fixed: {sorted(vs.excluded_fields)}")
    return lines


_PLAN_TABLE_H = 200.0
_PLAN_HIGHLIGHT_COLOR = imgui.ImVec4(0.30, 0.50, 0.92, 0.16)  # translucent accent tint - the row a target lands on
_METRIC_UNITS = dict(runs_panel._RESULT_FIELDS)


def _baseline_result(state: AgentPanelState, distance: float) -> dict | None:
    run = state.baseline_runs.get(distance)
    if isinstance(run, dict):
        return run.get("result")
    return run.result if run is not None else None


def _plan_metric_rows(spec) -> list[str]:
    """SIM_RESULT_METRICS (always shown, so the whole baseline solve stays
    visible) plus any objective on a derived quantity (outer diameter) not
    already in that list - an objective never goes missing from the table."""
    from agent.objective import DERIVED_QUANTITIES, SIM_RESULT_METRICS

    objective_metrics = {o.metric for o in spec.objectives}
    return list(SIM_RESULT_METRICS) + [m for m in DERIVED_QUANTITIES if m in objective_metrics]


def _draw_plan_metrics_table(state: AgentPanelState) -> None:
    """The baseline's own 12-metric solve, with each objective's target
    highlighted on its row - what needs to change read off real numbers,
    not the model's free-text restatement of the plan."""
    from agent.objective import SIM_RESULT_METRICS
    from agent.scorer import coil_diameters

    spec = state.interview_state.spec
    objectives = {o.metric: o for o in spec.objectives}
    plan_distance = spec.distances[0]
    baseline_result = _baseline_result(state, plan_distance) or {}
    baseline_derived = coil_diameters(state.baseline_coilset) if state.baseline_coilset is not None else {}

    at = f"at {plan_distance:g} mm" + (" (first of several)" if len(spec.distances) > 1 else "")
    imgui.text_colored(_ACCENT_COLOR, f"Target metrics (baseline solve {at})")
    imgui.begin_child("plan_metrics_child", imgui.ImVec2(0, _PLAN_TABLE_H), imgui.ChildFlags_.borders.value)
    if imgui.begin_table("plan_metrics_table", 3, imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value):
        imgui.table_setup_column("Metric")
        imgui.table_setup_column("Baseline")
        imgui.table_setup_column("Requirement")
        imgui.table_headers_row()
        for metric in _plan_metric_rows(spec):
            obj = objectives.get(metric)
            value = baseline_result.get(metric) if metric in SIM_RESULT_METRICS else baseline_derived.get(metric)
            unit = _METRIC_UNITS.get(metric, "mm")

            imgui.table_next_row()
            if obj is not None:
                imgui.table_set_bg_color(imgui.TableBgTarget_.row_bg0.value, imgui.get_color_u32(_PLAN_HIGHLIGHT_COLOR))
            imgui.table_next_column()
            (imgui.text_colored(_ACCENT_COLOR, metric) if obj is not None else imgui.text(metric))
            imgui.table_next_column()
            imgui.text(f"{value:.6g} {unit}" if isinstance(value, (int, float)) else "-")
            imgui.table_next_column()
            if obj is not None:
                imgui.text_colored(_ACCENT_COLOR, f"{'Required' if obj.required else 'Preferred'}: {obj.describe()}")
            else:
                imgui.text_disabled("keep as-is")
        imgui.end_table()
    imgui.end_child()


def _draw_plan_summary(state: AgentPanelState, avail_width: float) -> None:
    """The model calling finalize_spec proposes a plan - this renders it
    from the actual structured AgentSpec (not the model's free-text reply,
    which can be inconsistent) and requires an explicit click before
    AgentRunner.submit() ever runs (see AgentPanelState.poll())."""
    imgui.separator()
    imgui.text_colored(_ACCENT_COLOR, "Proposed plan")
    for line in _plan_context_lines(state.interview_state.spec):
        imgui.push_text_wrap_pos(avail_width)
        imgui.text_wrapped(line)
        imgui.pop_text_wrap_pos()
    imgui.dummy(imgui.ImVec2(0, 4))
    _draw_plan_metrics_table(state)
    imgui.dummy(imgui.ImVec2(0, 6))

    imgui.push_style_var(imgui.StyleVar_.frame_rounding.value, 16.0)
    imgui.push_style_color(imgui.Col_.button.value, _ACCENT_COLOR)
    imgui.push_style_color(imgui.Col_.button_hovered.value, _ACCENT_HOVER_COLOR)
    if imgui.button("Start Search", imgui.ImVec2(160, 34)):
        state.confirm_plan()
    imgui.pop_style_color(2)
    imgui.pop_style_var()
    imgui.same_line()
    imgui.text_disabled("or send another message to adjust it first")


_INPUT_HEIGHT = 64.0  # a real multi-line text area, not a single-line box
_SEND_ICON_D = 34.0  # the round send-icon button's diameter
_SEND_ICON_MARGIN = 8.0


def _draw_send_icon(center: imgui.ImVec2, enabled: bool) -> bool:
    """A round accent send button - a right-pointing triangle drawn with
    draw-list primitives, not a text glyph: the font atlas is Basic Latin
    only (see agent_candidate_card.py's _ASCII_SUBSTITUTIONS), so no arrow/
    paper-plane character is guaranteed to render. Returns True on click."""
    draw_list = imgui.get_window_draw_list()
    radius = _SEND_ICON_D / 2.0
    imgui.set_cursor_screen_pos(imgui.ImVec2(center.x - radius, center.y - radius))
    imgui.push_id("chat_send_icon")
    clicked = imgui.invisible_button("##icon", imgui.ImVec2(_SEND_ICON_D, _SEND_ICON_D)) and enabled
    hovered = imgui.is_item_hovered()
    imgui.pop_id()

    bg = _ACCENT_HOVER_COLOR if (hovered and enabled) else _ACCENT_COLOR
    if not enabled:
        bg = imgui.ImVec4(bg.x, bg.y, bg.z, 0.35)
    draw_list.add_circle_filled(center, radius, imgui.get_color_u32(bg))
    tri_r = 6.5
    fg = imgui.get_color_u32(imgui.ImVec4(1.0, 1.0, 1.0, 1.0 if enabled else 0.5))
    draw_list.add_triangle_filled(
        imgui.ImVec2(center.x - tri_r * 0.5, center.y - tri_r),
        imgui.ImVec2(center.x - tri_r * 0.5, center.y + tri_r),
        imgui.ImVec2(center.x + tri_r, center.y), fg,
    )
    return clicked


def _draw_chat_input_bar(state: AgentPanelState, avail_width: float) -> None:
    """One rounded bar: the multi-line text area with the send icon inline
    on its right, inside the same outline - no separate button row below."""
    imgui.push_style_var(imgui.StyleVar_.frame_rounding.value, 12.0)
    imgui.push_style_var(imgui.StyleVar_.frame_padding.value, imgui.ImVec2(14.0, 10.0))

    icon_col_w = _SEND_ICON_D + _SEND_ICON_MARGIN * 2.0
    text_w = max(avail_width - icon_col_w, 80.0)

    # The multi-line widget is its own child window, drawn on top of this
    # window's draw list and filled with the theme's dark, mostly-opaque
    # frame_bg - which buried the placeholder drawn underneath it. So the
    # box's background and placeholder are drawn here first, and the
    # widget's own fill/border are made transparent so they show through.
    box_pos = imgui.get_cursor_screen_pos()
    box_max = imgui.ImVec2(box_pos.x + avail_width, box_pos.y + _INPUT_HEIGHT)
    draw_list = imgui.get_window_draw_list()
    draw_list.add_rect_filled(box_pos, box_max, imgui.get_color_u32(_INPUT_BG_COLOR), 12.0)
    if not state.chat_input and not state.chat_input_active:
        # A manual hint overlay, not input_text_with_hint() - that widget
        # has no multi-line form.
        draw_list.add_text(imgui.ImVec2(box_pos.x + 14, box_pos.y + 10), imgui.get_color_u32(_INPUT_HINT_COLOR),
                           "Describe your objective and requirements... (Enter to send, Ctrl+Enter for a new line)")

    imgui.push_style_color(imgui.Col_.frame_bg.value, _TRANSPARENT)
    imgui.push_style_color(imgui.Col_.border.value, _TRANSPARENT)
    imgui.set_next_item_width(text_w)
    # ctrl_enter_for_new_line: plain Enter submits (detected below via
    # is_item_deactivated_after_edit - Dear ImGui's multi-line widget has
    # no EnterReturnsTrue equivalent, it just loses focus), Ctrl+Enter
    # inserts a newline. Note this is Dear ImGui's real built-in modifier
    # for "the other" action - it's Ctrl, not Shift, despite Shift+Enter
    # being the more common convention in other chat apps; a true
    # Shift+Enter would need custom key-callback handling this codebase
    # doesn't have anywhere else.
    flags = imgui.InputTextFlags_.ctrl_enter_for_new_line.value
    changed, new_text = imgui.input_text_multiline(
        "##chat_input", state.chat_input, imgui.ImVec2(text_w, _INPUT_HEIGHT), flags,
    )
    imgui.pop_style_color(2)
    if changed:
        state.chat_input = new_text
    submitted_via_enter = imgui.is_item_deactivated_after_edit()
    # Read after the widget, used by next frame's placeholder (drawn before it).
    state.chat_input_active = imgui.is_item_active()
    draw_list.add_rect(box_pos, box_max,
                       imgui.get_color_u32(_ACCENT_COLOR if state.chat_input_active else _INPUT_BORDER_COLOR),
                       12.0, 1.5)

    can_send = bool(state.chat_input.strip())
    icon_center = imgui.ImVec2(box_pos.x + text_w + icon_col_w / 2.0, box_pos.y + _INPUT_HEIGHT / 2.0)
    send_clicked = _draw_send_icon(icon_center, can_send)
    imgui.pop_style_var(2)
    imgui.set_cursor_screen_pos(imgui.ImVec2(box_pos.x, box_max.y))

    if (send_clicked and can_send) or submitted_via_enter:
        state.send_chat()


_BASELINE_DIAGRAM_H = 200.0
_BASELINE_RUNNING_H = 420.0  # the box's height while Search Progress shares the column
_BASELINE_PICKER_ONLY_H = 46.0  # just the picker row, above a finished search's summary
_RESULTS_COL_W = 330.0


def _draw_one_baseline_result(distance: float, run) -> None:
    imgui.text(f"Simulated at {distance:g} mm")
    if isinstance(run, dict):  # a cache hit - already done
        imgui.text_disabled("(cached result)")
        runs_panel.draw_result_table(run["result"], table_id=f"agent_baseline_result_{distance:g}")
        return
    if run.status.value == "failed":
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_ERROR_COLOR, f"Solve failed: {run.error}")
        imgui.pop_text_wrap_pos()
        return
    if run.result is None:
        imspinner.spinner_ang(f"##baseline_spinner_{distance:g}", 9.0, 2.5)
        imgui.same_line()
        started = run.started_at or run.created_at
        imgui.text_disabled(f"Solving in FEMM... {(now() - started).total_seconds():.0f}s")
        return
    runs_panel.draw_result_table(run.result, table_id=f"agent_baseline_result_{distance:g}")


def _draw_baseline_results(state: AgentPanelState) -> None:
    """The baseline's own solve at every requested distance - a spinner
    while FEMM runs each one, then the same 12-quantity table as a manual
    run's detail view. One block per distance, stacked (scrolls within the
    box's own child window if there are several)."""
    for i, distance in enumerate(sorted(state.baseline_runs)):
        if i > 0:
            imgui.separator()
        _draw_one_baseline_result(distance, state.baseline_runs[distance])


def _draw_baseline_inputs(buffer: dict) -> None:
    """The baseline's parameters, read-only - the same sections and labels
    as the manual-mode config form. Editing stays in Manual Runs."""
    labels = field_layout.field_labels()
    groups = [(g, names) for g, names in field_layout.FIELD_GROUPS.items() if g != "General"]
    groups.append(("Advanced", list(field_layout.ADVANCED_FIELDS)))
    for group_name, names in groups:
        flags = imgui.TreeNodeFlags_.default_open.value if group_name in ("Loop", "Tank", "RC") else 0
        if not imgui.collapsing_header(f"{group_name}##baseline_{group_name}", flags=flags):
            continue
        if imgui.begin_table(f"baseline_inputs_{group_name}", 2,
                             imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value):
            imgui.table_setup_column("Parameter", imgui.TableColumnFlags_.width_stretch.value)
            imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_fixed.value, 90.0)
            for name in names:
                value = buffer.get(name)
                if name in field_layout.PRESENCE_FIELDS:
                    value = "yes" if value else "no"
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(labels.get(name, name))
                imgui.table_next_column()
                imgui.text(agent_candidate_card._fmt(value))
            imgui.end_table()


def draw_baseline_box(state: AgentPanelState, size: imgui.ImVec2, show_body: bool = True) -> None:
    """One box for the baseline: the config picker, then (show_body) its
    geometry, its own simulated results and its input parameters."""
    state.refresh_configs()
    imgui.begin_child("agent_baseline_pane", size, imgui.ChildFlags_.borders.value)
    names = [c["name"] for c in state.configs]
    ids = [c["id"] for c in state.configs]
    current_index = ids.index(state.selected_config_id) if state.selected_config_id in ids else -1

    imgui.align_text_to_frame_padding()
    imgui.text("Baseline")
    imgui.same_line()
    imgui.set_next_item_width(imgui.get_content_region_avail().x)
    changed, new_index = imgui.combo("##baseline_combo", current_index, names)
    if changed:
        state.select_config(ids[new_index])

    if show_body and state.baseline_coilset is None:
        imgui.text_disabled("Pick a baseline to see its geometry, inputs and simulated results.")
    elif show_body:
        buffer = dataclasses.asdict(state.baseline_coilset)
        # The cross-section's "Distance=" annotation shows whichever
        # requested distance sorts first - purely representative (the
        # geometry itself doesn't change with distance).
        diagram_distance = min(state.baseline_runs) if state.baseline_runs else BASELINE_DISTANCE
        coil_diagram.draw(buffer, diagram_distance, imgui.ImVec2(0, _BASELINE_DIAGRAM_H),
                          child_id="agent_live_diagram", title="Geometry",
                          femm_label=f"{buffer.get('config_name') or 'baseline'} baseline")
        rest_h = max(imgui.get_content_region_avail().y, 80.0)
        results_w = min(_RESULTS_COL_W, imgui.get_content_region_avail().x * 0.45)
        imgui.begin_child("agent_baseline_results", imgui.ImVec2(results_w, rest_h), imgui.ChildFlags_.borders.value)
        _draw_baseline_results(state)
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child("agent_baseline_inputs", imgui.ImVec2(0, rest_h), imgui.ChildFlags_.borders.value)
        imgui.text("Inputs")
        _draw_baseline_inputs(buffer)
        imgui.end_child()
    imgui.end_child()


def draw_chat(state: AgentPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_chat_pane", size, imgui.ChildFlags_.borders.value)
    avail = imgui.get_content_region_avail()

    if state.interview_state is None:
        _draw_no_config_state(state, avail)
        imgui.end_child()
        return

    show_plan = state.interview_state.finalized and not state.plan_confirmed
    # Context lines + the fixed-height, independently-scrolling metrics
    # table (_PLAN_TABLE_H) + header/button chrome (~80px).
    plan_h = (80.0 + len(_plan_context_lines(state.interview_state.spec)) * imgui.get_text_line_height_with_spacing()
              + _PLAN_TABLE_H
              if show_plan else 0.0)
    input_h = _INPUT_HEIGHT  # the send icon is inline now, no separate button row below
    feed_h = max(avail.y - input_h - plan_h - 8.0, 60.0)

    _draw_chat_feed(state, imgui.ImVec2(avail.x, feed_h))

    if show_plan:
        _draw_plan_summary(state, avail.x)

    imgui.begin_disabled(state.plan_confirmed or state.interview_runner.busy)
    _draw_chat_input_bar(state, avail.x)
    imgui.end_disabled()

    if state.interview_state.error:
        imgui.text_colored(_ERROR_COLOR, state.interview_state.error)
    imgui.end_child()


def _round_timing_text(r) -> str:
    """Splits a round's wall-clock time into "waiting on the LLM" vs.
    "waiting on FEMM solves" - real, per-round data for a real question
    ("which is slower, the AI or xfemm?") that had no logged answer
    before. now() (agent/state.py) is UTC-aware throughout, so subtraction
    is always safe even across the background-thread boundary."""
    from datetime import datetime, timezone

    if r.evaluating_started_at is None:
        if r.done_at is not None:
            return ""  # no tool call this round - nothing to time-split
        elapsed = (datetime.now(timezone.utc) - r.proposing_started_at).total_seconds()
        return f"[AI thinking: {elapsed:.0f}s so far]"
    llm_s = (r.evaluating_started_at - r.proposing_started_at).total_seconds()
    if r.done_at is None:
        elapsed = (datetime.now(timezone.utc) - r.evaluating_started_at).total_seconds()
        return f"[AI: {llm_s:.0f}s, FEMM solving: {elapsed:.0f}s so far]"
    femm_s = (r.done_at - r.evaluating_started_at).total_seconds()
    return f"[AI: {llm_s:.0f}s, FEMM: {femm_s:.0f}s]"


def draw_progress(state: AgentPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_progress_pane", size, imgui.ChildFlags_.borders.value)
    imgui.text("Search Progress")
    imgui.separator()

    run_state = state.run_state
    imgui.text_disabled(f"status: {run_state.status.value}")

    # Plain dicts throughout, so agent_candidate_card renders a live round
    # exactly the way agent_history.py renders a replayed one.
    baseline_buffer = dataclasses.asdict(run_state.baseline_coilset)
    spec_summary = dataclasses.asdict(run_state.spec)
    distance = run_state.spec.distances[0]

    imgui.begin_child("agent_progress_feed", imgui.get_content_region_avail(), 0)
    for r in run_state.rounds:
        agent_candidate_card.draw_round_block(
            f"live_round_{r.round_num}", f"Round {r.round_num} [{r.status.value}]",
            _round_timing_text(r), r.reasoning_text,
            [dataclasses.asdict(c) for c in r.candidates],
            baseline_buffer, spec_summary, distance,
        )
    # Only re-pin to the bottom if already there - see text_feed.draw_feed().
    if imgui.get_scroll_y() >= imgui.get_scroll_max_y():
        imgui.set_scroll_here_y(1.0)
    imgui.end_child()
    imgui.end_child()


def _search_finished(state: AgentPanelState) -> bool:
    """Finished AND has a winner to show. A search that ended with no valid
    candidate at all keeps the round-by-round feed instead - there's no
    comparison to summarize, and those rounds are what explain why."""
    run_state = state.run_state
    return run_state is not None and run_state.best is not None and run_state.status.value != "running"


def _total_timing_text(run_state) -> str:
    llm_s = sum((r.evaluating_started_at - r.proposing_started_at).total_seconds()
                for r in run_state.rounds if r.evaluating_started_at)
    femm_s = sum((r.done_at - r.evaluating_started_at).total_seconds()
                 for r in run_state.rounds if r.evaluating_started_at and r.done_at)
    return f"Time: AI {llm_s:.0f}s, FEMM {femm_s:.0f}s"


def _draw_summary(state: AgentPanelState, size: imgui.ImVec2) -> None:
    """The finished search in one box, replacing both the live geometry and
    the round-by-round feed - the same view agent_history.py replays for a
    saved run (agent_candidate_card.draw_search_summary())."""
    from agent.scorer import tool_result
    from agent.variables import allowed_fields, audit_changed_fields

    run_state = state.run_state
    best = run_state.best
    # Diffs EVERY field, not just the free ones - a free_fields-filtered diff
    # could never show a change outside the allowed set.
    audit = audit_changed_fields(run_state.baseline_coilset, best.coilset,
                                 allowed_fields(run_state.baseline_coilset, run_state.spec.variable_spec))

    baseline_buffer = dataclasses.asdict(run_state.baseline_coilset)
    spec_summary = dataclasses.asdict(run_state.spec)
    distances = run_state.spec.distances
    # Round-by-round detail below still uses the first distance - a
    # per-round card is about what the model saw/decided that round, not
    # "compare at any distance", which stays the summary's own thing.
    round_distance = distances[0]

    imgui.begin_child("agent_summary_pane", size, imgui.ChildFlags_.borders.value)
    state.summary_distance = agent_candidate_card.draw_search_summary(
        "agent_summary", run_state.status.value,
        [(r.round_num, [dataclasses.asdict(c) for c in r.candidates]) for r in run_state.rounds],
        _total_timing_text(run_state), baseline_buffer,
        dataclasses.asdict(best.coilset), tool_result(best, run_state.spec), spec_summary,
        distances, state.summary_distance, audit,
        baseline_coilset=run_state.baseline_coilset, best_coilset=best.coilset,
    )

    # Same collapsed round-by-round detail as the History replay of this
    # same finished run (agent_history.py's _draw_detail()) - the summary
    # above answers "what happened", this answers "show me every step" on
    # demand, without duplicating agent_candidate_card.draw_round_block()'s
    # rendering for a third time.
    imgui.spacing()
    if imgui.tree_node_ex("Round-by-round details##agent_summary_rounds", 0):
        for r in run_state.rounds:
            agent_candidate_card.draw_round_block(
                f"live_summary_r{r.round_num}", f"Round {r.round_num} [{r.status.value}]",
                _round_timing_text(r), r.reasoning_text,
                [dataclasses.asdict(c) for c in r.candidates],
                baseline_buffer, spec_summary, round_distance,
            )
        imgui.tree_pop()
    imgui.end_child()


def draw_right_column(state: AgentPanelState, size: imgui.ImVec2) -> None:
    """The right column, top to bottom - main.py doesn't need to know which
    is showing. Before a search: the Baseline box alone, filling the column.
    While one runs (or ended with nothing to summarize): a shorter Baseline
    box above Search Progress. Once it finishes with a winner: just the
    picker row above the Search Summary. History (main.py's toolbar
    toggle) replaces all of it. Height 0 = fill (ImGui's convention)."""
    fill = imgui.ImVec2(size.x, 0)
    if state.showing_history:
        agent_history.draw(state.history, fill)
    elif state.run_state is None:
        draw_baseline_box(state, fill)
    elif _search_finished(state):
        draw_baseline_box(state, imgui.ImVec2(size.x, _BASELINE_PICKER_ONLY_H), show_body=False)
        _draw_summary(state, fill)
    else:
        draw_baseline_box(state, imgui.ImVec2(size.x, _BASELINE_RUNNING_H))
        draw_progress(state, fill)
