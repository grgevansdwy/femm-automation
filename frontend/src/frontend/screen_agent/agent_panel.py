"""AI Agent mode: a sidebar of saved chats (agent_sidebar.py), the open
chat in the middle - an interview that gathers an objective, then follow-up
questions once the search has started - and on the right that chat's
baseline, live search progress or results. Every chat is its own
ChatSession, so a search keeps running in the background while another
chat is read or planned; only one search runs at a time. The chat stays
open after the search starts - messages go to
agent/femm_agent/services/followup.py. While the search runs it only answers
questions. Once it has ended the user can also change the goal there: the
revised plan is shown as a plan card with what it changes, and Start Search
runs it as a new segment of the same run (the baseline never changes; "+ New"
opens another chat) - the toggle-mode counterpart to configs_panel/runs_panel's
manual-run dashboard (see main.py's toolbar toggle, _draw_agent_mode()).

Reuses coil_diagram.draw() verbatim for the live baseline view and the
finished-search summary's best-vs-baseline comparison, and
agent_candidate_card.draw_round_block() for the round-by-round search feed
(geometry + input/output tables + short reasoning per candidate - shared
with agent_sidebar.py's stored-run replay) while the search runs. Only ever
calls into backend/agent's entry points (InterviewRunner, AgentRunner,
restore.py for a chat reopened from the database) - never reimplements
orchestration here.
"""

import dataclasses
import json
import threading
import time

from imgui_bundle import imgui, imspinner

from ..services import field_layout
from ..services.job_runner import now
from ..visualization import coil_diagram
from ..visualization.result_table import RESULT_FIELDS, draw_result_table
from . import agent_candidate_card, agent_sidebar

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
# The baseline's own solve (and its diagram) - before the interview there's
# no chosen distance yet, so the manual-run default.
BASELINE_DISTANCE = 5.0


@dataclasses.dataclass
class ChatSession:
    """One conversation and everything that belongs to it. AgentPanelState
    shows one at a time (its `chat`) and keeps the others it has touched, so
    a search goes on while another chat is on screen."""
    interview_runner: object  # agent.input_chat.services.runner.InterviewRunner - this chat's own
    selected_config_id: str | None = None
    baseline_coilset: object = None  # the picked config, read once at selection
    # distance (mm) -> its own solve at that distance: a cached result
    # row (dict, already done) or a live JobRunner Job still solving.
    # Starts as {BASELINE_DISTANCE: ...} the moment a config is picked
    # (before any objective is known); _sync_baseline_distances() then
    # replaces it with exactly spec.distances the moment a plan is
    # finalized - dropping BASELINE_DISTANCE if it wasn't asked for.
    baseline_runs: dict = dataclasses.field(default_factory=dict)
    synced_distances: tuple | None = None  # poll()'s guard against resolving every frame
    interview_state: object = None  # agent.input_chat.services.interview.InterviewState, created the moment a config is picked
    run_state: object = None        # agent.state.AgentRunState, once the plan is confirmed
    # The model calling finalize_spec proposes a plan - it does NOT
    # start the search. plan_confirmed is the explicit human go-ahead
    # (the "Start Search" button on the rendered plan); poll() only
    # hands off to AgentRunner once both are true.
    plan_confirmed: bool = False
    # Why the proposed plan can't start under the Search fields table as it is
    # now (checked when Start Search is clicked - see confirm_plan()).
    plan_problems: list = dataclasses.field(default_factory=list)
    chat_input: str = ""  # the unsent draft - each chat keeps its own
    error: str = ""
    # Which distance the finished-search Search Summary's Result table
    # is showing - a dropdown lets the user switch (draw_search_summary()
    # falls back to spec.distances[0] whenever this isn't one of them,
    # e.g. still None on the very first draw).
    summary_distance: float | None = None
    # Where in the chat transcript each search segment was started (Start
    # Search, then each "continue") - the chat feed draws a search-status
    # line there, so follow-up questions appear below it. The last one is
    # the live segment's.
    search_markers: list = dataclasses.field(default_factory=list)
    # A chat opened from the sidebar: its stored row and messages as they
    # were when it was opened. The right column is drawn from these until a
    # search starts or continues in this session (`live`).
    record: dict | None = None
    messages: list = dataclasses.field(default_factory=list)
    live: bool = True
    # Why this chat can only be read (its baseline or plan can't be rebuilt), in one line.
    read_only_reason: str | None = None
    restoring: bool = False  # its run is being rebuilt from the database, for a question or a continue

    @property
    def ai_run_id(self) -> str | None:
        """None until the user's first message - an empty chat has no row."""
        return self.interview_runner.ai_run_id

    @property
    def search_started(self) -> bool:
        return self.run_state is not None or bool(self.record and self.record.get("started_at"))

    @property
    def phase(self) -> str:
        """"none" (no baseline picked), "interview" (planning), "running"
        (search in progress) or "finished" (search ended, any outcome)."""
        if self.interview_state is None:
            return "none"
        if self.run_state is not None:
            return "running" if self.run_state.status.value == "running" else "finished"
        return "finished" if self.search_started else "interview"

    @property
    def revised_plan(self):
        """A plan the user asked for after this chat's search ended, accepted by
        code and not started yet (agent/femm_agent/services/followup.py) - or
        None. interview_state.spec holds it; run_state.spec stays the plan the
        finished search ran under until Start Search."""
        state = self.interview_state
        if state is None or state.spec is None or not self.search_started:
            return None
        run = self.run_state
        if run is None:
            # Reopened from the sidebar and not rebuilt yet: the row says so.
            return state.spec if (self.record or {}).get("status") == "plan_ready" else None
        if run.status.value == "running":
            return None
        # A run rebuilt for a waiting revised plan already carries it (restore_run()).
        return state.spec if run.status.value == "plan_ready" or state.spec != run.spec else None

    @property
    def plan_in_force(self):
        """The plan the finished search ran under, to show what a revised plan
        changes - None when it is not known here (a chat reopened with its
        revised plan already waiting: the row holds only the new one)."""
        run = self.run_state
        return run.spec if run is not None and run.status.value != "plan_ready" else None

    @property
    def title(self) -> str:
        """The same name the sidebar lists it under."""
        state = self.interview_state
        if state is None:
            return "New chat"
        text = state.spec.objective_text if state.spec is not None else ""
        text = text or next((m.get("content") or "" for m in state.transcript if m.get("role") == "user"), "")
        return " ".join(text.split()) or "New chat"


def _chat_field(name: str) -> property:
    """AgentPanelState.<name> reads and writes the open chat's <name>."""
    return property(lambda self: getattr(self.chat, name), lambda self, value: setattr(self.chat, name, value))


class AgentPanelState:
    def __init__(self, job_runner) -> None:
        from agent.femm_agent.services.runner import AgentRunner

        self.job_runner = job_runner
        self.agent_runner = AgentRunner(job_runner)
        self.sidebar = agent_sidebar.AgentSidebarState()
        self.configs: list[dict] = []
        self.chat_input_active: bool = False
        # run id -> every saved chat touched in this session. Opening one again
        # returns the same object, so a running search is found as it is now.
        self._sessions: dict[str, ChatSession] = {}
        self._field_limits_cache: tuple | None = None
        self.chat = self._empty_chat()  # the chat on screen
        self.refresh_configs()

    interview_runner = _chat_field("interview_runner")
    selected_config_id = _chat_field("selected_config_id")
    baseline_coilset = _chat_field("baseline_coilset")
    baseline_runs = _chat_field("baseline_runs")
    interview_state = _chat_field("interview_state")
    run_state = _chat_field("run_state")
    plan_confirmed = _chat_field("plan_confirmed")
    plan_problems = _chat_field("plan_problems")
    chat_input = _chat_field("chat_input")
    error = _chat_field("error")
    summary_distance = _chat_field("summary_distance")
    search_markers = _chat_field("search_markers")

    @staticmethod
    def _empty_chat() -> ChatSession:
        from agent.input_chat.services.runner import InterviewRunner
        return ChatSession(interview_runner=InterviewRunner())

    @property
    def phase(self) -> str:
        return self.chat.phase

    @property
    def running_chat(self) -> ChatSession | None:
        """The chat whose search is running, or None. One search at a time:
        searches share the AI server (which answers one request at a time)
        and the solver."""
        for chat in self._all_chats():
            if chat.run_state is not None and chat.run_state.status.value == "running":
                return chat
        return None

    @property
    def search_busy_elsewhere(self) -> str | None:
        """Why this chat can't start or continue a search right now - another
        chat's search is running - or None."""
        other = self.running_chat
        if other is None or other is self.chat:
            return None
        return f"Another search is running ({other.title}). Wait for it to end, or open it and stop it."

    @property
    def baseline_locked(self) -> bool:
        """The baseline can't be changed once a search has started ("+ New"
        opens a chat on another one), nor mid-reply."""
        chat = self.chat
        return chat.search_started or chat.interview_runner.busy or chat.restoring

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
        return not self.chat.search_started and self.baseline_error is not None

    def retry_baseline(self) -> None:
        """Re-queues every failed baseline solve. A failed solve is never a
        cache hit, so this really solves again (a crash or timeout can clear;
        invalid geometry won't, and fails again with the same message)."""
        for distance, run in list(self.baseline_runs.items()):
            if not isinstance(run, dict) and run.status.value == "failed":
                self.baseline_runs[distance] = self._solve_one(self.chat, distance)

    def _all_chats(self) -> list[ChatSession]:
        chats = list(self._sessions.values())
        if all(chat is not self.chat for chat in chats):
            chats.append(self.chat)
        return chats

    def _keep_current(self) -> None:
        """Called before another chat takes the screen: a chat the user has
        written in stays reachable (and alive) under its run id; an empty one
        has no row and is simply dropped."""
        if self.chat.ai_run_id is not None:
            self._sessions[self.chat.ai_run_id] = self.chat

    def poll(self) -> None:
        """Called once per frame from main.py, alongside job_runner.poll().
        Every chat is polled, not just the open one - a plan confirmed in a
        chat still hands off to its search after the user has clicked away."""
        self.agent_runner.poll()
        self._keep_current()
        for chat in self._all_chats():
            self._poll_chat(chat)

    def _poll_chat(self, chat: ChatSession) -> None:
        chat.interview_runner.poll()
        state = chat.interview_state
        if state is None or not state.finalized:
            return
        # Re-sync only on an actual change of distances (comparing a
        # tuple, not calling sync unconditionally) - _solve_one() would
        # otherwise re-submit() a brand new duplicate job every single
        # frame while a solve is still in flight (find_cached() can't
        # see it yet - it isn't persisted until the job finishes).
        distances = tuple(state.spec.distances)
        if distances != chat.synced_distances:
            self._sync_baseline_distances(chat, distances)
            chat.synced_distances = distances
        if chat.search_started:
            # After a search the plan card is shown exactly while a revised plan
            # waits; starting it is immediate (confirm_plan()), never this hand-off.
            chat.plan_confirmed = chat.revised_plan is None
            return
        if chat.plan_confirmed and chat.run_state is None and self.running_chat is None:
            chat.run_state = self.agent_runner.submit(state.spec, ai_run_id=chat.ai_run_id)
            chat.live = True
            self.sidebar.refresh()

    def refresh_configs(self) -> None:
        from engine.repositories.coilset_repository import list_configs
        self.configs = list_configs()

    # --- which chat is on screen ---------------------------------------------------------------

    def new_chat(self, config_id: str | None = None) -> None:
        """Opens an empty chat on `config_id` (default: the baseline already
        selected), ready to type into - no separate "Start" click, no opening
        LLM call, and nothing saved until the first message
        (InterviewRunner.start()). The chat that was open is left exactly as
        it is: it stays in the sidebar, a search in it keeps running, and a
        planning chat can be picked up again later."""
        from engine.repositories.coilset_repository import get_coilset

        config_id = config_id or self.chat.selected_config_id
        previous = self.chat
        self._keep_current()
        chat = self._empty_chat()
        if previous.ai_run_id is None:
            chat.chat_input = previous.chat_input  # an unsent draft moves along with its empty chat
        self.chat = chat
        if config_id is None:
            return  # nothing picked yet: the "Pick a baseline" screen
        try:
            chat.baseline_coilset = get_coilset(config_id)
        except KeyError:
            return  # that config was deleted since - pick another
        chat.selected_config_id = config_id
        # No objective (hence no distances) is known yet - a single solve at
        # the manual-run default, replaced by _sync_baseline_distances() the
        # moment a plan is finalized with the user's real distances.
        chat.baseline_runs = {BASELINE_DISTANCE: self._solve_one(chat, BASELINE_DISTANCE)}
        chat.interview_state = chat.interview_runner.start(config_id)

    def select_config(self, config_id: str) -> None:
        """Picking a baseline opens a new chat on it (new_chat())."""
        self.new_chat(config_id)

    def request_baseline(self, config_id: str) -> None:
        """The picker's entry point. A chat's baseline never changes under
        it: picking another one opens a new chat on that baseline, and the
        chat that was open - if anything was said in it - stays in the sidebar."""
        if config_id == self.selected_config_id or self.baseline_locked:
            return
        self.new_chat(config_id)

    def open_chat(self, run_id: str) -> None:
        """Shows a saved chat: the live one if it was touched in this
        session (a search still running included), else rebuilt from the
        database. Opening never starts a search and never simulates a
        finished run's designs again."""
        if run_id == self.chat.ai_run_id:
            return
        self._keep_current()
        chat = self._sessions.get(run_id) or self._load_chat(run_id)
        if chat is None:
            self.sidebar.refresh()  # the row is gone
            return
        self._sessions[run_id] = chat
        self.chat = chat

    def _load_chat(self, run_id: str) -> ChatSession | None:
        from agent.femm_agent.services.restore import RestoreError, restore_interview, stored_baseline
        from agent.repositories.ai_run_repository import get_ai_run, list_messages

        record = get_ai_run(run_id)
        if record is None:
            return None
        messages = list_messages(run_id)
        chat = self._empty_chat()
        chat.record, chat.messages, chat.live = record, messages, False
        chat.selected_config_id = record["baseline_config_id"]
        problem = None
        try:
            chat.interview_state = restore_interview(record, messages)
        except RestoreError as exc:
            problem = exc
            chat.interview_state = restore_interview({**record, "spec": None}, messages)  # the conversation alone
        try:
            chat.baseline_coilset = stored_baseline(record)
        except RestoreError as exc:
            problem = problem or exc
        # The runner is attached either way, so the sidebar knows which row this chat is.
        chat.interview_runner.attach(run_id, chat.interview_state)
        if chat.search_started:
            chat.search_markers = _stored_search_markers(record, messages)
        if problem is not None:
            chat.read_only_reason = f"This chat can only be read: {problem}."
            return chat
        if record.get("imported_at") and not chat.search_started:
            # Planning goes on against a config in this machine's library, and
            # the one this chat names lives on another machine.
            chat.read_only_reason = ("This chat can only be read: it was imported before its search started. "
                                     "Start a new chat to plan on this machine.")
            return chat
        if not chat.search_started:
            # Still planning: the same baseline solve picking a config starts.
            chat.baseline_runs = {BASELINE_DISTANCE: self._solve_one(chat, BASELINE_DISTANCE)}
        return chat

    def delete_chat(self, run_id: str) -> bool:
        """The explicit user action that removes a chat: its row, its
        conversation and its search rounds. A search still running is never
        deleted (stop it first). False if nothing was deleted."""
        from agent.repositories.ai_run_repository import delete_ai_run, get_ai_run

        chat = next((c for c in self._all_chats() if c.ai_run_id == run_id), None)
        record = get_ai_run(run_id)
        if record is None or record["status"] == "running" or (
                chat is not None and (chat.phase == "running" or chat.restoring)):
            self.sidebar.refresh()
            return False
        if chat is not None:
            chat.interview_runner.discard()  # a reply still in flight writes nothing more
        self._sessions.pop(run_id, None)
        self.agent_runner.runs.pop(run_id, None)
        delete_ai_run(run_id)
        if chat is self.chat:
            config_id = chat.selected_config_id
            self.chat = self._empty_chat()
            self.new_chat(config_id)
        self.sidebar.refresh()
        return True

    # --- the open chat ---------------------------------------------------------------------------

    def _solve_one(self, chat: ChatSession, distance: float):
        """Same cache-first path as a manual run (runs_panel.submit_run()):
        reuse an identical earlier solve, otherwise queue one on the shared
        JobRunner - never blocks the frame either way."""
        cached = self.job_runner.find_cached(chat.baseline_coilset, distance)
        if cached is not None:
            return self.job_runner.touch(cached)
        name = next((c["name"] for c in self.configs if c["id"] == chat.selected_config_id), chat.selected_config_id)
        return self.job_runner.submit(chat.baseline_coilset, config_id=chat.selected_config_id, config_name=name,
                                      distance=distance)

    def _sync_baseline_distances(self, chat: ChatSession, distances: tuple) -> None:
        """Keeps baseline_runs solving exactly the distances a finalized
        plan asks for: drops any solve (e.g. the initial 5mm default) the
        user didn't actually ask for, and starts one for any new distance -
        so the Baseline box always shows real numbers at the distances that
        matter, never a stale/irrelevant one."""
        wanted = set(distances)
        chat.baseline_runs = {d: run for d, run in chat.baseline_runs.items() if d in wanted}
        for d in distances:
            if d not in chat.baseline_runs:
                chat.baseline_runs[d] = self._solve_one(chat, d)

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
        while planning, or after a search has ended (for a revised plan). A
        running search's table is fixed."""
        if self.interview_state is None or self.chat.phase == "running" or self.chat.read_only_reason:
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
        if self.chat.search_started:
            # After a search: the same fixed message, answered by the follow-up
            # model, which may now send the revised plan (built on the saved table).
            self._send_followup(self.chat, FIELD_CHANGE_APPLIED_MESSAGE)
            return
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
        if self.planning_blocked or self.chat.read_only_reason:
            return  # the Start Search button is disabled too - this covers any other caller
        busy = self.search_busy_elsewhere
        if busy:
            self.error = busy
            return
        if self.chat.search_started:
            self._confirm_revised_plan()
            return
        self.error = ""
        if self.recheck_plan():
            return  # shown on the plan, which stays unconfirmed
        self.plan_confirmed = True
        self.search_markers = [len(self.interview_state.transcript)]

    def revised_plan_blocked(self) -> str | None:
        """Why a waiting revised plan can't be started this moment, in one line
        - None when Start Search may be clicked."""
        chat = self.chat
        if chat.revised_plan is None:
            return "There is no revised plan to start."
        if chat.interview_runner.busy or chat.restoring:
            return "Wait for the reply in progress."
        for distance in chat.revised_plan.distances:
            run = chat.baseline_runs.get(distance)
            if run is None or (not isinstance(run, dict) and run.status.value not in ("done", "failed")):
                return f"The baseline is still being simulated at {distance:g} mm."
            if not isinstance(run, dict) and run.status.value == "failed":
                return f"The baseline could not be simulated at {distance:g} mm: {run.error or 'no reason given'}"
        return self.search_busy_elsewhere

    def revised_plan_notes(self) -> list[str]:
        """What the card of a waiting revised plan says besides the plan itself:
        each change from the plan the finished search ran under, then what the
        new search will start from. Written by code (services/replan.py)."""
        from agent.femm_agent.services import replan
        chat = self.chat
        new, old = chat.revised_plan, chat.plan_in_force
        if new is None:
            return []
        if old is not None:
            changes = replan.plan_diff(old, new) or ["Nothing differs from the plan the search ran under."]
            return [f"Changed: {c}" for c in changes] + [replan.memory_line(old, new, chat.run_state.tried)]
        # Reopened with the revised plan already waiting: the plan it replaces is not stored.
        last = ((chat.record or {}).get("budget") or [{}])[-1]
        fresh = not last.get("distances") or replan.memory_mode(last["distances"], new.distances) == replan.FRESH
        return ["Revised after the search below (the plan it replaces is not shown in a reopened chat).",
                "A distance was added, so this search starts from the beginning." if fresh
                else "Continues from the designs already simulated, re-scored against this plan."]

    def _confirm_revised_plan(self) -> None:
        """Start Search on a revised plan: a new segment of the same run
        (AgentRunner.start_revised()). A chat reopened from the sidebar first
        has its run rebuilt from the database, in the background."""
        chat = self.chat
        blocked = self.revised_plan_blocked()
        if blocked:
            chat.error = blocked
            return
        chat.error = ""
        if self.recheck_plan():
            return  # shown on the plan card, which stays
        if chat.run_state is None:
            self._restore_then(chat, lambda: self._start_revised(chat))
        else:
            self._start_revised(chat)

    def _start_revised(self, chat: ChatSession) -> bool:
        other = self.running_chat
        if other is not None and other is not chat:
            chat.error = "Another search started in the meantime - start this plan when it ends."
            return False
        try:
            self.agent_runner.start_revised(chat.run_state.id, chat.interview_state.spec)
        except Exception as exc:  # ValueError (not startable), or the row refused the change
            chat.error = f"The revised plan could not be started: {exc}"
            return False
        chat.error = ""
        chat.live = True
        chat.plan_confirmed = True
        chat.search_markers.append(len(chat.interview_state.transcript))
        self.sidebar.refresh()
        return True

    def stop_search(self) -> None:
        """ "Stop and keep the best": ends the running search at its next
        checkpoint. The chat stays open on it, with its best design and
        report, and it can be continued."""
        if self.phase == "running":
            self.agent_runner.stop(self.run_state.id)

    def _can_continue(self) -> bool:
        chat = self.chat
        if chat.phase != "finished" or chat.interview_runner.busy or chat.restoring or chat.read_only_reason:
            return False
        busy = self.search_busy_elsewhere
        if busy:
            chat.error = busy
            return False
        return True

    def continue_search(self) -> bool:
        """Searches on from where the finished search stopped: same goal, same
        memory, a fresh budget, rounds appended to the same run
        (AgentRunner.continue_run()). False if there is nothing to continue.
        A chat reopened from the sidebar first has its run rebuilt from the
        database, in the background - True then means the continue is on its way."""
        if not self._can_continue():
            return False
        chat = self.chat
        if chat.run_state is None:
            self._restore_then(chat, lambda: self._continue(chat))
            return True
        return self._continue(chat)

    def _continue(self, chat: ChatSession) -> bool:
        other = self.running_chat
        if other is not None and other is not chat:
            chat.error = "Another search started in the meantime - continue this one when it ends."
            return False
        try:
            self.agent_runner.continue_run(chat.run_state.id)
        except ValueError as exc:
            chat.error = str(exc)
            return False
        chat.error = ""
        chat.live = True
        chat.search_markers.append(len(chat.interview_state.transcript))
        return True

    def _restore_then(self, chat: ChatSession, action) -> None:
        """Rebuilds a reopened chat's search (restore.restore_run(): its
        rounds, everything it tried, its best design - simulations come from
        the cache) on a background thread, hands it to the AgentRunner, then
        runs `action`. If it can't be rebuilt the chat becomes read-only and
        says why."""
        from agent.femm_agent.services.restore import RestoreError, restore_run

        def work():
            try:
                state = restore_run(chat.ai_run_id, self.job_runner)
                self.agent_runner.adopt(state)
                chat.run_state = state
                action()
            except RestoreError as exc:
                chat.read_only_reason = f"This chat can only be read: {exc}."
            except Exception as exc:
                chat.error = f"This run's results could not be loaded: {exc}"
            finally:
                chat.restoring = False

        chat.restoring = True
        threading.Thread(target=work, daemon=True).start()

    def _send_followup(self, chat: ChatSession, text: str) -> None:
        """A message after Start Search (agent/femm_agent/services/followup.py).
        While the search runs it is a question and nothing can change. Once it
        has ended the model may also answer with a revised plan or a Search
        fields proposal - both only shown here, for the user to start or apply."""
        def ask():
            chat.interview_runner.send_followup(chat.interview_state, chat.run_state, text, allow_replan=True)
        if chat.run_state is None:
            self._restore_then(chat, ask)
        else:
            ask()

    def send_chat(self) -> None:
        chat = self.chat
        text = chat.chat_input.strip()
        if not text or chat.interview_state is None or chat.interview_runner.busy or chat.restoring:
            return
        if self.planning_blocked or chat.read_only_reason:
            return  # keeps the typed text - the chat's notice says why
        if chat.phase == "finished" and text.lower() == "continue":
            # Recognised by code, not by a model: the whole message is the
            # word "continue". Recorded in the chat, then the search resumes.
            if not self._can_continue():
                return  # keeps the typed text - chat.error says why
            chat.chat_input = ""
            chat.interview_runner.record_user_message(chat.interview_state, text)
            self.continue_search()
            return
        chat.chat_input = ""
        chat.error = ""
        if chat.search_started:
            self._send_followup(chat, text)
            return
        # A message sent after a plan was already proposed is feedback to
        # revise it, not a confirmation. Clear BOTH plan_confirmed and
        # finalized - the stale plan card must disappear the instant a
        # revision is sent (it was staying on screen through the whole
        # revision exchange, confusingly showing the OLD plan while the
        # user and model discussed a new one), reappearing only once the
        # model calls finalize_spec again with the updated understanding.
        chat.plan_confirmed = False
        chat.interview_state.finalized = False
        chat.plan_problems = []
        chat.interview_runner.send(chat.interview_state, text)


def _stored_search_markers(record: dict, messages: list[dict]) -> list[int]:
    """Where a stored chat's search segments began, as positions in its
    transcript: Start Search (the messages written before the run's
    started_at), then each "continue" the user typed - a message code acted
    on itself, so no reply follows it."""
    interview = sorted((m for m in messages if m["phase"] == "interview"), key=lambda m: m["seq"])
    started_at = record.get("started_at") or ""
    first = sum(1 for m in interview if (m["created_at"] or "") < started_at)
    markers = [first]
    for i, m in enumerate(interview):
        said_continue = m["role"] == "user" and (m["content"] or "").strip().lower() == "continue"
        answered = i + 1 < len(interview) and interview[i + 1]["role"] != "user"
        if i >= first and said_continue and not answered:
            markers.append(i + 1)
    # A revised plan the user started: its segment's budget entry says how its
    # memory began, and its first round's time says where in the chat it started.
    revised = {e.get("segment") for e in record.get("budget") or [] if e.get("memory")}
    rounds = sorted((m for m in messages if m["phase"] == "optimize"), key=lambda m: m["round_num"] or 0)
    for segment in sorted(s for s in revised if s):
        first_round = next((m for m in rounds if (m["payload"] or {}).get("segment") == segment), None)
        if first_round is not None:
            markers.append(sum(1 for m in interview if (m["created_at"] or "") < (first_round["created_at"] or "")))
    return sorted(set(markers))


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

    if state.chat.restoring:
        imgui.text_colored(_THINKING_COLOR, "Loading this run's results...")
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
_STOP_POPUP_ID = "Stop search?"  # also the popup's title bar


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


def _finished_line(status: str, stop_reason: str | None, rounds: int) -> str:
    outcome = _FINISHED_TEXT.get(status, status)
    rule = STOP_REASON_TEXT.get(stop_reason)
    return f"Search finished: {outcome} after {rounds} round(s)" + (f" ({rule})." if rule else ".")


def _finished_status_text(run_state) -> str:
    return _finished_line(run_state.status.value, run_state.stop_reason, len(run_state.rounds))


def _chat_ending(chat: ChatSession) -> tuple[str, str | None]:
    """How the chat's search ended, in one line, and code's report of it -
    from the live run, or for a chat reopened from the sidebar from its
    stored row."""
    if chat.live or chat.record is None:
        return _finished_status_text(chat.run_state), chat.run_state.outcome_report
    record = chat.record
    return (_finished_line(record["status"], record.get("stop_reason"), len(agent_sidebar.stored_rounds(chat.messages))),
            record["error"])


def _marker_text(chat: ChatSession, index: int) -> str:
    """What began at the chat's `index`-th search marker: the first search, a
    "continue", or a revised plan (and whether it kept the memory) - read
    from the segment's budget entry, which says how its memory began."""
    if index == 0:
        return "Search started."
    run = chat.run_state
    entries = list(run.budget_history) if run is not None else list((chat.record or {}).get("budget") or [])
    memory = next((e.get("memory") for e in entries if e.get("segment") == index + 1), None)
    if memory is None and run is not None and run.segment == index + 1:
        memory = run.segment_memory  # the segment in progress has no entry yet
    if memory == "fresh":
        return "Search started on the revised plan, from the beginning (a distance was added)."
    if memory == "kept":
        return "Search started on the revised plan, with everything already tried re-scored against it."
    return "Search continued with a fresh budget."


def _draw_search_marker(state: AgentPanelState, width: float, marker: int) -> None:
    """The point in the chat where a search segment was started (Start
    Search, or a "continue"). For the latest one: while it runs the live
    status is pinned above the input instead (always visible, even scrolled
    up); once it ends this line says how it ended and offers Continue."""
    imgui.separator()
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + width)
    started_text = _marker_text(state.chat, state.search_markers.index(marker))
    if marker != state.search_markers[-1]:
        imgui.text_colored(_ACCENT_COLOR, started_text)
    elif state.phase == "finished":
        ending, report = _chat_ending(state.chat)
        imgui.text_colored(_ACCENT_COLOR, ending)
        if report:
            agent_candidate_card.draw_outcome_report(report)
        if state.chat.read_only_reason:
            # Nothing can be asked or continued here - the notice under the input says why.
            imgui.pop_text_wrap_pos()
            imgui.separator()
            imgui.dummy(imgui.ImVec2(width, _MSG_GAP))
            return
        imgui.text_colored(_PLACEHOLDER_COLOR, 'Ask anything about this run. To search further toward the same goal, '
                                               'reply "continue" or click Continue. To change the goal, say what '
                                               "to change: the revised plan is shown here for you to start.")
        busy_elsewhere = state.search_busy_elsewhere
        chat = state.chat
        imgui.begin_disabled(bool(busy_elsewhere or chat.read_only_reason or chat.restoring or chat.interview_runner.busy))
        if imgui.button(f"Continue##continue_{marker}"):
            state.continue_search()
        imgui.end_disabled()
        if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled.value):
            imgui.set_tooltip(busy_elsewhere or
                              "Keep searching: same goal, everything tried so far kept in memory, a fresh budget.")
    else:
        imgui.text_colored(_ACCENT_COLOR, started_text)
        imgui.text_colored(_PLACEHOLDER_COLOR, "You can keep asking questions while it runs; the baseline and "
                                               "plan are locked.")
    imgui.pop_text_wrap_pos()
    imgui.separator()
    imgui.dummy(imgui.ImVec2(width, _MSG_GAP))


def _draw_chat_header(state: AgentPanelState) -> None:
    """Phase label on the left. On the right: "Stop and keep the best" while
    this chat's search runs, otherwise Delete for a saved chat. A new chat
    is the sidebar's "+ New" - it never closes this one."""
    labels = {"interview": "Planning", "running": "Searching - questions only",
              "finished": "Finished - ask, continue or change the goal"}
    right_edge = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x
    imgui.align_text_to_frame_padding()
    imgui.text_disabled("Read-only" if state.chat.read_only_reason else labels.get(state.phase, ""))
    if state.phase == "running":
        stop_label = "Stop and keep the best"
        stop_w = imgui.calc_text_size(stop_label).x + 20.0
        imgui.same_line(max(right_edge - stop_w, 0.0))
        if imgui.button(stop_label, imgui.ImVec2(stop_w, 0)):
            imgui.open_popup(_STOP_POPUP_ID)
        if imgui.is_item_hovered():
            imgui.set_tooltip("End the search now and keep its best design. You can continue it later.")
    elif state.chat.ai_run_id is not None:
        delete_w = 70.0
        imgui.same_line(max(right_edge - delete_w, 0.0))
        imgui.begin_disabled(state.chat.restoring)
        if imgui.button("Delete", imgui.ImVec2(delete_w, 0)):
            state.sidebar.request_delete(state.chat.ai_run_id)  # the sidebar asks for confirmation
        imgui.end_disabled()
        if imgui.is_item_hovered():
            imgui.set_tooltip("Delete this chat and its search rounds.")
    # begin_popup_modal() returns a tuple - unpack it (see configs_panel.py).
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
_PLAN_CHROME_H = 132.0  # separator, two headings, spacing, Start Search and its hint - measured on screen
_STATUS_SPINNER_W = 30.0  # the running-status spinner and its gap, left of the wrapped text
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
    revised = state.chat.revised_plan is not None
    imgui.separator()
    imgui.text_colored(_ACCENT_COLOR, "Revised plan" if revised else "Proposed plan")
    for line in _plan_context_lines(state.interview_state.spec):
        imgui.push_text_wrap_pos(avail_width)
        imgui.text_wrapped(line)
        imgui.pop_text_wrap_pos()
    # A revised plan: what changed and what the search starts from, written by
    # code - so a change the model made unasked is seen before Start Search.
    for line in state.revised_plan_notes():
        imgui.push_text_wrap_pos(avail_width)
        imgui.text_colored(_PLACEHOLDER_COLOR, line)
        imgui.pop_text_wrap_pos()
    imgui.dummy(imgui.ImVec2(0, 4))
    _draw_plan_metrics_table(state)
    imgui.dummy(imgui.ImVec2(0, 10))

    # Full-width primary action with the hint centered beneath it, instead of
    # a fixed-size pill with the hint squeezed onto its right.
    imgui.push_style_var(imgui.StyleVar_.frame_rounding.value, 10.0)
    imgui.push_style_color(imgui.Col_.button.value, _ACCENT_COLOR)
    imgui.push_style_color(imgui.Col_.button_hovered.value, _ACCENT_HOVER_COLOR)
    busy_elsewhere = state.search_busy_elsewhere
    revised_blocked = state.revised_plan_blocked() if revised else None
    imgui.begin_disabled(state.planning_blocked or bool(busy_elsewhere) or bool(state.plan_problems)
                         or bool(revised_blocked))
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

    hint = ("Another search is running - one at a time" if busy_elsewhere
            else revised_blocked if revised_blocked
            else "or send another message to adjust the plan first")
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
                "finished": 'Ask about this run, type "continue", or say what to change...'}.get(state.phase, "Describe your objective and requirements...")
        # Two lines: on one, the keys ran off the edge of a narrow chat column.
        draw_list.add_text(imgui.ImVec2(box_pos.x + 14, box_pos.y + 10), imgui.get_color_u32(_INPUT_HINT_COLOR), hint)
        draw_list.add_text(imgui.ImVec2(box_pos.x + 14, box_pos.y + 10 + imgui.get_text_line_height_with_spacing()),
                           imgui.get_color_u32(_PLACEHOLDER_COLOR), "Enter to send, Ctrl+Enter for a new line")

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
        imgui.set_tooltip("Locked for this chat - click + New to start one on another baseline."
                          if state.chat.search_started else "Wait for the reply to finish.")
    if changed and not locked:
        state.request_baseline(ids[new_index])  # a chat with messages stays in the sidebar; this opens a new one

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
    # The status line wraps instead of running off the column, so its height depends on its text.
    running_text = _running_status_text(state.run_state) if running else ""
    status_h = (max(imgui.calc_text_size(running_text, wrap_width=max(avail.x - _STATUS_SPINNER_W, 80.0)).y,
                    imgui.get_frame_height()) + imgui.get_style().item_spacing.y) if running else 0.0
    show_plan = state.interview_state.finalized and not state.plan_confirmed
    # Context lines + the fixed-height, independently-scrolling metrics
    # table (_PLAN_TABLE_H) + header/button chrome.
    plan_lines = (len(_plan_context_lines(state.interview_state.spec)) + len(state.revised_plan_notes())) if show_plan else 0
    plan_h = (_PLAN_CHROME_H + plan_lines * imgui.get_text_line_height_with_spacing()
              + _PLAN_TABLE_H
              + ((len(state.plan_problems) + 2) * imgui.get_text_line_height_with_spacing() if state.plan_problems else 0.0)
              if show_plan else 0.0)
    pending_change = state.pending_field_change
    plan_h += _field_change_card_height(pending_change) if pending_change else 0.0
    input_h = _INPUT_HEIGHT
    blocked_error = state.baseline_error if state.planning_blocked else None
    notice_h = _BLOCKED_NOTICE_H if blocked_error else 0.0
    # One line kept free under the input for a notice (read-only reason, an error).
    notes_h = imgui.get_text_line_height_with_spacing() if (
        state.chat.read_only_reason or state.interview_state.error or state.chat.error) else 0.0
    feed_h = max(avail.y - input_h - plan_h - status_h - notice_h - notes_h - 8.0, 60.0)

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
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_ACCENT_COLOR, running_text)
        imgui.pop_text_wrap_pos()

    # Only disabled while a reply is being generated - the chat stays open
    # through the search and after it (follow-up questions, agent/femm_agent/services/followup.py).
    chat = state.chat
    imgui.begin_disabled(state.interview_runner.busy or blocked_error is not None or chat.restoring
                         or chat.read_only_reason is not None)
    _draw_chat_input_bar(state, avail.x)
    imgui.end_disabled()

    for notice in (chat.read_only_reason, state.interview_state.error, chat.error):
        if notice:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(_ERROR_COLOR, notice)
            imgui.pop_text_wrap_pos()
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
    # exactly the way agent_sidebar.py renders a stored one.
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
    the round-by-round feed - the same view agent_sidebar.py replays for a
    stored run (agent_candidate_card.draw_search_summary())."""
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

    # Same collapsed round-by-round detail as the stored replay of this
    # same finished run (agent_sidebar.py's draw_stored_run()) - the summary
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
    picker row above the Search Summary. A chat reopened from the sidebar
    shows its search from the stored rows (agent_sidebar.draw_stored_run())
    until a search continues in it here. Height 0 = fill (ImGui's convention)."""
    fill = imgui.ImVec2(size.x, 0)
    chat = state.chat
    if not chat.live and chat.record is not None and chat.search_started:
        draw_baseline_box(state, imgui.ImVec2(size.x, _BASELINE_PICKER_ONLY_H), show_body=False)
        chat.summary_distance = agent_sidebar.draw_stored_run(chat.record, chat.messages, chat.summary_distance, fill)
    elif state.run_state is None:
        draw_baseline_box(state, fill)
    elif _search_finished(state):
        draw_baseline_box(state, imgui.ImVec2(size.x, _BASELINE_PICKER_ONLY_H), show_body=False)
        _draw_summary(state, fill)
    else:
        draw_baseline_box(state, imgui.ImVec2(size.x, _BASELINE_RUNNING_H))
        draw_progress(state, fill)
