"""AgentSpec: the fully-resolved objective for one agent run - the one
contract between "how the objective was gathered" (an interview
conversation, agent/input_chat/services/interview.py) and "what the optimize loop searches for"
(agent/femm_agent/services/optimize.py). optimize.py never needs to know which produced it.

Field defaults come from agent/config.py's constants - a caller that builds an
AgentSpec with no arguments gets exactly the hardcoded CLI behavior;
AgentSpec.from_settings() takes the search budget from the saved settings
instead. The objective itself is never a setting: the interview replaces it
with what the user asked for.
"""

import dataclasses

from agent import config
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.variables import VariableSpec
from agent.settings.settings import AgentSettings


def _default_objectives() -> list[Objective]:
    """The CLI's original hardcoded goal, spelled out as objectives: hold
    the primary M within M_TOLERANCE_PCT, cut ESR on all three coils by
    MIN_ESR_IMPROVEMENT_PCT each (agent/config.py), and - as preferences only - don't grow
    tank/RC outer diameter or self-inductance (the old scorer's built-in
    tie-breakers, which no longer exist outside this default)."""
    return [
        Objective(metric=config.PRIMARY_M, kind="match", tolerance_pct=config.M_TOLERANCE_PCT),
        Objective(metric="loop_esr", kind="reduce", tolerance_pct=config.MIN_ESR_IMPROVEMENT_PCT),
        Objective(metric="tank_esr", kind="reduce", tolerance_pct=config.MIN_ESR_IMPROVEMENT_PCT),
        Objective(metric="rc_esr", kind="reduce", tolerance_pct=config.MIN_ESR_IMPROVEMENT_PCT),
        *(Objective(metric=m, kind="reduce", tolerance_pct=0.0, required=False)
          for m in ("tank_outer_diameter", "rc_outer_diameter", "L_tank", "L_rc")),
    ]


@dataclasses.dataclass
class AgentSpec:
    baseline_config_id: str = config.BASELINE_CONFIG_ID
    distances: list = dataclasses.field(default_factory=lambda: list(config.DISTANCES))
    objective_text: str = ""  # free text from an interview, not yet consumed by optimize.py
    # What the search must achieve (required objectives) and would like
    # (preferences), each checked at every distance - see agent/input_chat/models/objective.py.
    objectives: list = dataclasses.field(default_factory=_default_objectives)
    max_tool_calls: int = AgentSettings().max_tool_calls
    candidates_per_round: int = AgentSettings().candidates_per_round  # proposals per LLM reply, solved concurrently
    keep_best_candidates: int = AgentSettings().keep_best_candidates  # best candidates resent each round, see optimize.search_memory()
    variable_spec: VariableSpec = dataclasses.field(default_factory=VariableSpec)
    notes: str = ""
    # The settings (prompt wording, LLM and retry limits) this run uses, copied
    # in when it starts so editing the Settings screen mid-run can't change it.
    settings: AgentSettings = dataclasses.field(default_factory=AgentSettings)

    @classmethod
    def from_settings(cls, settings: AgentSettings, baseline_config_id: str | None = None) -> "AgentSpec":
        """A spec with `settings`' search budget, the built-in distances and
        objectives (the CLI's whole objective; the interview replaces them with
        what the user asked for), and the settings themselves recorded for the
        run. Anything not passed to AgentSpec() directly starts from here."""
        return cls(
            baseline_config_id=baseline_config_id or config.BASELINE_CONFIG_ID,
            distances=list(config.DISTANCES),
            objectives=_default_objectives(),
            max_tool_calls=settings.max_tool_calls,
            candidates_per_round=settings.candidates_per_round,
            keep_best_candidates=settings.keep_best_candidates,
            settings=settings,
        )
