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
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol

from agent.femm_agent.services import fragility
from agent.femm_agent.services.optimize import RoundFailed, optimize_loop
from agent.femm_agent.services.outcome import outcome_report
from agent.femm_agent.models.state import AgentRunState, AgentRunStatus, Budget, now, status_for


def _git_commit() -> str | None:
    """The checked-out commit, for the run log (which code produced this run)."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


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
    it submits so AgentRunner.stop() can drop that run's still-queued
    solves (and only that run's). A job submitted after a stop - the
    loop racing past its last checkpoint - is dropped straight away."""

    def __init__(self, job_runner: SolveJobRunner, state: AgentRunState) -> None:
        self._inner = job_runner
        self._state = state
        self.jobs: list = []
        # What the run actually cost, for the run log and report: both include
        # the baseline's own solves. The limit is counted separately, baseline
        # free, on state.Budget (scorer.evaluate_async).
        self.simulations = 0  # fresh solves submitted (one candidate at one distance)
        self.cache_hits = 0

    def find_cached(self, coilset, distance):
        row = self._inner.find_cached(coilset, distance)
        if row is not None:
            self.cache_hits += 1
        return row

    def touch(self, row):
        return self._inner.touch(row)

    def submit(self, coilset, config_id, config_name, distance):
        job = self._inner.submit(coilset, config_id=config_id, config_name=config_name, distance=distance)
        self.jobs.append(job)
        self.simulations += 1
        if self._state.cancelled:
            self._inner.cancel(job.id)
        return job

    def cancel(self, job_id) -> bool:
        return self._inner.cancel(job_id)

    def cancel_queued(self) -> None:
        for job in list(self.jobs):
            self._inner.cancel(job.id)


class AgentRunner:
    def __init__(self, job_runner: SolveJobRunner, clock=time.monotonic) -> None:
        self.runs: dict[str, AgentRunState] = {}
        self._job_runner = job_runner
        self._run_jobs: dict[str, _RunJobs] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._clock = clock  # what each search's Budget reads time from (a test passes its own)

    def submit(self, spec, ai_run_id: str | None = None) -> AgentRunState:
        """`ai_run_id` lets a caller that already created the ai_runs row
        (agent/input_chat/services/runner.py's InterviewRunner, once its interview
        finalizes) hand it straight to the optimize phase, so both phases
        land under the SAME history row. Without one (e.g. orchestrator.py's
        CLI wrapper, no interview), a fresh row is created here instead -
        every agent run gets persisted history either way."""
        from engine.repositories.coilset_repository import get_coilset  # deferred - resolves via agent/__init__.py's sys.path bootstrap
        baseline = get_coilset(spec.baseline_config_id)
        from agent.repositories.ai_run_repository import create_ai_run, finalize_spec, transition
        if ai_run_id is None:
            ai_run_id = create_ai_run(spec.baseline_config_id)["id"]
            finalize_spec(ai_run_id, dataclasses.asdict(spec))
        # Start Search: the plan is confirmed. The baseline is copied into the
        # run, since its config can be edited or deleted later.
        transition(ai_run_id, "running", started_at=now().isoformat(), baseline_coilset=dataclasses.asdict(baseline))
        from agent.services.run_log import RunLog
        state = AgentRunState(id=ai_run_id, spec=spec, baseline_coilset=baseline, log=RunLog(ai_run_id),
                              budget=Budget.for_spec(spec, self._clock))
        self.runs[state.id] = state
        self._start_thread(state)
        return state

    def _start_thread(self, state: AgentRunState) -> None:
        self._run_jobs[state.id] = _RunJobs(self._job_runner, state)
        thread = threading.Thread(target=self._run, args=(state.id,), daemon=True)
        self._threads[state.id] = thread
        thread.start()

    def adopt(self, state: AgentRunState) -> None:
        """Takes in a finished run rebuilt from the database
        (restore.restore_run()), so it can be continued here like one that
        ran in this session."""
        if state.log is None:
            from agent.services.run_log import RunLog
            state.log = RunLog(state.id)
        self.runs[state.id] = state

    def continue_run(self, run_id: str) -> AgentRunState:
        """ "continue": searches on from where a finished search stopped, as a
        new segment of the same run - a fresh budget (the run's own settings),
        the same memory (everything tried so far), rounds appended after the
        existing ones, candidates still compared with the original baseline.
        Raises ValueError if that run isn't here or is still searching."""
        from agent.repositories.ai_run_repository import transition
        state = self.runs.get(run_id)
        if state is None:
            raise ValueError(f"no search {run_id!r} in this session to continue")
        if state.status == AgentRunStatus.RUNNING:
            raise ValueError("this search is still running")
        thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout=30)  # the finished segment's last save
            if thread.is_alive():
                raise ValueError("this search is still finishing - try again in a moment")
        transition(run_id, "running")
        state.segment += 1
        state.segment_start_round = len(state.rounds)
        state.budget = Budget.for_spec(state.spec, self._clock)
        state.search_attempt = 1
        state.cancelled = False
        state.stalled_notice_sent = False
        state.stop_reason = state.finished_at = state.outcome_report = state.error = None
        state.status = AgentRunStatus.RUNNING
        self._start_thread(state)
        return state

    def poll(self) -> None:
        """No-op: _run() mutates `state` directly on its own thread, no
        Future to clean up (see the module docstring). Kept so a per-frame
        GUI loop can call it unconditionally, the same shape as
        frontend/job_runner.py's JobRunner.poll()."""
        pass

    def stop(self, run_id: str) -> None:
        """Asks a running search to stop: flags it so optimize_loop ends at its
        next checkpoint, and drops its FEMM solves that haven't started yet.
        Solves already running finish and land in the run cache like any
        other solve. Returns immediately - the background thread may still be
        waiting on an LLM reply (which is aborted) or a solve. The run is
        kept: its finished rounds, its best design, and an ending of
        'cancelled' (or 'goal_met' if a design had already met the goal and
        survived its fragility check - nothing is checked once the user stops)."""
        state = self.runs.get(run_id)
        if state is None:
            return
        state.cancelled = True
        run_jobs = self._run_jobs.get(run_id)
        if run_jobs is not None:
            run_jobs.cancel_queued()

    cancel = stop  # the name other callers know it by

    def _run(self, run_id: str) -> None:
        """Runs one search segment to its end. When a round's AI request
        fails every try (optimize.RoundFailed) the search resumes - same
        memory, same budget still counting - until settings.search_attempts
        rounds have failed that way. Whatever the ending, the rule that
        ended it is left in state.stop_reason, the status follows from it
        (state.status_for), a plain-language report is left in
        state.outcome_report (agent/femm_agent/services/outcome.py), and the run is saved."""
        state = self.runs[run_id]
        run_jobs = self._run_jobs[run_id]
        search_attempts = state.spec.settings.search_attempts
        internal_error = None
        started = time.monotonic()
        self._log_run_start(state)
        try:
            while True:
                try:
                    optimize_loop(state, run_jobs, on_round_done=self._save_rounds)
                    break
                except RoundFailed:
                    if state.cancelled:
                        state.stop_reason = "user_stopped"
                    elif state.search_attempt >= search_attempts:
                        state.stop_reason = "llm_failed"
                    else:
                        state.stop_reason = state.budget.exhausted()
                    if state.stop_reason is not None:
                        # The fragility check needs no AI: a design that passed before
                        # the server was lost can still be checked, and win.
                        if state.stop_reason != "user_stopped" and state.baseline_metrics is not None:
                            fragility.run_episode(state, run_jobs)
                        else:
                            state.unchecked_pass = bool(fragility.unchecked_passing(state))
                        break
                    state.search_attempt += 1  # resume: everything tried so far is still in memory
        except Exception as exc:
            internal_error = repr(exc)
            state.stop_reason = "internal_error"
            if state.log is not None:
                state.log.warning("AgentRunner._run", "the search stopped on an internal error", exc)
        finally:
            if state.stop_reason is None:  # can't happen unless the loop returned without saying why
                state.stop_reason = "user_stopped" if state.cancelled else "internal_error"
            # Only a design that met the goal and survived its fragility check is a winner.
            status = status_for(state.stop_reason, state.winner is not None)
            state.finished_at = now()
            state.budget.stop()
            state.budget_history.append(state.budget.as_dict(state.segment))
            state.outcome_report = outcome_report(state, internal_error, status=status)
            state.error = state.outcome_report if status == AgentRunStatus.FAILED else None
            state.status = status  # last: a poller that sees the search finished finds the report already there
            if state.log is not None:
                state.log.run_end(
                    status=status.value, stop_reason=state.stop_reason, segment=state.segment,
                    rounds=len(state.rounds), simulations=run_jobs.simulations, cache_hits=run_jobs.cache_hits,
                    budget=state.budget_history[-1],
                    llm_failures=len(state.llm_failures), seconds=round(time.monotonic() - started, 1))
            try:
                self._persist(state)
            except Exception as exc:
                if state.log is not None:
                    state.log.warning("AgentRunner._persist", "could not save the finished run", exc)
                raise

    @staticmethod
    def _log_run_start(state: AgentRunState) -> None:
        """The run_start line: everything needed to say which code, settings,
        goal and baseline produced this run, and the tool schema its requests
        carried. Never raises - a run must start whether or not it can be logged."""
        if state.log is None:
            return
        try:
            from agent.femm_agent.models.schema import build_tools
            from agent.input_chat.models.variables import free_fields_for, search_field_table
            spec = state.spec
            tools = build_tools(free_fields_for(state.baseline_coilset, spec.variable_spec,
                                                search_field_table(spec.settings)), spec.baseline_config_id)
            state.log.run_start(
                segment=state.segment, git_commit=_git_commit(), settings=dataclasses.asdict(spec.settings),
                spec=dataclasses.asdict(spec), baseline_coilset=dataclasses.asdict(state.baseline_coilset),
                tools=tools)
        except Exception as exc:
            state.log.warning("AgentRunner._log_run_start", "could not write the run_start line", exc)

    @staticmethod
    def _save_rounds(state: AgentRunState) -> None:
        """optimize_loop's on_round_done hook: saves the finished rounds not yet
        in the database, so a crash mid-search loses at most the round in flight.
        Never raises - a database hiccup here must not kill the search; _persist()
        below saves whatever this missed. A round is written at most once
        (state.rounds_persisted)."""
        try:
            AgentRunner._write_rounds(state, only_finished=True)
        except Exception as exc:
            if state.log is not None:
                state.log.warning("AgentRunner._save_rounds", "could not save a finished round", exc)

    @staticmethod
    def _write_rounds(state: AgentRunState, only_finished: bool) -> None:
        from agent.femm_agent.models.state import RoundStatus
        from agent.repositories.ai_run_repository import append_message

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
                "segment": round_state.segment,
                "no_reply": round_state.no_reply,
                "verdict": round_state.verdict,
                "rounds_since_improvement": round_state.rounds_since_improvement,
                # Same AI-vs-FEMM split the live feed shows, so history can too.
                "llm_seconds": _seconds_between(round_state.proposing_started_at, round_state.evaluating_started_at),
                "femm_seconds": _seconds_between(round_state.evaluating_started_at, round_state.done_at),
            }
            append_message(
                state.id, phase="optimize", seq=round_state.round_num, role="assistant",
                round_num=round_state.round_num, content=round_state.reasoning_text, payload=payload,
            )
            state.rounds_persisted += 1

    @staticmethod
    def _persist(state: AgentRunState) -> None:
        """The end of a search segment, however it ended (a stop by the user
        included): saves any round not already saved by _save_rounds() (all of
        them if that never ran), then the final status, the rule that ended
        it, what each segment spent, and the best candidate if any."""
        from agent.repositories.ai_run_repository import transition

        AgentRunner._write_rounds(state, only_finished=False)

        best = state.best
        best_dict = dataclasses.asdict(best.coilset) if best is not None else None
        best_round = next((r.round_num for r in state.rounds for c in r.candidates
                           if c.valid and c.candidate_coilset == best_dict), None) if best is not None else None
        transition(
            # `error` holds the outcome report, so History can show how the
            # search ended (agent/femm_agent/services/outcome.py).
            state.id, state.status.value, error=state.outcome_report,
            stop_reason=state.stop_reason, budget=state.budget_history, fragility=state.fragility or None,
            finished_at=state.finished_at.isoformat() if state.finished_at else None,
            best_round=best_round,
            best_coilset=(dataclasses.asdict(best.coilset) if best is not None else None),
            best_tool_result=fragility.best_result(state),
        )
