"""AgentSpec: the fully-resolved objective for one agent run - the one
contract between "how the objective was gathered" (an interview
conversation, agent/interview.py) and "what the optimize loop searches for"
(agent/optimize.py). optimize.py never needs to know which produced it.

Field defaults come from agent/config.py's constants (kept there, not
deleted) - a caller that builds an AgentSpec with no arguments gets exactly
today's hardcoded CLI behavior.
"""

import dataclasses

from agent.config import (
    BASELINE_CONFIG_ID, DISTANCES, KEEP_RECENT_ROUNDS, MAX_TOOL_CALLS,
    MIN_ESR_IMPROVEMENT_PCT, M_TOLERANCE_PCT, PRIMARY_M,
)
from agent.objective import Objective
from agent.variables import VariableSpec


def _default_objectives() -> list[Objective]:
    """The CLI's original hardcoded goal, spelled out as objectives: hold
    PRIMARY_M within M_TOLERANCE_PCT, cut ESR on all three coils by
    MIN_ESR_IMPROVEMENT_PCT each, and - as preferences only - don't grow
    tank/RC outer diameter or self-inductance (the old scorer's built-in
    tie-breakers, which no longer exist outside this default)."""
    return [
        Objective(metric=PRIMARY_M, kind="match", tolerance_pct=M_TOLERANCE_PCT),
        Objective(metric="loop_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT),
        Objective(metric="tank_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT),
        Objective(metric="rc_esr", kind="reduce", tolerance_pct=MIN_ESR_IMPROVEMENT_PCT),
        *(Objective(metric=m, kind="reduce", tolerance_pct=0.0, required=False)
          for m in ("tank_outer_diameter", "rc_outer_diameter", "L_tank", "L_rc")),
    ]


@dataclasses.dataclass
class AgentSpec:
    baseline_config_id: str = BASELINE_CONFIG_ID
    distances: list = dataclasses.field(default_factory=lambda: list(DISTANCES))
    objective_text: str = ""  # free text from an interview, not yet consumed by optimize.py
    # What the search must achieve (required objectives) and would like
    # (preferences), each checked at every distance - see agent/objective.py.
    objectives: list = dataclasses.field(default_factory=_default_objectives)
    max_tool_calls: int = MAX_TOOL_CALLS
    keep_recent_rounds: int = KEEP_RECENT_ROUNDS
    variable_spec: VariableSpec = dataclasses.field(default_factory=VariableSpec)
    notes: str = ""
