"""Live, pollable state for one agent run - the Job-equivalent for the
optimize-phase loop. A background thread (agent/femm_agent/services/runner.py's AgentRunner,
running agent/femm_agent/services/optimize.py's optimize_loop) mutates these objects directly,
no lock - the same convention frontend/job_runner.py's Job already uses. A
render thread (or a CLI polling loop) reads them fresh each time, relying
on the GIL for attribute-assignment atomicity rather than locking.

Pure stdlib only, and every agent-specific type hint (AgentSpec, CoilSet,
Scored) is a plain string forward-reference rather than a real import - so
this module never has to care which other agent modules exist yet, and
nothing else risks an import cycle through it.
"""

import dataclasses
import time
from datetime import datetime, timezone
from enum import Enum


def now():
    return datetime.now(timezone.utc)


class RoundStatus(str, Enum):
    PROPOSING = "proposing"
    EVALUATING = "evaluating"
    DONE = "done"


class AgentRunStatus(str, Enum):
    """How a run stands (ai_runs.status). The first two are only ever on the
    database row - an AgentRunState exists from Start Search on. `status`
    says how a search ended; AgentRunState.stop_reason says which rule ended it."""
    INTERVIEWING = "interviewing"
    PLAN_READY = "plan_ready"
    RUNNING = "running"
    GOAL_MET = "goal_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STOPPED_BY_MODEL = "stopped_by_model"
    STALLED = "stalled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


_STATUS_WITHOUT_A_WINNER = {
    "rounds": AgentRunStatus.BUDGET_EXHAUSTED, "time": AgentRunStatus.BUDGET_EXHAUSTED,
    "simulations": AgentRunStatus.BUDGET_EXHAUSTED,
    "model_stopped": AgentRunStatus.STOPPED_BY_MODEL,
    "stalled": AgentRunStatus.STALLED,
    "llm_failed": AgentRunStatus.FAILED, "internal_error": AgentRunStatus.FAILED,
    "user_stopped": AgentRunStatus.CANCELLED,
}


def status_for(stop_reason: str, has_winner: bool) -> AgentRunStatus:
    """The status a search ends with. A design that meets the goal makes it
    'goal_met' whichever rule ended the search (a search that keeps going
    after a pass and then loses the AI server still found its winner);
    without one, the rule decides."""
    if has_winner:
        return AgentRunStatus.GOAL_MET
    return _STATUS_WITHOUT_A_WINNER.get(stop_reason, AgentRunStatus.FAILED)


BUDGET_STOP_REASONS = ("rounds", "time", "simulations")  # what Budget.exhausted() can return


@dataclasses.dataclass
class Budget:
    """What one search segment may spend, and what it has spent: rounds, wall
    clock, and fresh simulations. One per segment, and it lives on the run
    state, so a search that resumes after a failed round keeps counting where
    it was. A "continue" after a search that was cut short before any limit
    (stopped by the user, the app closed) starts its segment's budget with
    what was already spent (with_spend()); only one that reached a limit gets
    a budget that starts from zero.

    `clock` is read only through this object (default time.monotonic), so a
    test can drive time by hand instead of sleeping."""
    max_rounds: int
    time_limit_s: float
    max_simulations: int
    rounds_used: int = 0
    # One simulation = one fresh solve of one candidate at one distance
    # (scorer.evaluate_async). Cache hits and the baseline's own solves are free.
    simulations_used: int = 0
    cache_hits: int = 0
    # A candidate needing more fresh solves than were left was turned away:
    # the limit is reached even if a few simulations remain unspent.
    simulation_limit_hit: bool = False
    # Held back from the search so a passing design can still be put through the
    # fragility check (services/fragility.py): simulations always, time once a
    # design has passed. The check itself spends them. Both shorten what the
    # search sees as left; neither changes the limits reported.
    reserved_simulations: int = 0
    reserved_s: float = 0.0
    # Spent before this segment began, by the segment it carries on from
    # (`continues`, None for a budget that started from zero). The counters
    # above include that segment's; this is its share of the wall clock.
    carried_s: float = 0.0
    continues: int | None = None
    started: float | None = None  # clock() when the segment began
    ended: float | None = None    # clock() when it ended - the time used stops growing there
    clock: object = dataclasses.field(default=time.monotonic, repr=False, compare=False)

    @classmethod
    def for_spec(cls, spec, clock=time.monotonic) -> "Budget":
        return cls(max_rounds=spec.max_tool_calls, time_limit_s=spec.settings.search_time_limit_s,
                   max_simulations=spec.settings.max_simulations, clock=clock)

    @classmethod
    def with_spend(cls, spec, entry: dict, clock=time.monotonic) -> "Budget":
        """The plan's limits with what `entry` (a segment's as_dict()) says
        that segment had spent - its clock not running until start()."""
        return dataclasses.replace(
            cls.for_spec(spec, clock), rounds_used=int(entry.get("rounds_used") or 0),
            simulations_used=int(entry.get("simulations_used") or 0), cache_hits=int(entry.get("cache_hits") or 0),
            carried_s=float(entry.get("seconds_used") or 0.0))

    def start(self) -> None:
        if self.started is None:
            self.started = self.clock()

    def stop(self) -> None:
        if self.started is not None and self.ended is None:
            self.ended = self.clock()

    def elapsed_s(self) -> float:
        if self.started is None:
            return self.carried_s
        return self.carried_s + (self.clock() if self.ended is None else self.ended) - self.started

    def remaining_s(self) -> float:
        return self.time_limit_s - self.reserved_s - self.elapsed_s()

    def simulations_left(self) -> int:
        return max(0, self.max_simulations - self.reserved_simulations - self.simulations_used)

    def exhausted(self) -> str | None:
        """The limit that has been reached ("rounds", "time" or "simulations"), or None."""
        if self.rounds_used >= self.max_rounds:
            return "rounds"
        if self.remaining_s() <= 0:
            return "time"
        if self.simulation_limit_hit or self.simulations_left() <= 0:
            return "simulations"
        return None

    def as_dict(self, segment: int) -> dict:
        """This segment's entry in ai_runs.budget_json."""
        entry = {"segment": segment, "max_rounds": self.max_rounds, "time_limit_s": self.time_limit_s,
                 "max_simulations": self.max_simulations, "rounds_used": self.rounds_used,
                 "simulations_used": self.simulations_used, "cache_hits": self.cache_hits,
                 "seconds_used": round(self.elapsed_s(), 1)}
        if self.continues is not None:
            entry["continues"] = self.continues  # its counts include that segment's
        return entry


@dataclasses.dataclass
class CandidateResult:
    delta: dict
    valid: bool
    tool_result: dict | None = None
    error: str | None = None
    # Why it was rejected, as a code from schema.REJECTION_DETAIL (None for a valid
    # candidate, and for rows stored before the codes existed).
    error_code: str | None = None
    # The whole rejection as the model was sent it (schema.Rejection.as_result()),
    # kept so a resumed search can rebuild its memory of it word for word.
    rejection: dict | None = None
    # The full candidate CoilSet, as a plain dict (dataclasses.asdict) - set
    # whenever dataclasses.replace(baseline, **delta) succeeds, regardless
    # of whether the candidate went on to pass FEMM validation, so the GUI
    # can render its geometry (coil_diagram.draw() takes exactly this
    # shape) even for a rejected candidate. None only when the delta itself
    # was invalid before a CoilSet could even be constructed.
    candidate_coilset: dict | None = None
    # The model's own 1-2 sentence summary of its reasoning for this
    # proposal (propose_coilset's required `rationale` argument) - the
    # concise reasoning the GUI shows per candidate, instead of the raw
    # chain-of-thought, which reads as noise even when cut short.
    rationale: str = ""
    # "C7" - given to every design that was simulated and scored, numbered
    # run-wide in the order results came back. None for a rejected proposal,
    # which can't be a starting design.
    label: str | None = None
    base_label: str | None = None  # the design this proposal started from (its `base` argument); None = the baseline
    # Only the values this call itself gave. `delta` above stays every value
    # the design changes from the baseline (its base's changes + this step).
    step: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class RoundState:
    round_num: int
    status: RoundStatus = RoundStatus.PROPOSING
    reasoning_text: str = ""
    candidates: list[CandidateResult] = dataclasses.field(default_factory=list)
    # Three timestamps split each round's wall-clock time into "waiting on
    # the LLM" vs. "waiting on FEMM solves" - a real user question ("which
    # is slower, the AI or xfemm?") this project had no logged answer to.
    proposing_started_at: datetime = dataclasses.field(default_factory=now)   # round created, LLM call about to start
    evaluating_started_at: datetime | None = None  # LLM responded, FEMM solves about to start
    done_at: datetime | None = None
    # Which search attempt this round belongs to (1, then +1 each time the search
    # resumed after a failed round - see AgentRunner._run()) and its round number within it.
    search_attempt: int = 1
    attempt_round: int = 1
    ai_try: int = 1  # which try of the round's AI request is in flight (config.ROUND_LLM_ATTEMPTS)
    llm_failures: list[dict] = dataclasses.field(default_factory=list)  # LLMError.as_dict() + context, per failed try
    llm_stats: dict = dataclasses.field(default_factory=dict)  # the successful request's elapsed_s/tokens/first_token_s
    segment: int = 1  # which search segment (1, then +1 per "continue") the round belongs to
    # The model never got to reply: every AI try failed, or the search was
    # stopped (by the user or the time limit) mid-request. Such a round is
    # not counted for or against progress.
    no_reply: bool = False
    # Code's verdict after this round (services/progress.py): "improving",
    # "stalled" or "plateau", and how many rounds in a row brought no improvement.
    verdict: str | None = None
    rounds_since_improvement: int | None = None


@dataclasses.dataclass(eq=False)  # identity equality: search_memory() tells "in the top list" apart by object, not value
class Tried:
    """One candidate the search has evaluated, as its memory keeps it.
    `scored` is None for a rejected candidate (bad field, invalid geometry,
    failed solve); `result` is what the model was sent for it."""
    round_num: int  # run-wide, as in RoundState.round_num
    delta: dict  # every value changed from the baseline (see CandidateResult.delta)
    scored: "Scored | None"
    result: dict
    segment: int = 1
    # Its fragility check (services/fragility.py), once it has had one: only a
    # design that met the goal is ever checked.
    check: dict | None = None
    label: str | None = None  # "C7" - simulated designs only (see CandidateResult.label)
    base_label: str | None = None
    step: dict = dataclasses.field(default_factory=dict)

    @property
    def check_failed(self) -> bool:
        """Met the goal but did not survive the check: never a winner, and no
        longer counted as a pass."""
        return self.check is not None and not self.check["passed"]

    @property
    def name(self) -> str:
        """How memory lines refer to it: "C7 (round 3, from C3)", or
        "round 3 (from C3)" for a rejected proposal, which has no label."""
        if self.label is None:
            return f"round {self.round_num}" + (f" (from {self.base_label})" if self.base_label else "")
        return f"{self.label} (round {self.round_num}" + (f", from {self.base_label})" if self.base_label else ")")


@dataclasses.dataclass
class AgentRunState:
    id: str
    spec: "AgentSpec"
    baseline_coilset: "CoilSet"
    status: AgentRunStatus = AgentRunStatus.RUNNING
    # The chat came from an exported file (ai_runs.imported_at): its plan was
    # made elsewhere, so it is checked against this machine's Field Config
    # table before it may search here (AgentRunner.continue_run()).
    imported: bool = False
    rounds: list[RoundState] = dataclasses.field(default_factory=list)
    best: "Scored | None" = None
    error: str | None = None
    created_at: datetime = dataclasses.field(default_factory=now)
    finished_at: datetime | None = None
    # distance -> the baseline's own solve result, once optimize_loop has it -
    # kept on the state (not just a loop local) so the follow-up chat
    # (agent/femm_agent/services/followup.py) can quote baseline numbers too.
    baseline_metrics: dict | None = None
    # The user asked the search to stop (AgentRunner.stop(): the GUI's "Stop
    # and keep the best", or Restart on a running search). optimize_loop
    # stops at its next checkpoint; the run is kept, with every finished round.
    cancelled: bool = False
    search_attempt: int = 1  # +1 each time the search resumes after a round's AI request failed every try
    # What this segment may spend and has spent. Set by AgentRunner (or by
    # optimize_loop itself when called without one).
    budget: Budget | None = None
    budget_history: list[dict] = dataclasses.field(default_factory=list)  # Budget.as_dict() of each finished segment
    # Which rule ended the search (ai_runs.stop_reason) - None while it runs.
    stop_reason: str | None = None
    segment: int = 1  # 1 for the first search, +1 for each "continue" or revised plan
    segment_start_round: int = 0  # how many rounds earlier segments had (this one's first round is this + 1)
    # Which plan the search is working to: 1, then +1 each time the user starts a
    # revised plan (AgentRunner.start_revised()). `spec` is always the plan in force.
    plan: int = 1
    # Rounds up to and including this one are outside the model's memory: a
    # revised plan added a distance they were never simulated at, so the search
    # started from the beginning (services/replan.py). 0 = every round counts.
    memory_from_round: int = 0
    # How this segment's memory began when a revised plan started it: "kept" or
    # "fresh". None for a first search or a "continue".
    segment_memory: str | None = None
    # Set by start_revised(): the tried list must be rebuilt against the new
    # plan before the segment's first round (done on the search's own thread).
    rebuild_pending: bool = False
    # True until the first round of a segment a revised plan started: an earlier
    # design may already meet the new goal, so the loop checks before proposing.
    revised_start: bool = False
    # Every candidate evaluated so far, across resumes and continuations - the
    # model's memory is rebuilt from it each round (optimize.search_memory()).
    tried: list[Tried] = dataclasses.field(default_factory=list)
    progress: "Progress | None" = None  # code's latest verdict (services/progress.py)
    # The fragility check (services/fragility.py). `winner` is the best design
    # that met the goal AND survived its check - the only thing that makes a
    # run 'goal_met'. `fragility` is every check made (ai_runs.fragility_json).
    winner: "Scored | None" = None
    fragility: list[dict] = dataclasses.field(default_factory=list)
    unchecked_pass: bool = False  # the search ended with a passing design it could not check
    progress_reset_round: int = 0  # the no-improvement count restarts after this round (a failed check episode)
    stalled_notice_sent: bool = False   # the "you are stalled" message goes out once per segment
    llm_failures: list[dict] = dataclasses.field(default_factory=list)  # every failed AI try, run-wide
    # How and why the run ended, in plain words (agent/femm_agent/services/outcome.py) -
    # set when it finishes.
    outcome_report: str | None = None
    # How many of `rounds` (from the start, in order) are already saved to the
    # database - AgentRunner saves each round as it finishes, so a crash mid-search
    # loses at most the round in flight, and never saves a round twice.
    rounds_persisted: int = 0
    # The run's log file (agent/services/run_log.py's RunLog), or None - every AI
    # request, warning and swallowed error of this run is written through it.
    log: "RunLog | None" = dataclasses.field(default=None, repr=False, compare=False)
