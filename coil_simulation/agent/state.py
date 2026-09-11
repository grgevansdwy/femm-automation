"""Live, pollable state for one agent run - the Job-equivalent for the
optimize-phase loop. A background thread (agent/runner.py's AgentRunner,
running agent/optimize.py's optimize_loop) mutates these objects directly,
no lock - the same convention gui/job_runner.py's Job already uses. A
render thread (or a CLI polling loop) reads them fresh each time, relying
on the GIL for attribute-assignment atomicity rather than locking.

Pure stdlib only, and every agent-specific type hint (AgentSpec, CoilSet,
Scored) is a plain string forward-reference rather than a real import - so
this module never has to care which other agent modules exist yet, and
nothing else risks an import cycle through it.
"""

import dataclasses
from datetime import datetime, timezone
from enum import Enum


def now():
    return datetime.now(timezone.utc)


class RoundStatus(str, Enum):
    PROPOSING = "proposing"
    EVALUATING = "evaluating"
    DONE = "done"


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    GOAL_MET = "goal_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STOPPED_BY_MODEL = "stopped_by_model"
    FAILED = "failed"


@dataclasses.dataclass
class CandidateResult:
    delta: dict
    valid: bool
    tool_result: dict | None = None
    error: str | None = None
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


@dataclasses.dataclass
class AgentRunState:
    id: str
    spec: "AgentSpec"
    baseline_coilset: "CoilSet"
    status: AgentRunStatus = AgentRunStatus.RUNNING
    rounds: list[RoundState] = dataclasses.field(default_factory=list)
    best: "Scored | None" = None
    error: str | None = None
    created_at: datetime = dataclasses.field(default_factory=now)
    finished_at: datetime | None = None
