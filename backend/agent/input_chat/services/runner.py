"""InterviewRunner: runs one interview turn (agent/input_chat/services/interview.py) on a
background thread and persists each new transcript message as it lands -
the interview-phase counterpart to agent/femm_agent/services/runner.py's AgentRunner, same
pollable/no-lock conventions. frontend/ only ever calls into this (and
AgentRunner) - it never reimplements orchestration or touches
agent/input_chat/services/interview.py directly.
"""

import dataclasses
import threading
import time
from datetime import datetime, timezone

from agent.input_chat.services.interview import InterviewState, run_interview_turn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InterviewRunner:
    def __init__(self) -> None:
        self.ai_run_id: str | None = None
        self.busy: bool = False  # a turn is in flight - the GUI disables input/Send while True
        # A real LLM turn can take anywhere from a few seconds to several
        # minutes - with no feedback beyond "busy=True" the panel looked
        # indistinguishable from hung. turn_started_at lets it show a live
        # elapsed-seconds counter instead of a static "...".
        self.turn_started_at: float | None = None
        self._persisted_count = 0
        self._persisted_spec = None  # the spec object last written by finalize_spec(), so a re-finalized plan is written once
        # Set by discard() (the GUI's Restart, or picking another baseline). A
        # turn still in flight when that happens finishes on its own thread,
        # but writes nothing more - the chat was closed.
        self.discarded: bool = False
        self._log = None  # the run's RunLog (agent/services/run_log.py), once the run has an id

    def _phase_log(self, phase: str):
        return self._log.bind(phase=phase) if self._log is not None else None

    def poll(self) -> None:
        """No-op, kept for the same per-frame-call-uniformity reason as
        AgentRunner.poll() - see that method's docstring."""
        pass

    def start(self, baseline_config_id: str) -> InterviewState:
        """An empty InterviewState for a new chat. Nothing is saved yet: the
        ai_runs row is created by the first send(), so opening the panel or
        picking a baseline leaves no empty row behind. Deliberately does NOT
        fire an opening LLM turn either - an unprompted first turn produced
        a long multi-question wall of text (real, observed behavior); the
        chat input's own placeholder prompts for an objective directly."""
        from agent.settings.settings import load_settings
        self.ai_run_id = None
        self.discarded = False
        self._log = None
        self._persisted_count = 0
        self._persisted_spec = None
        return InterviewState(baseline_config_id=baseline_config_id, settings=load_settings())

    def attach(self, ai_run_id: str, state: InterviewState) -> None:
        """Points this runner at a stored run whose chat was rebuilt from the
        database (restore.restore_interview()): new messages are saved after
        the stored ones, and the stored plan is not written again."""
        from agent.services.run_log import RunLog
        self.ai_run_id = ai_run_id
        self.discarded = False
        self._log = RunLog(ai_run_id)
        self._persisted_count = len(state.transcript)
        self._persisted_spec = state.spec

    def _ensure_run(self, baseline_config_id: str) -> None:
        """The user's first message: the run gets its row ('interviewing') and its log."""
        if self.ai_run_id is None:
            from agent.repositories.ai_run_repository import create_ai_run
            from agent.services.run_log import RunLog
            self.ai_run_id = create_ai_run(baseline_config_id)["id"]
            self._log = RunLog(self.ai_run_id)

    def send(self, state: InterviewState, user_message: str) -> None:
        self._ensure_run(state.baseline_config_id)
        self._run_turn_async(state, lambda: run_interview_turn(state, user_message, should_abort=lambda: self.discarded,
                                                                log=self._phase_log("interview")))

    def send_followup(self, state: InterviewState, run_state, user_message: str) -> None:
        """A question after the search has started (running or finished) -
        agent/femm_agent/services/followup.py, same transcript, no tools. Persisted under the
        same ai_run as the planning chat, after it, so History's
        Conversation section shows the whole exchange in order."""
        from agent.femm_agent.services.followup import run_followup_turn
        self._run_turn_async(state, lambda: run_followup_turn(state, user_message, run_state,
                                                              should_abort=lambda: self.discarded,
                                                              log=self._phase_log("followup")))

    def record_user_message(self, state: InterviewState, user_message: str) -> None:
        """Adds a user message that code acts on itself (no AI turn) to the
        chat and saves it - "continue" after a search, so the conversation
        shows why a new search segment starts there."""
        self._ensure_run(state.baseline_config_id)
        state.transcript.append({"role": "user", "content": user_message})
        self._persist_new_messages(state)

    def discard(self) -> None:
        self.discarded = True

    def cancel_conversation(self) -> None:
        """Closes a chat whose search never started (Restart or a new baseline
        while planning): nothing more is written for it, and its row - if the
        user ever sent a message - is kept as 'cancelled', plan and all.
        A no-op for a row that is past planning (the search owns its status)."""
        from agent.repositories.ai_run_repository import IllegalTransition, transition
        self.discarded = True
        if self.ai_run_id is None:
            return
        try:
            transition(self.ai_run_id, "cancelled", finished_at=_now_iso())
        except (IllegalTransition, KeyError) as exc:
            if self._log is not None:
                self._log.warning("InterviewRunner.cancel_conversation", "the chat could not be marked cancelled", exc)

    def _run_turn_async(self, state: InterviewState, turn) -> None:
        self.busy = True
        self.turn_started_at = time.monotonic()
        threading.Thread(target=self._run_turn, args=(state, turn), daemon=True).start()

    def _run_turn(self, state: InterviewState, turn) -> None:
        try:
            turn()
            if not self.discarded:
                self._persist_new_messages(state)
        except Exception as exc:
            # Last resort: a turn must never die silently on its thread, leaving
            # the chat with no reply and no reason.
            state.error = f"This turn failed with an internal error: {exc}"
            if self._log is not None:
                self._log.warning("InterviewRunner._run_turn", "an interview or follow-up turn raised", exc)
        finally:
            self.busy = False
            self.turn_started_at = None

    def _persist_new_messages(self, state: InterviewState) -> None:
        from agent.repositories.ai_run_repository import append_message, finalize_spec

        for i, msg in enumerate(state.transcript[self._persisted_count:], start=self._persisted_count):
            append_message(
                self.ai_run_id, phase="interview", seq=i, role=msg.get("role", "assistant"),
                content=msg.get("content"), payload=msg,
            )
        self._persisted_count = len(state.transcript)

        # Only a newly finalized plan - a follow-up turn leaves the spec
        # untouched, and re-writing it would move a finished run back to
        # 'plan_ready' (finalize_spec() sets that).
        if state.finalized and state.spec is not None and state.spec is not self._persisted_spec:
            finalize_spec(self.ai_run_id, dataclasses.asdict(state.spec))
            self._persisted_spec = state.spec
