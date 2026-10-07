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

from agent.input_chat.services.interview import InterviewState, run_interview_turn


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
        # Set by discard() (the GUI's Restart). A turn still in flight when
        # that happens finishes on its own thread, but writes nothing - its
        # ai_run was deleted (or, for a finished run, the chat was closed).
        self.discarded: bool = False
        self._log = None  # the run's RunLog (agent/services/run_log.py), once start() has an ai_run id

    def _phase_log(self, phase: str):
        return self._log.bind(phase=phase) if self._log is not None else None

    def poll(self) -> None:
        """No-op, kept for the same per-frame-call-uniformity reason as
        AgentRunner.poll() - see that method's docstring."""
        pass

    def start(self, baseline_config_id: str) -> InterviewState:
        """Creates the ai_runs history row (status='interviewing') and an
        empty InterviewState - deliberately does NOT fire an opening LLM
        turn. An unprompted first turn produced a long multi-question wall
        of text (real, observed behavior); the chat input's own placeholder
        now prompts for an objective directly, so the first LLM call only
        happens once the user actually sends something."""
        from agent.repositories.ai_run_repository import create_ai_run
        from agent.settings.settings import load_settings
        from agent.services.run_log import RunLog
        self.ai_run_id = create_ai_run(baseline_config_id)["id"]
        self._log = RunLog(self.ai_run_id)
        self._persisted_count = 0
        self._persisted_spec = None
        return InterviewState(baseline_config_id=baseline_config_id, settings=load_settings())

    def send(self, state: InterviewState, user_message: str) -> None:
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

    def discard(self) -> None:
        self.discarded = True

    def _run_turn_async(self, state: InterviewState, turn) -> None:
        self.busy = True
        self.turn_started_at = time.monotonic()
        threading.Thread(target=self._run_turn, args=(state, turn), daemon=True).start()

    def _run_turn(self, state: InterviewState, turn) -> None:
        try:
            turn()
            if not self.discarded:
                self._persist_new_messages(state)
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
        # untouched, and re-writing it would flip a finished run's status
        # back to 'running' (finalize_spec() sets that).
        if state.finalized and state.spec is not None and state.spec is not self._persisted_spec:
            finalize_spec(self.ai_run_id, dataclasses.asdict(state.spec))
            self._persisted_spec = state.spec
