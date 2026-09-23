"""The search prompt's builder: works out everything specific to this run
(objectives, baseline values, free fields, radii, distances) and fills it
into the search prompt template - the wording itself is in
agent/settings/prompts/search_system.txt and editable from the GUI's Settings
screen (see agent/settings/prompt_defs.py for its placeholders).

Nothing here assumes what the search is for or which coilset it runs on:
everything baseline- or run-specific (radii, distances, allowed thicknesses,
which feature fields are free, the objectives and their baseline values) is
computed from the actual baseline and spec each time.

The default wording's geometry-validity and outer-diameter claims are exact
(copied from engine/services/femm_service.py's check_valid_coil()); the
inductance/ESR direction and skin-depth claims are coil-theory heuristics,
refined against real results from this project's own test runs - correct
them first if the agent acts weird.
"""

import dataclasses
import json

from agent.input_chat.models.objective import QUANTITY_GLOSSES, is_per_distance
from agent.femm_agent.models.schema import _THICKNESS_ENUM
from agent.femm_agent.services.scorer import coil_diameters
from agent.input_chat.models.variables import CONSENT_GROUPS
from agent.settings.settings import AgentSettings
from agent.settings.templating import render

_METAL_FEATURES = ("tc_shield", "rc_shield", "backplane", "rc_backplane")
_FERRITE_FEATURES = ("tank_ferrite", "rc_ferrite")


def _physics_values(start, free_fields: dict, distances) -> dict:
    """The run-specific numbers the search prompt's physics guidance quotes:
    allowed copper thicknesses, which ferrite / metal fields are free (empty
    when none, so the prompt's ferrite and metal notes drop out), and the coil
    radii. `start` is the CoilSet every candidate starts from (baseline + any
    pinned values)."""
    def free(features):
        return ", ".join(f for feat in features for f in [CONSENT_GROUPS[feat]["flag"], *CONSENT_GROUPS[feat]["fields"]]
                         if f in free_fields)

    radii = {"loop": start.loop_r, "tank": start.tank_center_r, "rc": start.rc_radius}
    radius_text = ", ".join(f"{name} {r:g}mm" for name, r in radii.items())
    smallest = min(radii.values())
    ratio_text = f" - so the largest gap is {max(distances) / smallest:.2f}x the smallest radius" if smallest > 0 else ""
    return {
        "thicknesses": ", ".join(f"{t:g} ({round(t / 0.035)}oz)" for t in _THICKNESS_ENUM),
        "ferrite_fields": free(_FERRITE_FEATURES),
        "metal_fields": free(_METAL_FEATURES),
        "radii": f"{radius_text}{ratio_text}",
    }


def _baseline_text(objective, start, baseline_metrics) -> str:
    if is_per_distance(objective.metric):
        values = [baseline_metrics[d][objective.metric] for d in sorted(baseline_metrics)]
        if min(values) == max(values):
            return f"baseline {values[0]:.4g}"
        return f"baseline {min(values):.4g} to {max(values):.4g} across distances"
    design = {**dataclasses.asdict(start), **coil_diameters(start)}
    return f"baseline {design[objective.metric]:.4g}"


def system_prompt(baseline, free_fields: dict, spec, baseline_metrics: dict) -> str:
    """One-time system message: the objective, the baseline, how to propose
    candidates, and the physics guidance. `spec` is an agent.input_chat.models.spec.AgentSpec (left untyped
    so this module doesn't import it just for a hint), whose `settings` supply
    the wording; `baseline_metrics` is distance -> the baseline's solved
    SimResult dict."""
    settings = spec.settings
    fixed = spec.variable_spec.fixed_values
    start = dataclasses.replace(baseline, **fixed)
    free_values = {k: v for k, v in dataclasses.asdict(baseline).items() if k in free_fields}
    objective_lines = "\n".join(
        f"  {'REQUIRED' if o.required else 'PREFERRED'}: {o.kind} {o.metric} ({QUANTITY_GLOSSES[o.metric]}) - "
        f"{o.describe()}  [{'every distance' if is_per_distance(o.metric) else 'no distance'}; "
        f"{_baseline_text(o, baseline, baseline_metrics)}]"
        for o in spec.objectives
    )
    pinned = (f"Pinned by the user - already applied to every candidate, not yours to change: {json.dumps(fixed)}\n"
              if fixed else "")
    return render(settings.prompt("search_system"), {
        "baseline_config_id": spec.baseline_config_id,
        "objectives": objective_lines,
        "free_fields": json.dumps(free_values, indent=2),
        "pinned": pinned,
        "distances": spec.distances,
        # Several candidates per turn is a different instruction from one at a
        # time, so the count is only given when it is more than one.
        "n_candidates": spec.candidates_per_round if spec.candidates_per_round > 1 else "",
        "keep_best": spec.keep_best_candidates,
        **_physics_values(start, free_fields, spec.distances),
    })
