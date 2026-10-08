"""AI Agent mode: an interview chat (left) that gathers an objective, then
a live search-progress feed (right) once the interview hands off to the
optimize phase. The chat stays open after that - questions about the running
or finished search go to agent/femm_agent/services/followup.py, which can answer but never
change anything (the baseline and plan are locked once the search starts;
Restart is the only way out) - the toggle-mode counterpart to configs_panel/runs_panel's
manual-run dashboard (see main.py's toolbar toggle, _draw_agent_mode()).

Reuses coil_diagram.draw() verbatim for the live baseline view and the
finished-search summary's best-vs-baseline comparison, and
agent_candidate_card.draw_round_block() for the round-by-round search feed
(geometry + input/output tables + short reasoning per candidate - shared
with agent_history.py's replay) while the search runs. Only ever
calls into backend/agent's entry points (InterviewRunner,
AgentRunner) - never reimplements orchestration here.
"""

import dataclasses
import json
import time

from imgui_bundle import imgui, imspinner

from ..services import field_layout
from ..services.job_runner import now
from ..visualization import coil_diagram
from ..visualization.result_table import RESULT_FIELDS, draw_result_table
from . import agent_candidate_card, agent_history

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
# The baseline's own solve (and its diagram) - before the interview there's
# no chosen distance yet, so the manual-run default.
BASELINE_DISTANCE = 5.0


class AgentPanelState:
    def __init__(self, job_runner) -> None:
        from agent.input_chat.services.runner import InterviewRunner
        from agent.femm_agent.services.runner import AgentRunner

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
        self.interview_state = None  # agent.input_chat.services.interview.InterviewState, created the moment a config is picked
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
        # Where in the chat transcript each search segment was started (Start
        # Search, then each "continue") - the chat feed draws a search-status
        # line there, so follow-up questions appear below it. The last one is
        # the live segment's.
        self.search_markers: list[int] = []
        # A baseline picked while the chat has content, waiting on the
        # "this clears the chat" confirmation (see request_baseline()).
        self.pending_baseline_id: str | None = None
        # Why the proposed plan can't start under the Search fields table as it is
        # now (checked when Start Search is clicked - see confirm_plan()).
        self.plan_problems: list[str] = []
        self._field_limits_cache: tuple | None = None
        self.refresh_configs()

    @property
    def phase(self) -> str:
        """"none" (no baseline picked), "interview" (planning), "running"
        (search in progress) or "finished" (search ended, any outcome)."""
        if self.interview_state is None:
            return "none"
        if self.run_state is None:
            return "interview"
        return "running" if self.run_state.status.value == "running" else "finished"

    @property
    def baseline_locked(self) -> bool:
        """The baseline can't be changed once a search has started (only
        Restart unlocks it), nor mid-reply - a turn still in flight would
        otherwise land in the next conversation."""
        return self.run_state is not None or self.interview_runner.busy

    @property
    def baseline_error(self) -> str | None:
        """Why the baseline can't be simulated, or None while every solve of it
        is done or still going. Plain text for the chat's notice. Checked
        against baseline_runs, so it covers the 5 mm solve started on picking
        a config and, once a plan is finalized, each of the plan's distances."""
        for distance in sorted(self.baseline_runs):
            run = self.baseline_runs[distance]
            if not isinstance(run, dict) and run.status.value == "failed":
                return f"Solve failed at {distance:g} mm: {run.error or 'no reason given'}"
        return None

    @property
    def planning_blocked(self) -> bool:
        """True while a plan can't go anywhere because the baseline can't be
        simulated: the planning chat and Start Search are locked, since every
        candidate is compared against a baseline that doesn't exist. Not a
        block once the search has started - by then its own baseline solve
        already succeeded, and follow-up questions change nothing."""
        return self.run_state is None and self.baseline_error is not None

    def retry_baseline(self) -> None:
        """Re-queues every failed baseline solve. A failed solve is never a
        cache hit, so this really solves again (a crash or timeout can clear;
        invalid geometry won't, and fails again with the same message)."""
        for distance, run in list(self.baseline_runs.items()):
            if not isinstance(run, dict) and run.status.value == "failed":
                self.baseline_runs[distance] = self._solve_one(distance)

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
        from engine.repositories.coilset_repository import list_configs
        self.configs = list_configs()

    def select_config(self, config_id: str) -> None:
        """Picking a baseline immediately opens an empty chat, ready to
        type into - no separate "Start" click, no opening LLM call (see
        InterviewRunner.start())."""
        from engine.repositories.coilset_repository import get_coilset

        if self.run_state is None and self.interview_state is not None:
            # Switching baseline mid-planning closes that conversation, just
            # like Restart does: it is kept in History as cancelled (if the
            # user ever sent a message - otherwise there is nothing to keep).
            self.interview_runner.cancel_conversation()
        self.selected_config_id = config_id
        self.run_state = None
        self.search_markers = []
        self.summary_distance = None
        self.plan_confirmed = False
        self.plan_problems = []
        self.error = ""
        self.baseline_coilset = get_coilset(config_id)
        # No objective (hence no distances) is known yet - a single solve at
        # the manual-run default, replaced by _sync_baseline_distances() the
        # moment a plan is finalized with the user's real distances.
        self.baseline_runs = {BASELINE_DISTANCE: self._solve_one(BASELINE_DISTANCE)}
        self._synced_distances = None
        self.interview_state = self.interview_runner.start(config_id)

    def request_baseline(self, config_id: str) -> bool:
        """The picker's entry point. Switches straight away when there's
        nothing to lose; if the planning chat has messages (or a typed,
        unsent draft) it only records the pick and returns True - the caller
        asks for confirmation, then calls confirm_baseline_change()."""
        if config_id == self.selected_config_id or self.baseline_locked:
            return False
        has_chat = self.interview_state is not None and bool(self.interview_state.transcript)
        if has_chat or self.chat_input.strip():
            self.pending_baseline_id = config_id
            return True
        self.select_config(config_id)
        return False

    def confirm_baseline_change(self) -> None:
        config_id, self.pending_baseline_id = self.pending_baseline_id, None
        if config_id is None or self.baseline_locked:
            return
        self.chat_input = ""
        self.select_config(config_id)  # discards the planning conversation (see select_config)

    def cancel_baseline_change(self) -> None:
        self.pending_baseline_id = None

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

    # ---- the Search fields table (Settings): which fields a search may change
    def field_limits(self) -> dict[str, str]:
        """Open field -> its allowed values in words ("1 to 8"), under the table
        this conversation is planning with. Shown beside the baseline's values."""
        from agent.femm_agent.models.schema import limit_text
        from agent.input_chat.models.variables import free_fields_for, search_field_table
        if self.interview_state is None or self.baseline_coilset is None:
            return {}
        key = (self.selected_config_id, json.dumps(self.interview_state.settings.search_fields, sort_keys=True))
        if self._field_limits_cache is None or self._field_limits_cache[0] != key:
            free = free_fields_for(self.baseline_coilset, None, search_field_table(self.interview_state.settings))
            self._field_limits_cache = (key, {name: limit_text(schema) for name, schema in free.items()})
        return self._field_limits_cache[1]

    @property
    def pending_field_change(self) -> dict | None:
        """A Search fields change the AI proposed and the user hasn't answered -
        only while planning; once a search has started its table is fixed."""
        if self.interview_state is None or self.run_state is not None:
            return None
        return self.interview_state.pending_field_change

    def apply_field_change(self) -> None:
        """The Apply button on a proposal: the user's own action, and the only
        place outside the Settings screen that changes the table. Saves it,
        brings this conversation onto the new table, and tells the model (a
        fixed, code-written message) so it re-plans with the field open."""
        from agent.settings.settings import apply_search_field_changes
        pending = self.pending_field_change
        if pending is None or self.interview_runner.busy:
            return
        try:
            saved = apply_search_field_changes(pending["changes"])
        except ValueError as exc:
            self.error = f"Couldn't apply the change: {exc}"
            return
        self.error = ""
        self.interview_state.settings.search_fields = dict(saved.search_fields)
        self.interview_state.pending_field_change = None
        self.plan_confirmed = False
        self.interview_state.finalized = False
        self.plan_problems = []
        self.interview_runner.send(self.interview_state, FIELD_CHANGE_APPLIED_MESSAGE)

    def dismiss_field_change(self) -> None:
        if self.interview_state is not None:
            self.interview_state.pending_field_change = None

    def recheck_plan(self) -> list[str]:
        """Copies the Search fields table as saved right now into the plan (it
        may have been edited in Settings since the plan was proposed) and
        re-runs the plan's own checks against it. Returns, and remembers, why
        the plan can't start - empty when it can."""
        from agent.input_chat.services.interview import plan_problems
        from agent.repositories.ai_run_repository import finalize_spec
        from agent.settings.settings import load_settings
        spec = self.interview_state.spec
        live = load_settings().search_fields
        if live != spec.settings.search_fields:
            spec.settings.search_fields = dict(live)
            self.interview_state.settings.search_fields = dict(live)
            if self.interview_runner.ai_run_id is not None:
                finalize_spec(self.interview_runner.ai_run_id, dataclasses.asdict(spec))  # the stored plan carries the table it runs under
        self.plan_problems = plan_problems(spec, self.baseline_coilset)
        return self.plan_problems

    def confirm_plan(self) -> None:
        if self.planning_blocked:
            return  # the Start Search button is disabled too - this covers any other caller
        if self.recheck_plan():
            return  # shown on the plan, which stays unconfirmed
        self.plan_confirmed = True
        self.search_markers = [len(self.interview_state.transcript)]

    def restart(self) -> None:
        """Ends the current conversation and opens a fresh one on the same
        baseline. The old one is always kept in History:

        - finished: exactly as it is.
        - still searching: the search is stopped (AgentRunner.stop()) and
          saved with every finished round - as cancelled, or as goal met if
          a design already met the goal.
        - still planning: saved as cancelled, with its plan if it had one.

        A chat reply still in flight is thrown away when it lands."""
        if self.interview_state is None:
            return
        from agent.input_chat.services.runner import InterviewRunner

        phase = self.phase
        self.interview_runner.discard()
        if phase == "running":
            self.agent_runner.stop(self.run_state.id)
        elif phase == "interview":
            self.interview_runner.cancel_conversation()
        self.interview_runner = InterviewRunner()
        self.chat_input = ""
        self.select_config(self.selected_config_id)

    def stop_search(self) -> None:
        """ "Stop and keep the best": ends the running search at its next
        checkpoint. The chat stays open on it, with its best design and
        report, and it can be continued."""
        if self.phase == "running":
            self.agent_runner.stop(self.run_state.id)

    def continue_search(self) -> bool:
        """Searches on from where the finished search stopped: same goal, same
        memory, a fresh budget, rounds appended to the same run
        (AgentRunner.continue_run()). False if there is nothing to continue."""
        if self.phase != "finished" or self.interview_runner.busy:
            return False
        try:
            self.agent_runner.continue_run(self.run_state.id)
        except ValueError as exc:
            self.error = str(exc)
            return False
        self.error = ""
        self.search_markers.append(len(self.interview_state.transcript))
        return True

    def send_chat(self) -> None:
        text = self.chat_input.strip()
        if not text or self.interview_state is None or self.interview_runner.busy:
            return
        if self.planning_blocked:
            return  # keeps the typed text - the chat's notice says why
        self.chat_input = ""
        if self.phase == "finished" and text.lower() == "continue":
            # Recognised by code, not by a model: the whole message is the
            # word "continue". Recorded in the chat, then the search resumes.
            self.interview_runner.record_user_message(self.interview_state, text)
            self.continue_search()
            return
        if self.run_state is not None:
            # The search has started: questions only, nothing about the plan
            # can change - see agent/femm_agent/services/followup.py.
            self.interview_runner.send_followup(self.interview_state, self.run_state, text)
            return
        # A message sent after a plan was already proposed is feedback to
        # revise it, not a confirmation. Clear BOTH plan_confirmed and
        # finalized - the stale plan card must disappear the instant a
        # revision is sent (it was staying on screen through the whole
        # revision exchange, confusingly showing the OLD plan while the
        # user and model discussed a new one), reappearing only once the
        # model calls finalize_spec again with the updated understanding.
        self.plan_confirmed = False
        self.interview_state.finalized = False
        self.plan_problems = []
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
    agent/femm_agent/services/optimize.py's round reasoning_text, which deliberately WANTS the
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
    always names what the interview turn itself did instead, success or not.

    A flag_off_topic call is checked FIRST, before "content" - deliberately,
    not incidentally. A model can put explanatory text in "content" on the
    very same reply that also calls a tool, and none of that text should
    ever reach the user here: the whole point of this tool is that the
    redirect the user sees is fixed and identical every time, never the
    model's own words about why (see interview.py / docs/agent-skills.md).

    Deliberately short, and says nothing about what happens next: the very
    next transcript entry is interview.py's toolless follow-up reply, which
    actually knows the context (e.g. a pending question) and continues from
    there - this line used to also say "what would you like the search to
    achieve?", which then sat right above that follow-up contradicting it
    (reading as a restart immediately followed by "no wait, back to X")."""
    tool_calls = msg.get("tool_calls")
    if tool_calls and tool_calls[0]["function"]["name"] == "flag_off_topic":
        return "That's outside what I can help with here."

    content = msg.get("content")
    if content:
        return content
    if tool_calls:
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
    # runs_panel.draw_history() needs for their rows.
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
        if i in state.search_markers:
            _draw_search_marker(state, width, i)
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

    for marker in state.search_markers:
        if marker >= len(transcript):
            _draw_search_marker(state, width, marker)

    if state.interview_runner.busy:
        started = state.interview_runner.turn_started_at
        elapsed = f" ({time.monotonic() - started:.0f}s)" if started is not None else ""
        imgui.text_colored(_THINKING_COLOR, f"Thinking{elapsed}...")
        live = state.interview_state.live_reasoning if state.interview_state is not None else ""
        if live:
            # The model's live chain-of-thought, growing as it streams in
            # (agent/services/llm_client.py's on_update) - replaces the generic
            # placeholder the instant real text arrives, so "Thinking..."
            # never sits there silent while the server is actually working.
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_PLACEHOLDER_COLOR, agent_candidate_card.full_text(live))
            imgui.pop_text_wrap_pos()
        else:
            # A real AI call, not a stall - a reasoning model works out an
            # answer internally before replying, so a short reply can still
            # take a while. Shown so this doesn't read as broken/hung.
            imgui.text_colored(_PLACEHOLDER_COLOR, "Generating a real response on the LLM server - this can take a minute.")

    # Only re-pin to the bottom if already there before this frame's
    # messages were added (an unconditional call here made scrolling up
    # impossible).
    if imgui.get_scroll_y() >= imgui.get_scroll_max_y():
        imgui.set_scroll_here_y(1.0)
    imgui.end_child()


_FINISHED_TEXT = {
    "goal_met": "goal met",
    "budget_exhausted": "target not achieved within the budget",
    "stopped_by_model": "the AI stopped without meeting the goal",
    "stalled": "stopped early, no longer getting closer",
    "failed": "failed",
    "interrupted": "interrupted",
    "cancelled": "stopped by you",
}
# ai_runs.stop_reason -> the rule that ended the search, in a few words.
STOP_REASON_TEXT = {
    "nothing_left": "nothing left to improve", "plateau": "no better design for a while",
    "stalled": "no longer getting closer", "rounds": "round limit", "time": "time limit",
    "simulations": "simulation limit", "model_stopped": "the AI stopped proposing",
    "user_stopped": "stopped by you", "llm_failed": "AI requests failed", "internal_error": "internal error",
}
_RESTART_POPUP_ID = "confirm_restart_agent"
_STOP_POPUP_ID = "confirm_stop_agent"


def _format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def _running_status_text(run_state) -> str:
    """One line for the pinned status while a search runs - read straight
    off the live AgentRunState, no LLM involved."""
    rounds = run_state.rounds
    elapsed = _format_elapsed((now() - run_state.created_at).total_seconds())
    if not rounds:
        return f"Search running: solving the baseline ({elapsed})"
    current = rounds[-1]
    doing = {"proposing": "AI proposing", "evaluating": "FEMM solving", "done": "round done"}.get(
        current.status.value, current.status.value)
    if current.status.value == "proposing" and current.ai_try > 1:
        doing += f" (retry {current.ai_try} of {run_state.spec.settings.round_llm_attempts} after a failed AI request)"
    best = run_state.best
    best_text = "" if best is None else (" - a design meets the goal, looking for better" if best.goal_met else " - goal not met yet")
    budget = run_state.budget
    where = f"round {current.round_num}"
    if budget is not None:
        # The budget is this segment's: rounds, minutes and simulations all count toward it.
        where = (f"round {budget.rounds_used} of {budget.max_rounds}, {budget.elapsed_s() / 60:.0f} of "
                 f"{budget.time_limit_s / 60:.0f} min, {budget.simulations_used} of {budget.max_simulations} simulations")
    if current.search_attempt > 1:
        where += ", resumed after failed AI requests"
    return f"Search running: {where}, {doing} ({elapsed}){best_text}"


def _finished_status_text(run_state) -> str:
    outcome = _FINISHED_TEXT.get(run_state.status.value, run_state.status.value)
    rule = STOP_REASON_TEXT.get(run_state.stop_reason)
    return f"Search finished: {outcome} after {len(run_state.rounds)} round(s)" + (f" ({rule})." if rule else ".")


def _draw_search_marker(state: AgentPanelState, width: float, marker: int) -> None:
    """The point in the chat where a search segment was started (Start
    Search, or a "continue"). For the latest one: while it runs the live
    status is pinned above the input instead (always visible, even scrolled
    up); once it ends this line says how it ended and offers Continue."""
    imgui.separator()
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + width)
    started_text = "Search started." if marker == state.search_markers[0] else "Search continued with a fresh budget."
    if marker != state.search_markers[-1]:
        imgui.text_colored(_ACCENT_COLOR, started_text)
    elif state.phase == "finished":
        imgui.text_colored(_ACCENT_COLOR, _finished_status_text(state.run_state))
        if state.run_state.outcome_report:
            agent_candidate_card.draw_outcome_report(state.run_state.outcome_report)
        imgui.text_colored(_PLACEHOLDER_COLOR, 'Ask anything about this run. To search further toward the same goal, '
                                               'reply "continue" or click Continue. To change the goal, click Restart '
                                               "and start a new task - this run stays in History.")
        if imgui.button(f"Continue##continue_{marker}"):
            state.continue_search()
        if imgui.is_item_hovered():
            imgui.set_tooltip("Keep searching: same goal, everything tried so far kept in memory, a fresh budget.")
    else:
        imgui.text_colored(_ACCENT_COLOR, started_text)
        imgui.text_colored(_PLACEHOLDER_COLOR, "You can keep asking questions while it runs; the baseline and "
                                               "plan are locked.")
    imgui.pop_text_wrap_pos()
    imgui.separator()
    imgui.dummy(imgui.ImVec2(width, _MSG_GAP))


def _draw_chat_header(state: AgentPanelState) -> None:
    """Phase label on the left, Restart on the right (and, while a search
    runs, "Stop and keep the best" beside it). Restarting a finished run
    changes nothing about it, so it happens straight away; an unfinished one
    is closed as cancelled, so that asks first."""
    labels = {"interview": "Planning", "running": "Searching - questions only",
              "finished": "Finished - questions only"}
    button_w = 90.0
    right_x = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x - button_w
    imgui.align_text_to_frame_padding()
    imgui.text_disabled(labels.get(state.phase, ""))
    imgui.same_line(max(right_x, 0.0))
    if imgui.button("Restart", imgui.ImVec2(button_w, 0)):
        if state.phase == "finished":
            state.restart()
        else:
            imgui.open_popup(_RESTART_POPUP_ID)
    if imgui.is_item_hovered():
        imgui.set_tooltip("Start a new conversation on this baseline." if state.phase == "finished"
                          else "Close this conversation and start over. It stays in History.")
    if state.phase == "running":
        stop_label = "Stop and keep the best"
        stop_w = imgui.calc_text_size(stop_label).x + 20.0
        imgui.same_line(max(right_x - stop_w - 8.0, 0.0))
        if imgui.button(stop_label, imgui.ImVec2(stop_w, 0)):
            imgui.open_popup(_STOP_POPUP_ID)
        if imgui.is_item_hovered():
            imgui.set_tooltip("End the search now and keep its best design. You can continue it later.")
    stop_open, _ = imgui.begin_popup_modal(_STOP_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if stop_open:
        imgui.text("Stop the search and keep the best design so far?")
        imgui.text_disabled("It stops after the step in progress. Reply \"continue\" afterwards to search on.")
        imgui.separator()
        if imgui.button("Cancel##stop"):
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button("Stop search"):
            state.stop_search()
            imgui.close_current_popup()
        imgui.end_popup()

    # begin_popup_modal() returns a tuple - unpack it (see configs_panel.py).
    is_open, _ = imgui.begin_popup_modal(_RESTART_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if is_open:
        if state.phase == "finished":
            # The search finished while the popup was open - nothing to discard any more.
            imgui.text("The search just finished - it will stay in History.")
        elif state.phase == "running":
            imgui.text("Stop the running search and start a new conversation?")
            imgui.text_disabled("The search is kept in History with every finished round and its best design.")
            imgui.text_disabled("To stop it but stay here, use \"Stop and keep the best\" instead.")
        else:
            imgui.text("Close this conversation and start a new one?")
            imgui.text_disabled("It is kept in History as cancelled.")
        imgui.separator()
        if imgui.button("Cancel"):
            imgui.close_current_popup()
        imgui.same_line()
        # Read once - restart() changes the phase mid-frame, and the push/pop
        # below must stay paired.
        discarding = state.phase != "finished"
        if discarding:
            imgui.push_style_color(imgui.Col_.button.value, _ERROR_COLOR)
        if imgui.button("Stop and restart" if state.phase == "running" else "Restart"):
            state.restart()
            imgui.close_current_popup()
        if discarding:
            imgui.pop_style_color()
        imgui.end_popup()
    imgui.separator()


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
    if vs.fixed_values:
        lines.append("Set to: " + ", ".join(f"{k} = {v:g}" for k, v in vs.fixed_values.items()))
    if vs.excluded_fields:
        lines.append(f"Kept fixed: {sorted(vs.excluded_fields)}")
    if spec.favourite_metric:
        lines.append(f"Pushed as far as possible: {spec.favourite_metric}")
    return lines


_PLAN_TABLE_H = 200.0
_PLAN_HIGHLIGHT_COLOR = imgui.ImVec4(0.30, 0.50, 0.92, 0.16)  # translucent accent tint - the row a target lands on
_METRIC_UNITS = dict(RESULT_FIELDS)


def _baseline_result(state: AgentPanelState, distance: float) -> dict | None:
    run = state.baseline_runs.get(distance)
    if isinstance(run, dict):
        return run.get("result")
    return run.result if run is not None else None


def _plan_metric_rows(spec) -> list[str]:
    """SIM_RESULT_METRICS (always shown, so the whole baseline solve stays
    visible) plus any objective on a derived quantity (outer diameter) not
    already in that list - an objective never goes missing from the table."""
    from agent.input_chat.models.objective import DERIVED_QUANTITIES, SIM_RESULT_METRICS

    objective_metrics = {o.metric for o in spec.objectives}
    return list(SIM_RESULT_METRICS) + [m for m in DERIVED_QUANTITIES if m in objective_metrics]


def _draw_plan_metrics_table(state: AgentPanelState) -> None:
    """The baseline's own 12-metric solve, with each objective's target
    highlighted on its row - what needs to change read off real numbers,
    not the model's free-text restatement of the plan."""
    from agent.input_chat.models.objective import SIM_RESULT_METRICS
    from agent.femm_agent.services.scorer import coil_diameters

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
                # Not "keep as-is": nothing in scoring/ranking (or the search prompt) holds
                # a metric with no objective near baseline - it's simply unconstrained and
                # free to land wherever the search's other changes happen to put it.
                imgui.text_disabled("No requirement")
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
    imgui.dummy(imgui.ImVec2(0, 10))

    # Full-width primary action with the hint centered beneath it, instead of
    # a fixed-size pill with the hint squeezed onto its right.
    imgui.push_style_var(imgui.StyleVar_.frame_rounding.value, 10.0)
    imgui.push_style_color(imgui.Col_.button.value, _ACCENT_COLOR)
    imgui.push_style_color(imgui.Col_.button_hovered.value, _ACCENT_HOVER_COLOR)
    imgui.begin_disabled(state.planning_blocked or bool(state.plan_problems))
    if imgui.button("Start Search", imgui.ImVec2(avail_width, _START_BUTTON_H)):
        state.confirm_plan()
    imgui.end_disabled()
    imgui.pop_style_color(2)
    imgui.pop_style_var()
    if state.plan_problems:
        # Fixed, code-written text: the plan no longer fits the Search fields table.
        imgui.push_text_wrap_pos(avail_width)
        imgui.text_colored(_ERROR_COLOR, "This plan can't start with the Search fields as they are now:")
        for problem in state.plan_problems:
            imgui.bullet_text(problem)
        imgui.pop_text_wrap_pos()
        if imgui.small_button("Check again"):
            state.recheck_plan()
        imgui.same_line()
        imgui.text_disabled("after changing Settings > Search fields - or send a message to change the plan")

    hint = "or send another message to adjust the plan first"
    hint_w = imgui.calc_text_size(hint).x
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + max((avail_width - hint_w) / 2.0, 0.0))
    imgui.text_disabled(hint)
    imgui.dummy(imgui.ImVec2(0, 6))


_START_BUTTON_H = 38.0

# What the model is told after the user clicks Apply on a proposed Search fields
# change - fixed text, sent as the user's turn so the model re-plans.
FIELD_CHANGE_APPLIED_MESSAGE = "I applied the Search fields change. Carry on with the plan."


def _field_change_card_height(pending: dict) -> float:
    return (len(pending["changes"]) + 3) * imgui.get_text_line_height_with_spacing() + 44.0


def _draw_field_change_card(state: AgentPanelState, avail_width: float, pending: dict) -> None:
    """A change to the Search fields table the AI proposed. It is only a
    proposal: nothing is written until the user clicks Apply. The change lines
    are built by code from the validated proposal; only `reason` is the model's."""
    from agent.input_chat.models.variables import describe_field_change
    imgui.separator()
    imgui.text_colored(_ACCENT_COLOR, "Proposed change to Search fields")
    imgui.push_text_wrap_pos(avail_width)
    if pending.get("reason"):
        imgui.text_wrapped(agent_candidate_card.full_text(pending["reason"]))
    for change in pending["changes"]:
        imgui.bullet_text(describe_field_change(change))
    imgui.text_disabled("Applies to every design, not just this one. Nothing changes unless you click Apply.")
    imgui.pop_text_wrap_pos()
    imgui.begin_disabled(state.interview_runner.busy)
    if imgui.button("Apply", imgui.ImVec2(110, 0)):
        state.apply_field_change()
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("Dismiss", imgui.ImVec2(110, 0)):
        state.dismiss_field_change()
    imgui.dummy(imgui.ImVec2(0, 4))


_INPUT_HEIGHT = 64.0  # a real multi-line text area, not a single-line box

_BLOCKED_NOTICE_H = 96.0


def _draw_baseline_blocked_notice(state: AgentPanelState, avail_width: float, error: str) -> None:
    """Fixed, code-written message (never the model's) shown in place of a
    working chat while the baseline can't be simulated."""
    imgui.push_text_wrap_pos(avail_width)
    imgui.text_colored(_ERROR_COLOR, "This baseline can't be simulated, so there is nothing to plan against.")
    imgui.text_wrapped(error)
    imgui.text_disabled("Fix it in Manual Runs or pick a different baseline, then retry.")
    imgui.pop_text_wrap_pos()
    if imgui.button("Retry solve"):
        state.retry_baseline()


def _draw_chat_input_bar(state: AgentPanelState, avail_width: float) -> None:
    """One rounded bar: the multi-line text area. Enter sends - there is no
    send button."""
    imgui.push_style_var(imgui.StyleVar_.frame_rounding.value, 12.0)
    imgui.push_style_var(imgui.StyleVar_.frame_padding.value, imgui.ImVec2(14.0, 10.0))

    text_w = max(avail_width, 80.0)

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
        hint = {"running": "Ask about the search while it runs...",
                "finished": 'Ask about this run, or type "continue" to search on...'}.get(state.phase, "Describe your objective and requirements...")
        draw_list.add_text(imgui.ImVec2(box_pos.x + 14, box_pos.y + 10), imgui.get_color_u32(_INPUT_HINT_COLOR),
                           f"{hint} (Enter to send, Ctrl+Enter for a new line)")

    imgui.push_style_color(imgui.Col_.frame_bg.value, _TRANSPARENT)
    imgui.push_style_color(imgui.Col_.border.value, _TRANSPARENT)
    imgui.set_next_item_width(text_w)
    # ctrl_enter_for_new_line: plain Enter submits (detected below: the
    # multi-line widget has no EnterReturnsTrue equivalent, Enter just makes
    # it lose focus), Ctrl+Enter
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
    # Losing focus alone is NOT a send - clicking anywhere else in the window
    # does that too, and used to send a half-typed message. Only a
    # deactivation caused by Enter itself (pressed this same frame) counts.
    # is_item_deactivated(), not ..._after_edit(): click away, click back in
    # and press Enter without typing more must still send.
    enter_pressed = imgui.is_key_pressed(imgui.Key.enter) or imgui.is_key_pressed(imgui.Key.keypad_enter)
    submitted_via_enter = imgui.is_item_deactivated() and enter_pressed
    # Read after the widget, used by next frame's placeholder (drawn before it).
    state.chat_input_active = imgui.is_item_active()
    draw_list.add_rect(box_pos, box_max,
                       imgui.get_color_u32(_ACCENT_COLOR if state.chat_input_active else _INPUT_BORDER_COLOR),
                       12.0, 1.5)

    imgui.pop_style_var(2)
    imgui.set_cursor_screen_pos(imgui.ImVec2(box_pos.x, box_max.y))

    if submitted_via_enter:
        state.send_chat()


_BASELINE_DIAGRAM_H = 200.0
_BASELINE_RUNNING_H = 420.0  # the box's height while Search Progress shares the column
_BASELINE_PICKER_ONLY_H = 46.0  # just the picker row, above a finished search's summary
_RESULTS_COL_W = 330.0


def _draw_one_baseline_result(distance: float, run) -> None:
    imgui.text(f"Simulated at {distance:g} mm")
    if isinstance(run, dict):  # a cache hit - already done
        imgui.text_disabled("(cached result)")
        draw_result_table(run["result"], table_id=f"agent_baseline_result_{distance:g}")
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
    draw_result_table(run.result, table_id=f"agent_baseline_result_{distance:g}")


def _draw_baseline_results(state: AgentPanelState) -> None:
    """The baseline's own solve at every requested distance - a spinner
    while FEMM runs each one, then the same 12-quantity table as a manual
    run's detail view. One block per distance, stacked (scrolls within the
    box's own child window if there are several)."""
    for i, distance in enumerate(sorted(state.baseline_runs)):
        if i > 0:
            imgui.separator()
        _draw_one_baseline_result(distance, state.baseline_runs[distance])


def _draw_baseline_inputs(buffer: dict, limits: dict[str, str]) -> None:
    """The baseline's parameters, read-only - the same sections and labels
    as the manual-mode config form. Editing stays in Manual Runs. `limits`
    (AgentPanelState.field_limits()) says, for each field the search may
    change, what it may be set to; a field not in it is closed."""
    labels = field_layout.field_labels()
    groups = [(g, names) for g, names in field_layout.FIELD_GROUPS.items() if g != "General"]
    groups.append(("Advanced", list(field_layout.ADVANCED_FIELDS)))
    for group_name, names in groups:
        flags = imgui.TreeNodeFlags_.default_open.value if group_name in ("Loop", "Tank", "RC") else 0
        if not imgui.collapsing_header(f"{group_name}##baseline_{group_name}", flags=flags):
            continue
        if imgui.begin_table(f"baseline_inputs_{group_name}", 3,
                             imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value):
            imgui.table_setup_column("Parameter", imgui.TableColumnFlags_.width_stretch.value)
            imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_fixed.value, 90.0)
            imgui.table_setup_column("Search may set", imgui.TableColumnFlags_.width_fixed.value, 150.0)
            imgui.table_headers_row()
            for name in names:
                value = buffer.get(name)
                if name in field_layout.PRESENCE_FIELDS:
                    value = "yes" if value else "no"
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(labels.get(name, name))
                imgui.table_next_column()
                imgui.text(agent_candidate_card._fmt(value))
                imgui.table_next_column()
                if name in limits:
                    imgui.text(limits[name])
                else:
                    imgui.text_disabled("fixed")
            imgui.end_table()


_BASELINE_POPUP_ID = "confirm_baseline_change"


def _draw_baseline_change_confirmation(state: AgentPanelState) -> None:
    # begin_popup_modal() returns a tuple - unpack it (see configs_panel.py).
    is_open, _ = imgui.begin_popup_modal(_BASELINE_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if not is_open:
        return
    if state.pending_baseline_id is None:
        imgui.close_current_popup()
        imgui.end_popup()
        return
    new_name = next((c["name"] for c in state.configs if c["id"] == state.pending_baseline_id),
                    state.pending_baseline_id)
    old_name = next((c["name"] for c in state.configs if c["id"] == state.selected_config_id),
                    state.selected_config_id)
    imgui.text(f"Switch baseline from {old_name!r} to {new_name!r}?")
    imgui.text_disabled("This removes the current chat - it won't be saved to History.")
    imgui.separator()
    if imgui.button("Cancel") or imgui.is_key_pressed(imgui.Key.escape):
        state.cancel_baseline_change()
        imgui.close_current_popup()
    imgui.same_line()
    imgui.push_style_color(imgui.Col_.button.value, _ERROR_COLOR)
    if imgui.button("Clear chat and switch"):
        state.confirm_baseline_change()
        imgui.close_current_popup()
    imgui.pop_style_color()
    imgui.end_popup()


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
    locked = state.baseline_locked
    imgui.begin_disabled(locked)
    changed, new_index = imgui.combo("##baseline_combo", current_index, names)
    imgui.end_disabled()
    if locked and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled.value):
        imgui.set_tooltip("Locked for this task - click Restart in the chat to change the baseline."
                          if state.run_state is not None else "Wait for the reply to finish.")
    if changed and not locked and state.request_baseline(ids[new_index]):
        imgui.open_popup(_BASELINE_POPUP_ID)
    _draw_baseline_change_confirmation(state)

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
        _draw_baseline_inputs(buffer, state.field_limits())
        imgui.end_child()
    imgui.end_child()


def draw_chat(state: AgentPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_chat_pane", size, imgui.ChildFlags_.borders.value)
    avail = imgui.get_content_region_avail()

    if state.interview_state is None:
        _draw_no_config_state(state, avail)
        imgui.end_child()
        return

    _draw_chat_header(state)
    avail = imgui.get_content_region_avail()

    running = state.phase == "running"
    status_h = imgui.get_frame_height_with_spacing() if running else 0.0
    show_plan = state.interview_state.finalized and not state.plan_confirmed
    # Context lines + the fixed-height, independently-scrolling metrics
    # table (_PLAN_TABLE_H) + header/button chrome (~80px).
    plan_h = (110.0 + len(_plan_context_lines(state.interview_state.spec)) * imgui.get_text_line_height_with_spacing()
              + _PLAN_TABLE_H
              + ((len(state.plan_problems) + 2) * imgui.get_text_line_height_with_spacing() if state.plan_problems else 0.0)
              if show_plan else 0.0)
    pending_change = state.pending_field_change
    plan_h += _field_change_card_height(pending_change) if pending_change else 0.0
    input_h = _INPUT_HEIGHT
    blocked_error = state.baseline_error if state.planning_blocked else None
    notice_h = _BLOCKED_NOTICE_H if blocked_error else 0.0
    feed_h = max(avail.y - input_h - plan_h - status_h - notice_h - 8.0, 60.0)

    _draw_chat_feed(state, imgui.ImVec2(avail.x, feed_h))

    if pending_change:
        _draw_field_change_card(state, avail.x, pending_change)

    if show_plan:
        _draw_plan_summary(state, avail.x)

    if blocked_error:
        _draw_baseline_blocked_notice(state, avail.x, blocked_error)

    if running:
        # The "running..." status, pinned where it's always visible - the
        # feed's own marker scrolls away once follow-up questions pile up.
        imspinner.spinner_ang("##agent_search_spinner", 8.0, 2.5)
        imgui.same_line()
        imgui.text_colored(_ACCENT_COLOR, _running_status_text(state.run_state))

    # Only disabled while a reply is being generated - the chat stays open
    # through the search and after it (follow-up questions, agent/femm_agent/services/followup.py).
    imgui.begin_disabled(state.interview_runner.busy or blocked_error is not None)
    _draw_chat_input_bar(state, avail.x)
    imgui.end_disabled()

    if state.interview_state.error:
        imgui.text_colored(_ERROR_COLOR, state.interview_state.error)
    imgui.end_child()


def _round_header(r) -> str:
    """"Round 7 [done]", plus which attempt it belongs to once the search has
    restarted from zero memory (agent/femm_agent/services/runner.py)."""
    header = f"Round {r.round_num} [{r.status.value}]"
    if r.search_attempt > 1:
        header += f" - restart {r.search_attempt - 1}, round {r.attempt_round} of the new attempt"
    return header


def _round_timing_text(r, settings) -> str:
    """Splits a round's wall-clock time into "waiting on the LLM" vs.
    "waiting on FEMM solves" - real, per-round data for a real question
    ("which is slower, the AI or xfemm?") that had no logged answer
    before. now() (agent/femm_agent/models/state.py) is UTC-aware throughout, so subtraction
    is always safe even across the background-thread boundary."""
    from datetime import datetime, timezone

    if r.evaluating_started_at is None:
        if r.done_at is not None:
            return ""  # no tool call this round - nothing to time-split
        elapsed = (datetime.now(timezone.utc) - r.proposing_started_at).total_seconds()
        try_text = f", try {r.ai_try} of {settings.round_llm_attempts}" if r.ai_try > 1 else ""
        return f"[AI thinking: {elapsed:.0f}s so far, limit {settings.llm_time_limit_s:.0f}s{try_text}]"
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

    if run_state.status.value != "running" and run_state.outcome_report:
        agent_candidate_card.draw_outcome_report(run_state.outcome_report)
        imgui.separator()

    imgui.begin_child("agent_progress_feed", imgui.get_content_region_avail(), 0)
    for r in run_state.rounds:
        agent_candidate_card.draw_round_block(
            f"live_round_{r.round_num}", _round_header(r),
            _round_timing_text(r, run_state.spec.settings), r.reasoning_text,
            [dataclasses.asdict(c) for c in r.candidates],
            baseline_buffer, spec_summary, distance,
            finished=r.status.value == "done", failures=r.llm_failures,
        )
    # Only re-pin to the bottom if already there.
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
    from agent.femm_agent.services import fragility
    from agent.input_chat.models.variables import allowed_fields, audit_changed_fields, search_field_table

    run_state = state.run_state
    best = run_state.best
    # Diffs EVERY field, not just the free ones - a free_fields-filtered diff
    # could never show a change outside the allowed set.
    audit = audit_changed_fields(run_state.baseline_coilset, best.coilset,
                                 allowed_fields(run_state.baseline_coilset, run_state.spec.variable_spec,
                                                search_field_table(run_state.spec.settings)))

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
        dataclasses.asdict(best.coilset), fragility.best_result(run_state), spec_summary,
        distances, state.summary_distance, audit,
        baseline_coilset=run_state.baseline_coilset, best_coilset=best.coilset,
        report=run_state.outcome_report,
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
                f"live_summary_r{r.round_num}", _round_header(r),
                _round_timing_text(r, run_state.spec.settings), r.reasoning_text,
                [dataclasses.asdict(c) for c in r.candidates],
                baseline_buffer, spec_summary, round_distance,
                finished=r.status.value == "done", failures=r.llm_failures,
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
