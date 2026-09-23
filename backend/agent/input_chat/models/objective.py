"""The objective vocabulary: what a search is trying to achieve, as a list
of Objective entries over any quantity a candidate has - a simulated result
(solved at every distance), a design variable (a CoilSet field), or a
derived geometry value (outer diameter). Replaces AgentSpec's old fixed
shape - see docs/objective_generalization_plan.md.

Nothing here assumes what a search is FOR: there are no built-in goals or
tie-breakers, only what the spec's objectives say.
"""

# In simple, what is the search trying to achieve
import dataclasses

from engine.models.coilset import CoilSet

# Result model (12 variables output)
SIM_RESULT_METRICS = (
    "L_loop", "L_tank", "L_rc",
    "M12", "M13", "M23",
    "loop_esr", "tank_esr", "rc_esr",
    "k12", "k13", "k23",
)

# Geometry values derived from several fields (scorer.coil_diameters()) -
# no distance, same as a design variable.
DERIVED_QUANTITIES = ("loop_outer_diameter", "tank_outer_diameter", "rc_outer_diameter")

# All coilset variable who have type int or float
DESIGN_VARIABLES = tuple(f.name for f in dataclasses.fields(CoilSet) if f.type in (int, float))

# All english words with the output, input, and derivative variables
QUANTITY_GLOSSES = {
    "L_loop": "loop coil self-inductance (H)",
    "L_tank": "tank coil self-inductance (H)",
    "L_rc": "RC (receive) coil self-inductance (H)",
    "M12": "loop <-> tank mutual inductance (H)",
    "M13": "loop <-> RC mutual inductance (H)",
    "M23": "tank <-> RC mutual inductance (H), the power-transfer link",
    "loop_esr": "loop coil ESR, AC resistance (Ohm)",
    "tank_esr": "tank coil ESR, AC resistance (Ohm)",
    "rc_esr": "RC coil ESR, AC resistance (Ohm)",
    "k12": "loop <-> tank coupling coefficient",
    "k13": "loop <-> RC coupling coefficient",
    "k23": "tank <-> RC coupling coefficient",
    "loop_outer_diameter": "loop coil outer diameter (mm)",
    "tank_outer_diameter": "tank coil outer diameter (mm)",
    "rc_outer_diameter": "RC coil outer diameter (mm)",
    **{f.name: f.metadata.get("label", f.name) for f in dataclasses.fields(CoilSet) if f.name in DESIGN_VARIABLES},
}

# Relative (vs. baseline):
#   match    - within +/-tolerance_pct of target_pct (% change; 0 = hold baseline)
#   reduce   - drop by AT LEAST tolerance_pct (0 = just don't get worse)
#   increase - rise by AT LEAST tolerance_pct
# Absolute (target_value set, in the quantity's own units):
#   match    - within +/-tolerance_pct of target_value
#   reduce   - at most target_value
#   increase - at least target_value
KINDS = ("match", "reduce", "increase")

# Margins are normalized by the objective's tolerance so objectives at very
# different natural scales rank fairly against each other. A tolerance below
# this floor normalizes as if it were this wide instead - otherwise a 0%
# tolerance divides by zero and a 0.1% one inflates every margin 10x.
MIN_SCALE_PCT = 1.0


def is_per_distance(quantity: str) -> bool:
    return quantity in SIM_RESULT_METRICS


@dataclasses.dataclass(frozen=True)
class Objective:
    metric: str             # the quantity: a SIM_RESULT_METRICS entry, DERIVED_QUANTITIES entry, or DESIGN_VARIABLES field
    kind: str               # one of KINDS
    tolerance_pct: float    # >= 0
    required: bool = True   # True = hard requirement (blocks goal_met); False = preference (ranking only)
    target_pct: float = 0.0  # relative match only: the % change vs baseline to hit, e.g. -10 for "10% lower"
    target_value: float | None = None  # absolute target/bound, in the quantity's own units (replaces target_pct)

    def __post_init__(self):
        if self.metric not in QUANTITY_GLOSSES:
            raise ValueError(f"unknown quantity {self.metric!r} - must be a simulated result "
                             f"{list(SIM_RESULT_METRICS)}, a derived value {list(DERIVED_QUANTITIES)}, "
                             "or a design variable (CoilSet field)")
        if self.kind not in KINDS:
            raise ValueError(f"unknown objective kind {self.kind!r} - must be one of {list(KINDS)}")
        if self.tolerance_pct < 0:
            raise ValueError(f"{self.metric}: tolerance_pct must be >= 0, got {self.tolerance_pct}")
        if self.target_value is not None:
            if self.target_pct != 0:
                raise ValueError(f"{self.metric}: give target_value OR target_pct, not both")
            if self.target_value == 0:
                raise ValueError(f"{self.metric}: target_value can't be 0 - tolerances are a % of it")
            if self.kind != "match" and self.tolerance_pct != 0:
                raise ValueError(f"{self.metric}: {self.kind} with target_value is a plain bound "
                                 f"({'at most' if self.kind == 'reduce' else 'at least'} target_value) - "
                                 "set tolerance_pct to 0")
        elif self.kind != "match" and self.target_pct != 0:
            raise ValueError(f"{self.metric}: target_pct only applies to kind='match' - "
                             f"use tolerance_pct for how far to {self.kind}")
        if self.kind == "match" and self.required and self.tolerance_pct == 0:
            raise ValueError(f"{self.metric}: a required match needs tolerance_pct > 0 - an exact float "
                             "match can never pass (to set a design variable to an exact value, pin it "
                             "with fixed_values instead)")

    def describe(self) -> str:
        """Short, ASCII-only requirement text (the GUI's font atlas is Basic
        Latin only) - e.g. "within +/-5% of baseline", ">= 10% lower"."""
        if self.target_value is not None:
            if self.kind == "match":
                return f"within +/-{self.tolerance_pct:g}% of {self.target_value:g}"
            return f"{'<=' if self.kind == 'reduce' else '>='} {self.target_value:g}"
        if self.kind == "match":
            where = "baseline" if self.target_pct == 0 else f"{self.target_pct:+g}% vs baseline"
            return f"within +/-{self.tolerance_pct:g}% of {where}"
        if self.tolerance_pct == 0:
            return "no higher than baseline" if self.kind == "reduce" else "no lower than baseline"
        return f">= {self.tolerance_pct:g}% {'lower' if self.kind == 'reduce' else 'higher'}"


def margin(objective: Objective, change_pct: float, value: float | None = None) -> float:
    """How far a result is inside (>= 0, passes) or outside (< 0, fails)
    the objective, as a fraction of its tolerance - so missing a 5% band by
    0.5pp (-0.10) reads as worse than missing a 50% reduction target by
    0.5pp (-0.01). `change_pct` is the % change vs baseline; `value` is the
    raw value, needed only for an absolute target_value."""
    if objective.target_value is not None:
        deviation_pct = (value - objective.target_value) / abs(objective.target_value) * 100.0
        if objective.kind == "match":
            raw = objective.tolerance_pct - abs(deviation_pct)
        else:
            raw = -deviation_pct if objective.kind == "reduce" else deviation_pct
    elif objective.kind == "match":
        raw = objective.tolerance_pct - abs(change_pct - objective.target_pct)
    elif objective.kind == "reduce":
        raw = -change_pct - objective.tolerance_pct
    else:  # increase
        raw = change_pct - objective.tolerance_pct
    return raw / max(objective.tolerance_pct, MIN_SCALE_PCT)
