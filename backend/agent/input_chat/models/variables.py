"""Which CoilSet fields the LLM may propose changing, and within what limits.
schema.py owns the JSON-schema *format* of a field (the _num/_int/_thickness
builders, coerce_delta, Rejection); this file owns *which fields are eligible*
and why.

Permission is one table the user controls - the "Field Config" page in
Settings (AgentSettings.search_fields): per field an enabled flag, an optional
minimum and an optional maximum. DEFAULT_SEARCH_FIELDS is what ships: the base
winding geometry enabled, everything else (ferrite, shields, backplanes, PCB
thickness, loss angles) disabled, and at most 8 layers. The interview model can
propose a change to the table but never applies one, and nothing a chat says
opens a field by itself.

On top of the table, the interview's VariableSpec can only narrow a search:
excluded_fields stay at baseline, fixed_values are pinned on every candidate.

Code floors hold whatever the table says: a dimension is >= 0, a count is >= 1,
a flag is 0 or 1, a copper thickness is one of the real copper weights.
"""
# In simple, what input variables is allowed to touch

import dataclasses
import math
import types

from agent.femm_agent.models.schema import _THICKNESS_ENUM, _int, _num, _thickness, coerce_delta, limit_text
from engine.models.coilset import CoilSet

# The base winding geometry - what the search may change as shipped
# (DEFAULT_SEARCH_FIELDS), and what was always free before the table existed.
FREE_VARIABLES = [
    "loop_offset", "loop_r", "num_loop_turns", "num_parallel_loop_traces",
    "num_loop_layers", "loop_trace_spacing", "loop_turn_spacing",
    "loop_trace_width", "loop_trace_thickness",
    "tank_center_r", "num_tank_turns", "num_parallel_tank_traces",
    "num_tank_layers", "tank_trace_spacing", "tank_turn_spacing",
    "tank_trace_width", "tank_trace_thickness",
    "rc_radius", "num_rc_turns", "num_parallel_rc_traces",
    "num_rc_layers", "rc_trace_spacing", "rc_turn_spacing",
    "rc_trace_width", "rc_trace_thickness",
]

# Names for sets of related fields: a group name in excluded_fields stands for
# all of its fields, and the search prompt's ferrite / metal notes list the
# open fields of theirs. A group grants nothing - only the table does.
FIELD_GROUPS = {
    "tank_ferrite": {
        "flag": "tank_ferrite_present",
        "fields": ["tank_ferrite_y_offset", "tank_ferrite_thickness", "tank_mu",
                   "tank_ferrite_outer_r", "tc_loss_angle"],
    },
    "rc_ferrite": {
        "flag": "rc_ferrite_present",
        "fields": ["rc_ferrite_y_offset", "rc_ferrite_thickness", "rc_mu", "rc_ferrite_outer_r", "rc_loss_angle"],
    },
    "tc_shield": {
        "flag": "tc_shield",
        "fields": ["tc_shield_y_distance", "tc_shield_radius", "tc_shield_thickness"],
    },
    "rc_shield": {
        "flag": "rc_shield",
        "fields": ["rc_shield_y_distance", "rc_shield_radius", "rc_shield_thickness"],
    },
    "backplane": {
        "flag": "backplane",
        "fields": ["backplane_y_distance"],
    },
    "rc_backplane": {
        "flag": "rc_backplane",
        "fields": ["rc_backplane_y_distance"],
    },
    "pcb_thickness": {
        "flag": None,  # no on/off toggle - the fields themselves always exist
        "fields": ["tank_PCB_thickness", "loop_PCB_thickness", "rc_PCB_thickness"],
    },
}

# Groups a run stored before the Field Config table existed may list in
# VariableSpec.enabled_groups (see legacy_allowed_fields()). rc_ferrite_y_offset
# was always free then, so it is not part of the old rc_ferrite group.
_LEGACY_GROUP_FIELDS = {
    group: ([info["flag"]] if info["flag"] else []) + [f for f in info["fields"] if f != "rc_ferrite_y_offset"]
    for group, info in FIELD_GROUPS.items()
}


def _group_fields(group: str) -> list[str]:
    """A group's own field names, its toggle flag included if it has one."""
    info = FIELD_GROUPS[group]
    return ([info["flag"]] if info["flag"] else []) + info["fields"]


# On/off fields: declared int on CoilSet, only ever 0 or 1.
FLAG_FIELDS = frozenset(info["flag"] for info in FIELD_GROUPS.values() if info["flag"])

# Thickness fields are not continuous - only 3 real copper weights exist.
THICKNESS_FIELDS = frozenset({"loop_trace_thickness", "tank_trace_thickness", "rc_trace_thickness"})

LAYER_FIELDS = ("num_loop_layers", "num_tank_layers", "num_rc_layers")
DEFAULT_MAX_LAYERS = 8  # 12 and 16 layers crashed or timed out the solver in every stored case; 8 always solved

_FIELD_TYPES = {f.name: f.type for f in dataclasses.fields(CoilSet)}
_FIELD_LABELS = {f.name: f.metadata.get("label", f.name) for f in dataclasses.fields(CoilSet)}

# Every field the table has a row for, in CoilSet order.
NUMERIC_FIELDS = tuple(name for name, kind in _FIELD_TYPES.items() if kind in (int, float))


@dataclasses.dataclass(frozen=True)
class FieldRule:
    """One row of the Field Config table. min/max are plain numbers in the
    field's own unit; None = no limit on that side."""
    enabled: bool = False
    min: float | None = None
    max: float | None = None


# Limits every search starts with: (minimum, maximum) in the field's own unit, None = no limit
# on that side. They keep a search out of degenerate coils (a one-turn tank, a tank a few mm wide)
# and out of sizes no board would use. PLACEHOLDER numbers, not engineering advice: each is the
# range the starter configs span, widened by a margin, so every starter config still opens (the
# baseline must be inside its limits). Change them per field in Settings > Field Config.
DEFAULT_LIMITS = {
    "loop_offset": (0, 20),
    "loop_r": (25, 100),
    "num_loop_turns": (1, 3),
    "num_parallel_loop_traces": (1, 10),
    "loop_trace_spacing": (0, 10),
    "loop_turn_spacing": (0, 30),
    "loop_trace_width": (None, 6),
    "tank_center_r": (50, 120),
    "num_tank_turns": (3, 12),
    "num_parallel_tank_traces": (1, 4),
    "tank_trace_spacing": (0, 8),
    "tank_turn_spacing": (2.5, 16),
    "tank_trace_width": (None, 10),
    "rc_radius": (15, 80),
    "num_rc_turns": (2, 8),
    "num_parallel_rc_traces": (1, 4),
    "rc_trace_spacing": (0, 8),
    "rc_turn_spacing": (1.5, 12),
    "rc_trace_width": (None, 6),
}


def _default_rule(name: str) -> FieldRule:
    low, high = DEFAULT_LIMITS.get(name, (None, None))
    return FieldRule(enabled=name in FREE_VARIABLES, min=low, max=DEFAULT_MAX_LAYERS if name in LAYER_FIELDS else high)


DEFAULT_SEARCH_FIELDS = {name: _default_rule(name) for name in NUMERIC_FIELDS}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def search_field_problems(overrides) -> list[str]:
    """Everything wrong with a saved table (AgentSettings.search_fields), as
    messages a person can act on. An unknown field is not a problem here - it
    is ignored when the table is read (search_field_table())."""
    if not isinstance(overrides, dict):
        return ["Field Config: must be a table of field -> {enabled, min, max}"]
    problems = []
    for name, row in overrides.items():
        if name not in DEFAULT_SEARCH_FIELDS:
            continue
        if not isinstance(row, dict):
            problems.append(f"Field Config, {name}: must be {{enabled, min, max}}")
            continue
        if "enabled" in row and not isinstance(row["enabled"], bool):
            problems.append(f"Field Config, {name}: enabled must be on or off")
        low, high = row.get("min"), row.get("max")
        for side, value in (("minimum", low), ("maximum", high)):
            if value is not None and not _is_number(value):
                problems.append(f"Field Config, {name}: {side} must be a number or left empty")
        if _is_number(low) and _is_number(high) and low > high:
            problems.append(f"Field Config, {name}: minimum {low:g} is above maximum {high:g}")
    return problems


def search_field_table(settings=None) -> dict[str, FieldRule]:
    """The table in force: the shipped defaults with `settings.search_fields`
    (the rows a person changed) on top. A saved row for a field that no longer
    exists, or one that isn't a usable row, is ignored for that field."""
    table = dict(DEFAULT_SEARCH_FIELDS)
    overrides = getattr(settings, "search_fields", None) or {}
    for name, row in overrides.items():
        if name not in table or not isinstance(row, dict) or search_field_problems({name: row}):
            continue
        default = table[name]
        table[name] = FieldRule(enabled=row.get("enabled", default.enabled),
                                min=row.get("min", default.min), max=row.get("max", default.max))
    return table


def search_field_overrides(table: dict[str, FieldRule]) -> dict:
    """`table` as AgentSettings.search_fields stores it: only the rows that
    differ from the shipped defaults, each written out in full."""
    return {name: dataclasses.asdict(rule) for name, rule in table.items()
            if name in DEFAULT_SEARCH_FIELDS and rule != DEFAULT_SEARCH_FIELDS[name]}


def check_field_changes(changes) -> list[dict]:
    """Validates a proposed change to the table ([{field, enabled?, min?,
    max?}]) and returns it cleaned. Raises ValueError naming the bad entry.
    Checking is all this does - nothing is written."""
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes must be a non-empty list of {field, enabled?, min?, max?}")
    cleaned = []
    for change in changes:
        if not isinstance(change, dict) or change.get("field") not in DEFAULT_SEARCH_FIELDS:
            name = change.get("field") if isinstance(change, dict) else change
            raise ValueError(f"changes names {name!r}, which is not a numeric design field")
        row = {key: change[key] for key in ("enabled", "min", "max") if key in change}
        if not row:
            raise ValueError(f"the change to {change['field']} sets none of enabled, min, max")
        if change["field"] in FLAG_FIELDS and ("min" in row or "max" in row):
            raise ValueError(f"{change['field']} is an on/off field - it has no minimum or maximum")
        problems = search_field_problems({change["field"]: row})
        if problems:
            raise ValueError(problems[0])
        cleaned.append({"field": change["field"], **row})
    return cleaned


def apply_field_changes(table: dict[str, FieldRule], changes: list[dict]) -> dict[str, FieldRule]:
    """`table` with `changes` (see check_field_changes()) applied. A key a
    change leaves out keeps its current value; an explicit null clears a limit."""
    table = dict(table)
    for change in check_field_changes(changes):
        rule = table[change["field"]]
        rule = dataclasses.replace(rule, **{k: v for k, v in change.items() if k != "field"})
        if rule.min is not None and rule.max is not None and rule.min > rule.max:
            raise ValueError(f"Field Config, {change['field']}: minimum {rule.min:g} is above maximum {rule.max:g}")
        table[change["field"]] = rule
    return table


def describe_field_change(change: dict) -> str:
    """One proposed change to the table as a line a person reads before
    clicking Apply - e.g. "tank_mu: open to the search, maximum 2000"."""
    parts = []
    if "enabled" in change:
        parts.append("open to the search" if change["enabled"] else "closed to the search")
    for key, word in (("min", "minimum"), ("max", "maximum")):
        if key in change:
            parts.append(f"no {word}" if change[key] is None else f"{word} {change[key]:g}")
    return f"{change['field']}: " + ", ".join(parts)


def _schema_for(field_name: str, rule: FieldRule | None = None) -> dict:
    """One field's JSON-schema entry: type and label from CoilSet's own
    annotation, the code floor for its kind, and `rule`'s limits on top."""
    rule = rule or FieldRule()
    label = f"{_FIELD_LABELS[field_name]}."
    if field_name in FLAG_FIELDS:
        return {"type": "integer", "enum": [0, 1], "description": f"{label} 0 = off, 1 = on."}
    if field_name in THICKNESS_FIELDS:
        allowed = [t for t in _THICKNESS_ENUM
                   if (rule.min is None or t >= rule.min) and (rule.max is None or t <= rule.max)]
        return _thickness(label, allowed)
    if _FIELD_TYPES[field_name] is int:
        return _int(label, minimum=max(1, math.ceil(rule.min)) if rule.min is not None else 1,
                    maximum=math.floor(rule.max) if rule.max is not None else None)
    return _num(label, minimum=max(0.0, rule.min) if rule.min is not None else 0.0, maximum=rule.max)


@dataclasses.dataclass
class VariableSpec:
    """What an interview gathered about which variables may move."""
    # Read only: runs stored before the Field Config table existed may carry
    # it (see legacy_allowed_fields()). Nothing writes it any more.
    enabled_groups: set[str] = dataclasses.field(default_factory=set)
    excluded_fields: set[str] = dataclasses.field(default_factory=set)  # fields the user said to keep fixed
    # field -> value the user said a variable must BE ("make rc trace
    # thickness 4oz") - applied to every candidate, so it's never searched
    # over and never offered as free. Comparisons stay against the
    # original, un-pinned baseline.
    fixed_values: dict = dataclasses.field(default_factory=dict)

# expands a mix of raw field names and group names into a flat set of actual field names to exclude, erroring on anything unrecognized.
def resolve_excluded(names) -> set[str]:
    """excluded_fields entries as real field names: a FIELD_GROUPS key
    ("tank_ferrite") stands for its flag (if it has one) and all its
    fields. Raises ValueError on anything that's neither."""
    resolved = set()
    for name in names:
        if name in FIELD_GROUPS:
            resolved |= set(_group_fields(name))
        elif name in _FIELD_TYPES:
            resolved.add(name)
        else:
            raise ValueError(f"excluded_fields contains {name!r}, which is neither a CoilSet field nor a "
                             f"group ({sorted(FIELD_GROUPS)})")
    return resolved

# checks that pinned values are all numeric fields inside their limits and converts them to the right type, the same way a proposed change would be.
def coerce_fixed_values(values: dict, table: dict[str, FieldRule] | None = None) -> dict:
    """Validates and type-coerces pinned values the same way a proposed delta
    is: int/float, the code floors, and `table`'s limits for that field. A pin
    is the user's own stated value, so the field need not be enabled - but it
    cannot go outside a limit. Raises ValueError (a schema.Rejection) naming
    the field and its allowed values."""
    table = table or DEFAULT_SEARCH_FIELDS
    unknown = [k for k in values if k not in table]
    if unknown:
        raise ValueError(f"fixed_values contains {unknown}, which aren't numeric CoilSet fields")
    return coerce_delta(values, {k: _schema_for(k, table[k]) for k in values}, NUMERIC_FIELDS)


def free_fields_for(baseline: CoilSet, spec: VariableSpec | None = None,
                    table: dict[str, FieldRule] | None = None) -> dict[str, dict]:
    """Which CoilSet fields the LLM may propose changing, each with its schema
    entry (limits included): enabled in `table`, not excluded and not pinned.
    What the baseline physically has makes no difference - a design with
    ferrite fitted does not make ferrite free. `table` defaults to the shipped
    one; a run passes search_field_table(spec.settings), its own copy."""
    spec = spec or VariableSpec()
    table = table or DEFAULT_SEARCH_FIELDS
    fields = {}
    for name, rule in table.items():
        if not rule.enabled or name in spec.excluded_fields or name in spec.fixed_values:
            continue
        schema = _schema_for(name, rule)
        if schema.get("enum") == []:
            continue  # its limits leave no allowed copper thickness - nothing to propose
        fields[name] = schema
    return fields


def allowed_fields(baseline: CoilSet, spec: VariableSpec, table: dict[str, FieldRule] | None = None) -> set[str]:
    """Every field a candidate may legitimately differ from baseline in:
    the free ones the model proposes, plus the pinned ones the user set."""
    return set(free_fields_for(baseline, spec, table)) | set(spec.fixed_values)


def legacy_allowed_fields(spec: VariableSpec) -> set[str]:
    """allowed_fields() by the rule in force before the Field Config table:
    FREE_VARIABLES and rc_ferrite_y_offset always, a group's fields only if
    the interview listed it in enabled_groups. Only for auditing a run whose
    stored settings have no table."""
    names = set(FREE_VARIABLES) | {"rc_ferrite_y_offset"}
    for group in spec.enabled_groups:
        names |= set(_LEGACY_GROUP_FIELDS.get(group, ()))
    return {n for n in names if n not in spec.excluded_fields} | set(spec.fixed_values)


def stored_allowed_fields(baseline: CoilSet, spec_record: dict) -> set[str]:
    """allowed_fields() for a run read back from the database (its spec as a
    plain dict): by the run's own copy of the table when its settings snapshot
    has one, else by the rule in force when it was stored (legacy_allowed_fields())."""
    raw = spec_record.get("variable_spec") or {}
    spec = VariableSpec(enabled_groups=set(raw.get("enabled_groups") or []),
                        excluded_fields=set(raw.get("excluded_fields") or []),
                        fixed_values=dict(raw.get("fixed_values") or {}))
    settings = spec_record.get("settings") or {}
    if "search_fields" not in settings:
        return legacy_allowed_fields(spec)
    table = search_field_table(types.SimpleNamespace(search_fields=settings["search_fields"]))
    return allowed_fields(baseline, spec, table)


def limit_problems(baseline: CoilSet, spec: VariableSpec | None = None,
                   table: dict[str, FieldRule] | None = None) -> list[str]:
    """Why a search can't start from `baseline` under `table`: each free field
    whose baseline value is already outside its minimum / maximum, named with
    the allowed range. Only the table's own limits are checked, not the code
    floors - a baseline is a design a person saved (a tank-less one has 0 tank
    turns) and must still open. Empty list = nothing wrong."""
    table = table or DEFAULT_SEARCH_FIELDS
    problems = []
    for name in free_fields_for(baseline, spec, table):
        rule, value = table[name], getattr(baseline, name)
        if (rule.min is not None and value < rule.min) or (rule.max is not None and value > rule.max):
            problems.append(f"{name} is {value:g} on the baseline, outside its Field Config limit "
                            f"({limit_text(_schema_for(name, rule))})")
    return problems


# diffs a candidate against the baseline and flags any changed field that wasn't actually supposed to be allowed.
def audit_changed_fields(baseline: CoilSet, candidate: CoilSet, allowed) -> dict:
    """Every field that differs between baseline and candidate, and which
    of those fall outside `allowed` (see allowed_fields()). coerce_delta()
    already rejects an out-of-scope key before the candidate is built, so
    unexpected_fields should always be empty - this makes that guarantee
    checked and visible instead of assumed, and gives the reports a diff
    over ALL fields (a diff filtered by the free set could never show an
    out-of-scope change)."""
    baseline_dict = dataclasses.asdict(baseline)
    candidate_dict = dataclasses.asdict(candidate)
    changed = {k: (v, candidate_dict[k]) for k, v in baseline_dict.items() if v != candidate_dict[k]}
    unexpected = {k: v for k, v in changed.items() if k not in allowed}
    return {"changed_fields": changed, "unexpected_fields": unexpected}
