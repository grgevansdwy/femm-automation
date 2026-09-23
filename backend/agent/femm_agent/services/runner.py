"""AgentRunner: runs the optimize-phase loop (agent/femm_agent/services/optimize.py) on a
background thread, mutating a shared AgentRunState in place each round -
the Job-equivalent for one agent run, mirroring frontend/job_runner.py's
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

from agent.femm_agent.services.optimize import RoundFailed, optimize_loop
from agent.femm_agent.services.outcome import outcome_report
from agent.femm_agent.models.state import AgentRunState, AgentRunStatus, now


def _seconds_between(start, end) -> float | None:
    return (end - start).total_seconds() if start is not None and end is not None else None


class SolveJobRunner(Protocol):
    """The subset of frontend/job_runner.py's JobRunner this package depends on -
    a Protocol instead of importing JobRunner directly, so backend
    never has to depend on frontend. Any object with this shape works, whether
    it's the GUI's real JobRunner or a test double."""
    def find_cached(self, coilset, distance): ...
    def submit(self, coilset, config_id, config_name, distance): ...
    def touch(self, row): ...
    def cancel(self, job_id) -> bool: ...


class _RunJobs:
    """Wraps the shared job runner for one agent run, remembering every job
    it submits so AgentRunner.cancel() can drop that run's still-queued
    solves (and only that run's). A job submitted after cancellation - the
    loop racing past its last checkpoint - is dropped straight away."""

    def __init__(self, job_runner: SolveJobRunner, state: AgentRunState) -> None:
        self._inner = job_runner
        self._state = state
        self.jobs: list = []

    def find_cached(self, coilset, distance):
        return self._inner.find_cached(coilset, distance)

    def touch(self, row):
        return self._inner.touch(row)

    def submit(self, coilset, config_id, config_name, distance):
        job = self._inner.submit(coilset, config_id=config_id, config_name=config_name, distance=distance)
        self.jobs.append(job)
        if self._state.cancelled:
            self._inner.cancel(job.id)
        return job

    def cancel_queued(self) -> None:
        for job in list(self.jobs):
            self._inner.cancel(job.id)


class AgentRunner:
    def __init__(self, job_runner: SolveJobRunner) -> None:
        self.runs: dict[str, AgentRunState] = {}
        self._job_runner = job_runner
        self._run_jobs: dict[str, _RunJobs] = {}

    def submit(self, spec, ai_run_id: str | None = None) -> AgentRunState:
        """`ai_run_id` lets a caller that already created the ai_runs row
        (agent/input_chat/services/runner.py's InterviewRunner, once its interview
        finalizes) hand it straight to the optimize phase, so both phases
        land under the SAME history row. Without one (e.g. orchestrator.py's
        CLI wrapper, no interview), a fresh row is created here instead -
        every agent run gets persisted history either way."""
        from engine.repositories.coilset_repository import get_coilset  # deferred - resolves via agent/__init__.py's sys.path bootstrap
        baseline = get_coilset(spec.baseline_config_id)
        if ai_run_id is None:
            from agent.repositories.ai_run_repository import create_ai_run, finalize_spec
            ai_run_id = create_ai_run(spec.baseline_config_id)["id"]
            finalize_spec(ai_run_id, dataclasses.asdict(spec))
        state = AgentRunState(id=ai_run_id, spec=spec, baseline_coilset=baseline)
        self.runs[state.id] = state
        self._run_jobs[state.id] = _RunJobs(self._job_runner, state)
        threading.Thread(target=self._run, args=(state.id,), daemon=True).start()
        return state

    def poll(self) -> None:
        """No-op: _run() mutates `state` directly on its own thread, no
        Future to clean up (see the module docstring). Kept so a per-frame
        GUI loop can call it unconditionally, the same shape as
        frontend/job_runner.py's JobRunner.poll()."""
        pass

    def cancel(self, run_id: str) -> None:
        """Discards a running search: flags it so optimize_loop stops at its
        next checkpoint and _run() skips persisting it, and drops its FEMM
        solves that haven't started yet. Solves already running finish and
        land in the run cache like any other solve. Returns immediately -
        the background thread may still be waiting on an LLM reply or a
        solve, whose result is thrown away when it arrives."""
        state = self.runs.get(run_id)
        if state is None:
            return
        state.cancelled = True
        run_jobs = self._run_jobs.get(run_id)
        if run_jobs is not None:
            run_jobs.cancel_queued()

    def _run(self, run_id: str) -> None:
        """Runs the search, restarting it from zero memory when a round's AI
        request fails every try (optimize.RoundFailed), up to settings.search_attempts
        attempts in total. Earlier attempts' rounds stay in state.rounds (and
        History) and state.best stays the best across attempts; only what
        the model is sent starts over. Whatever the ending, a plain-language
        report of why the goal wasn't met is left in state.outcome_report and
        state.error (agent/femm_agent/services/outcome.py)."""
        state = self.runs[run_id]
        run_jobs = self._run_jobs[run_id]
        search_attempts = state.spec.settings.search_attempts
        internal_error = None
        try:
            for attempt in range(1, search_attempts + 1):
                state.search_attempt = attempt
                try:
                    optimize_loop(state, run_jobs, on_round_done=self._save_rounds)
                    break
                except RoundFailed:
                    if attempt == search_attempts or state.cancelled:
                        state.status = AgentRunStatus.FAILED
                        break
                    # Start over from zero memory - see optimize_loop().
        except Exception as exc:
            internal_error = repr(exc)
            state.status = AgentRunStatus.FAILED
        finally:
            state.finished_at = now()
            if not state.cancelled:
                state.outcome_report = outcome_report(state, internal_error)
                state.error = state.outcome_report if state.status == AgentRunStatus.FAILED else None
            if state.cancelled:
                self.runs.pop(run_id, None)
                self._run_jobs.pop(run_id, None)
            else:
                self._persist(state)

    @staticmethod
    def _save_rounds(state: AgentRunState) -> None:
        """optimize_loop's on_round_done hook: saves the finished rounds not yet
        in the database, so a crash mid-search loses at most the round in flight.
        Never raises - a database hiccup here must not kill the search; _persist()
        below saves whatever this missed. A round is written at most once
        (state.rounds_persisted), and nothing is written for a run the user
        has since discarded."""
        try:
            AgentRunner._write_rounds(state, only_finished=True)
        except Exception:
            pass

    @staticmethod
    def _write_rounds(state: AgentRunState, only_finished: bool) -> None:
        from agent.femm_agent.models.state import RoundStatus
        from agent.repositories.ai_run_repository import append_message, delete_ai_run

        if state.cancelled:
            return
        while state.rounds_persisted < len(state.rounds):
            round_state = state.rounds[state.rounds_persisted]
            if only_finished and round_state.status != RoundStatus.DONE:
                break
            payload = {
                "candidates": [dataclasses.asdict(c) for c in round_state.candidates],
                "search_attempt": round_state.search_attempt,
                "attempt_round": round_state.attempt_round,
                "llm_failures": round_state.llm_failures,
                "llm_stats": round_state.llm_stats,
                # Same AI-vs-FEMM split the live feed shows, so history can too.
                "llm_seconds": _seconds_between(round_state.proposing_started_at, round_state.evaluating_started_at),
                "femm_seconds": _seconds_between(round_state.evaluating_started_at, round_state.done_at),
            }
            append_message(
                state.id, phase="optimize", seq=round_state.round_num, role="assistant",
                round_num=round_state.round_num, content=round_state.reasoning_text, payload=payload,
            )
            state.rounds_persisted += 1
            if state.cancelled:
                # Restart landed while this row was being written: cancel() only stops
                # NEW writes, and the GUI's delete may have run before this insert - so
                # delete again (a no-op if it already ran) rather than leave orphan rows.
                delete_ai_run(state.id)
                return

    @staticmethod
    def _persist(state: AgentRunState) -> None:
        """The end of the run: saves any round not already saved by _save_rounds()
        (all of them if that never ran), then the final status and the best
        candidate if any."""
        from agent.femm_agent.services.scorer import tool_result
        from agent.repositories.ai_run_repository import update_run_status

        AgentRunner._write_rounds(state, only_finished=False)

        best = state.best
        best_dict = dataclasses.asdict(best.coilset) if best is not None else None
        best_round = next((r.round_num for r in state.rounds for c in r.candidates
                           if c.valid and c.candidate_coilset == best_dict), None) if best is not None else None
        update_run_status(
            # `error` holds the outcome report for any ending but a met goal,
            # so History can show why (agent/femm_agent/services/outcome.py).
            state.id, status=state.status.value, error=state.outcome_report,
            finished_at=state.finished_at.isoformat() if state.finished_at else None,
            best_round=best_round,
            best_coilset=(dataclasses.asdict(best.coilset) if best is not None else None),
            best_tool_result=(tool_result(best, state.spec) if best is not None else None),
        )
