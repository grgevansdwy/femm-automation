"""Physics guidance handed to the model, and the system-prompt builder.
Kept separate from schema.py on purpose: this is the file you'll keep
hand-tuning, so it shouldn't be buried in validation code.

Nothing here assumes what the search is for or which coilset it runs on:
the guidance says what each lever does to every quantity (L, M/k, ESR,
diameter) in both directions, and everything baseline- or run-specific
(radii, distances, allowed thicknesses, which feature fields are free,
the objectives and their baseline values) is computed from the actual
baseline and spec each time.

Geometry-validity and outer-diameter claims are exact (copied from
services/femm_service.py's check_valid_coil()); the inductance/ESR direction
and skin-depth claims are coil-theory heuristics, refined against real
results from this project's own test runs - correct here first if the agent
acts weird.
"""

import dataclasses
import json

from agent.objective import QUANTITY_GLOSSES, is_per_distance
from agent.schema import _THICKNESS_ENUM
from agent.scorer import coil_diameters
from agent.variables import FEATURE_FLAGS

_LEVERS = """
Which levers move which quantity:
- Self-inductance (L) and mutual inductance / coupling (M, k) are set mainly
  by coil AREA and TURN COUNT: radius (*_r, *_radius, *_center_r),
  num_*_turns, num_*_layers - plus any ferrite or metal near the coils.
- ESR is set mainly by COPPER CROSS-SECTION and PROXIMITY LOSS: trace
  thickness, trace width, turn spacing, parallel traces.
These two sets barely overlap: to move one family while holding the other,
change that family's own levers and leave the other family's alone.

Per lever (each works in both directions - reverse the change to reverse
the effect):
- radius: larger -> higher L, higher M/k with the coils it faces, larger
  outer diameter, slightly higher ESR (longer trace per turn).
- num_*_turns: self-L scales roughly with turns SQUARED; M between two
  coils scales with the PRODUCT of their turn counts (N_a * N_b). More turns
  also raise ESR (more copper in series) and widen coil_width.
- num_*_layers: stacks turns in Z instead of radially - raises L and M like
  more turns, without the outer-diameter cost. Adds Z-height, which slightly
  increases effective separation. Layers share the uneven-current problem
  of parallel traces.
- *_trace_thickness (allowed values: {thicknesses}): thicker lowers ESR
  only while thickness stays at or under the copper skin depth (~0.206mm at
  100kHz, ~0.146mm at 200kHz, ~0.065mm at 1MHz); beyond ~2x skin depth it
  stops helping and proximity loss grows. Little effect on L, M or diameter.
- *_turn_spacing: wider spacing lowers proximity loss (lower ESR) and
  slightly lowers L (weaker turn-to-turn coupling); tighter does the
  opposite. Changes outer diameter by (num_turns-1) * the spacing change.
- *_trace_width: wider -> more copper -> lower DC resistance, but the
  benefit saturates and can reverse (eddy currents across a wide trace in
  its own field). Bounded by validity: with one parallel trace,
  turn_width = trace_width must stay below turn_spacing.
- num_parallel_*_traces: lowers ESR, but current does not split evenly (the
  inner trace is shorter and lower-impedance, so current crowds into it),
  so the gain is well short of 1/N - judge it by the returned ESR. Widens
  the coil by (num_parallel_traces-1)*trace_spacing.
- *_trace_spacing: mostly geometry/validity; same proximity logic as
  turn_spacing at a smaller scale.""".strip()

_FERRITE_NOTE = """- Ferrite ({fields}): higher permeability (mu), larger or thicker
  ferrite, or ferrite closer to the winding raises L and M/k by guiding
  flux; its loss angle adds core loss, which shows up as ESR.""".strip()

_METAL_NOTE = """- Metal shield/backplane ({fields}): conductive metal near a coil lowers
  L and M/k (eddy currents oppose the field) and adds eddy-current loss
  (higher ESR); the closer and larger it is, the stronger both effects.""".strip()

_METAL_FEATURES = ("tc_shield", "rc_shield", "backplane", "rc_backplane")
_FERRITE_FEATURES = ("tank_ferrite", "rc_ferrite")

_VALIDITY = """
Geometry validity (exact - from services/femm_service.py check_valid_coil):
  turn_width = (num_parallel_traces-1)*trace_spacing + trace_width
  coil_width = (num_turns-1)*turn_spacing + turn_width
INVALID if center_radius < coil_width/2 (winding crosses the axis). Overlap
checks (trace_spacing < trace_width, turn_spacing < turn_width) apply only
where there are multiple parallel traces / multiple turns respectively - a
single-trace or single-turn coil legitimately has spacing 0.
Outer diameter = 2*(center_radius + coil_width/2). Widening traces or
opening spacing grows coil_width, which grows outer diameter AND pushes
toward the center_radius < coil_width/2 wall - re-check both.""".strip()


def physics_reference(start, free_fields: dict, distances) -> str:
    """The heuristic guidance, specialized to this run: `start` is the
    CoilSet every candidate starts from (baseline + any pinned values)."""
    thicknesses = ", ".join(f"{t:g} ({round(t / 0.035)}oz)" for t in _THICKNESS_ENUM)
    notes = []
    for features, template in ((_FERRITE_FEATURES, _FERRITE_NOTE), (_METAL_FEATURES, _METAL_NOTE)):
        free = [f for feat in features for f in [FEATURE_FLAGS[feat]["flag"], *FEATURE_FLAGS[feat]["fields"]]
                if f in free_fields]
        if free:
            notes.append(template.format(fields=", ".join(free)))

    radii = {"loop": start.loop_r, "tank": start.tank_center_r, "rc": start.rc_radius}
    radius_text = ", ".join(f"{name} {r:g}mm" for name, r in radii.items())
    smallest = min(radii.values())
    ratio_text = f" - so the largest gap is {max(distances) / smallest:.2f}x the smallest radius" if smallest > 0 else ""
    distance_text = (
        f"Distance (RC separation) is NOT proposable: every candidate is evaluated at {list(distances)} mm.\n"
        f"Coil center radii at the start point: {radius_text}{ratio_text}. "
        "When the gap is small compared with the radii, coupling is\n"
        "near-field and changes gently with distance (not a steep 1/d^3 dipole falloff), so an offset M/k\n"
        "tends to show at every distance at once; as the gap approaches the radius, M/k fall off\n"
        "increasingly steeply and the farthest distance becomes the hardest to hold."
    )
    return "\n\n".join([
        "Physics reference - heuristics to guide your search. FEMM is ground truth: when a returned "
        "result contradicts anything below, trust the result.",
        _LEVERS.format(thicknesses=thicknesses) + ("\n" + "\n".join(notes) if notes else ""),
        _VALIDITY,
        distance_text,
    ])


def _baseline_text(objective, start, baseline_metrics) -> str:
    if is_per_distance(objective.metric):
        values = [baseline_metrics[d][objective.metric] for d in sorted(baseline_metrics)]
        if min(values) == max(values):
            return f"baseline {values[0]:.4g}"
        return f"baseline {min(values):.4g} to {max(values):.4g} across distances"
    design = {**dataclasses.asdict(start), **coil_diameters(start)}
    return f"baseline {design[objective.metric]:.4g}"


def system_prompt(baseline, free_fields: dict, spec, baseline_metrics: dict) -> str:
    """One-time system message: the objective, the baseline, and the physics
    guidance. `spec` is an agent.spec.AgentSpec, left untyped here so this
    module doesn't need to import agent.spec just for a hint;
    `baseline_metrics` is distance -> the baseline's solved SimResult dict."""
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
    return f"""You are proposing coilset geometries for a wireless power transfer coil, one
candidate per turn, using the propose_coilset tool. The operating frequency is 6.78MHz. Only propose valid tool
calls - no free text needed once you understand the task. Always fill in the
tool's `rationale` argument with your reasoning summarized in 1-2 sentences
max: what the previous result told you (if there was one) and why this
specific change should move the objective. That summary is the only
reasoning the person watching the search reads, so make it plain and
specific, no lists.

Baseline: {spec.baseline_config_id}. Fields you may change, with their baseline values:
{json.dumps(free_values, indent=2)}
{pinned}Every other field is fixed - you cannot change it.

Objectives, each compared to the ORIGINAL baseline above:
{objective_lines}
  Simulated quantities are checked at every distance ({spec.distances} mm) and the
  worst distance decides; design variables and outer diameters have no distance.
  ALL required objectives must pass at once for the goal to be met - a large
  win on one does not make up for missing another. Coils are
  proximity-coupled, so a change aimed at one quantity can move another -
  watch every objective, not just the one you are working on. Preferred
  objectives only break ties between candidates; nothing else is ranked.

{physics_reference(start, free_fields, spec.distances)}

After each proposal you'll get the result back: per objective, its value,
its % change vs. baseline (at each distance, for simulated quantities),
whether it passed, and its margin (>= 0 passes; negative = how far short,
as a fraction of its tolerance, so margins of different objectives compare
directly). The required objective with the most negative margin is your
current bottleneck - fix that one next, while keeping any "match"
objectives inside their bands. goal_met is true only once every required
objective passes. Stop calling the tool (respond with plain text instead)
once you believe you've found a good candidate or are stuck."""
