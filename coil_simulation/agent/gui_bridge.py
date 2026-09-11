"""InterviewRunner: runs one interview turn (agent/interview.py) on a
background thread and persists each new transcript message as it lands -
the interview-phase counterpart to agent/runner.py's AgentRunner, same
pollable/no-lock conventions. gui/ only ever calls into this (and
AgentRunner) - it never reimplements orchestration or touches
agent/interview.py directly.
"""

import dataclasses
import threading
import time

from agent.interview import InterviewState, run_interview_turn


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
        from repositories.ai_run_repository import create_ai_run
        self.ai_run_id = create_ai_run(baseline_config_id)["id"]
        self._persisted_count = 0
        return InterviewState(baseline_config_id=baseline_config_id)

    def send(self, state: InterviewState, user_message: str) -> None:
        self._run_turn_async(state, user_message)

    def _run_turn_async(self, state: InterviewState, user_message: str | None) -> None:
        self.busy = True
        self.turn_started_at = time.monotonic()
        threading.Thread(target=self._run_turn, args=(state, user_message), daemon=True).start()

    def _run_turn(self, state: InterviewState, user_message: str | None) -> None:
        try:
            run_interview_turn(state, user_message)
            self._persist_new_messages(state)
        finally:
            self.busy = False
            self.turn_started_at = None

    def _persist_new_messages(self, state: InterviewState) -> None:
        from repositories.ai_run_repository import append_message, finalize_spec

        for i, msg in enumerate(state.transcript[self._persisted_count:], start=self._persisted_count):
            append_message(
                self.ai_run_id, phase="interview", seq=i, role=msg.get("role", "assistant"),
                content=msg.get("content"), payload=msg,
            )
        self._persisted_count = len(state.transcript)

        if state.finalized and state.spec is not None:
            finalize_spec(self.ai_run_id, dataclasses.asdict(state.spec))
