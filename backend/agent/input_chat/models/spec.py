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
from agent.settings.settings import AgentSettings, settings_from_dict


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
    # The one required reduce/increase objective the user wants pushed as far as
    # possible: passing designs are then compared on it alone (scorer.rank_key).
    # None = compare them on the blended score over all of them.
    favourite_metric: str | None = None
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


def spec_from_dict(data: dict) -> AgentSpec:
    """Rebuilds a spec from a run's stored copy (dataclasses.asdict output, as
    ai_runs.spec_json holds it). Keys this version doesn't know are ignored and
    missing ones fall back to the defaults, so an older run still opens."""
    raw_vars = dict(data.get("variable_spec") or {})
    known_vars = {f.name for f in dataclasses.fields(VariableSpec)}
    variable_spec = VariableSpec(**{
        k: (set(v or []) if k in ("enabled_groups", "excluded_fields") else v)
        for k, v in raw_vars.items() if k in known_vars})
    known_obj = {f.name for f in dataclasses.fields(Objective)}
    simple = {k: data[k] for k in ("baseline_config_id", "distances", "objective_text", "max_tool_calls",
                                   "candidates_per_round", "keep_best_candidates", "favourite_metric", "notes")
              if k in data}
    return AgentSpec(
        **simple, variable_spec=variable_spec,
        objectives=[Objective(**{k: v for k, v in o.items() if k in known_obj}) for o in data.get("objectives") or []],
        settings=settings_from_dict(data.get("settings")),
    )
