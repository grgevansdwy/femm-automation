"""The editable system prompts - one per conversation the app has with the AI
(interview, search, follow-up). Each PromptDef gives its key, its title and
description in the Settings screen, which {{placeholders}} it understands, and
its default text. A prompt is ONE piece of text: parts that only apply
sometimes (several candidates vs one, ferrite fields open, search running vs
finished) are {{#name}}...{{/name}} blocks inside it, not separate prompts.

Default text is in prompts/<key>.txt so it reads as plain text, not as Python
strings. Placeholders are filled in by the code that builds each prompt
(femm_agent/services/prompts.py, input_chat/services/interview.py,
femm_agent/services/followup.py) - a prompt can only use the ones listed here,
because those are the only values that code computes.
"""

import dataclasses
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "prompts"


@dataclasses.dataclass(frozen=True)
class PromptDef:
    key: str
    title: str
    help: str
    placeholders: dict  # name -> what the app fills in
    required: tuple = ()  # placeholders the prompt stops working without
    switches: tuple = ()  # placeholders that may also open a {{#name}}...{{/name}} block (kept only when the value is non-empty)

    @property
    def default(self) -> str:
        return default_text(self.key)


@lru_cache(maxsize=None)
def default_text(key: str) -> str:
    return (_DIR / f"{key}.txt").read_text().rstrip("\n")


PROMPT_DEFS = {d.key: d for d in [
    PromptDef("search_system", "FEMM agent",
              "Sent to the AI at the start of every design search. It carries the objective, the baseline, "
              "how to propose candidates, and the physics guidance - everything the AI knows while searching.",
              {"baseline_config_id": "the baseline's name",
               "objectives": "the objectives, one per line, with baseline values",
               "free_fields": "the fields the AI may change, with baseline values (JSON)",
               "pinned": "values you pinned (empty if none)",
               "distances": "the distances being simulated, e.g. [3.0, 5.0]",
               "n_candidates": "candidates per turn when it is more than one (empty when one at a time)",
               "keep_best": "how many best candidates are re-shown each turn",
               "thicknesses": "the allowed copper thicknesses",
               "ferrite_fields": "the ferrite fields you opened up (empty if none)",
               "metal_fields": "the shield/backplane fields you opened up (empty if none)",
               "radii": "the coil radii at the start point, with the gap-to-radius ratio"},
              required=("objectives", "free_fields"),
              switches=("n_candidates", "ferrite_fields", "metal_fields")),

    PromptDef("interview_system", "Input chat",
              "Sent to the AI at the start of the chat where it turns your request into an objective. "
              "It tells the AI what it may ask about and what a finished objective looks like.",
              {"baseline_config_id": "the baseline's name",
               "present": "optional structural details the baseline has",
               "absent": "optional structural details the baseline lacks",
               "sim_lines": "the simulated quantities you can ask about, one per line",
               "derived_lines": "outer-diameter quantities with their baseline values",
               "variable_lines": "design variables free by default with their baseline values",
               "default_distances": "the built-in default distances, e.g. [3.0, 5.0]"},
              required=("sim_lines", "derived_lines", "variable_lines")),

    PromptDef("followup_system", "Follow up chat",
              "Sent to the AI for the chat after a search has started. It answers questions about the run "
              "and changes nothing (typing \"continue\" is handled by the app, not by this prompt).",
              {"running": "set while the search is still running, empty once it has finished",
               "run_data": "the live snapshot of the run: plan, budget used, progress verdict, baseline, rounds, "
                           "best candidate"},
              required=("run_data",),
              switches=("running",)),
]}
