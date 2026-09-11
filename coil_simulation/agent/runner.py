"""AgentRunner: runs the optimize-phase loop (agent/optimize.py) on a
background thread, mutating a shared AgentRunState in place each round -
the Job-equivalent for one agent run, mirroring gui/job_runner.py's
JobRunner shape (submit(), a live object a caller polls) so a GUI panel
(or a CLI polling loop) can read live progress the same way it already
reads a Job's progress.

Deliberately does NOT run on the same ThreadPoolExecutor as the individual
FEMM solve jobs (job_runner.EXECUTOR) - this loop spends most of its time
blocked waiting on those very solves (see scorer.wait_for_jobs), and
sharing a bounded pool with the work it's waiting on can starve or
deadlock that pool once concurrent agent runs approach the pool's worker
count. One dedicated daemon thread per run instead - there are never more
than a handful of concurrent agent runs, so a second executor would be
overkill.
"""

import dataclasses
import threading
from typing import Protocol

from agent.optimize import optimize_loop
from agent.state import AgentRunState, AgentRunStatus, now


def _seconds_between(start, end) -> float | None:
    return (end - start).total_seconds() if start is not None and end is not None else None


class SolveJobRunner(Protocol):
    """The subset of gui/job_runner.py's JobRunner this package depends on -
    a Protocol instead of importing JobRunner directly, so coil_simulation
    never has to depend on gui. Any object with this shape works, whether
    it's the GUI's real JobRunner or a test double."""
    def find_cached(self, coilset, distance): ...
    def submit(self, coilset, config_id, config_name, distance): ...
    def touch(self, row): ...


class AgentRunner:
    def __init__(self, job_runner: SolveJobRunner) -> None:
        self.runs: dict[str, AgentRunState] = {}
        self._job_runner = job_runner

    def submit(self, spec, ai_run_id: str | None = None) -> AgentRunState:
        """`ai_run_id` lets a caller that already created the ai_runs row
        (agent/gui_bridge.py's InterviewRunner, once its interview
        finalizes) hand it straight to the optimize phase, so both phases
        land under the SAME history row. Without one (e.g. orchestrator.py's
        CLI wrapper, no interview), a fresh row is created here instead -
        every agent run gets persisted history either way."""
        from repositories.coilset_repository import get_coilset  # deferred - resolves via agent/__init__.py's sys.path bootstrap
        baseline = get_coilset(spec.baseline_config_id)
        if ai_run_id is None:
            from repositories.ai_run_repository import create_ai_run, finalize_spec
            ai_run_id = create_ai_run(spec.baseline_config_id)["id"]
            finalize_spec(ai_run_id, dataclasses.asdict(spec))
        state = AgentRunState(id=ai_run_id, spec=spec, baseline_coilset=baseline)
        self.runs[state.id] = state
        threading.Thread(target=self._run, args=(state.id,), daemon=True).start()
        return state

    def poll(self) -> None:
        """No-op: _run() mutates `state` directly on its own thread, no
        Future to clean up (see the module docstring). Kept so a per-frame
        GUI loop can call it unconditionally, the same shape as
        gui/job_runner.py's JobRunner.poll()."""
        pass

    def _run(self, run_id: str) -> None:
        state = self.runs[run_id]
        try:
            optimize_loop(state, self._job_runner)
        except Exception as exc:
            state.error = repr(exc)
            state.status = AgentRunStatus.FAILED
        finally:
            state.finished_at = now()
            self._persist(state)

    @staticmethod
    def _persist(state: AgentRunState) -> None:
        """Writes the whole run - every round, the final status, and the
        best candidate if any - in one shot at the end, not incrementally
        per round. Nothing currently needs to read a still-running agent
        run's progress from disk (the GUI polls `state` directly in
        memory), so incremental persistence would add concurrency surface
        for no consumer - see docs/gui_agent_plan.md's milestone 3 notes."""
        from agent.scorer import tool_result
        from repositories.ai_run_repository import append_message, update_run_status

        for round_state in state.rounds:
            payload = {
                "candidates": [dataclasses.asdict(c) for c in round_state.candidates],
                # Same AI-vs-FEMM split the live feed shows, so history can too.
                "llm_seconds": _seconds_between(round_state.proposing_started_at, round_state.evaluating_started_at),
                "femm_seconds": _seconds_between(round_state.evaluating_started_at, round_state.done_at),
            }
            append_message(
                state.id, phase="optimize", seq=round_state.round_num, role="assistant",
                round_num=round_state.round_num, content=round_state.reasoning_text, payload=payload,
            )

        best = state.best
        update_run_status(
            state.id, status=state.status.value, error=state.error,
            finished_at=state.finished_at.isoformat() if state.finished_at else None,
            best_round=(state.rounds[-1].round_num if best is not None and state.rounds else None),
            best_coilset=(dataclasses.asdict(best.coilset) if best is not None else None),
            best_tool_result=(tool_result(best, state.spec) if best is not None else None),
        )
