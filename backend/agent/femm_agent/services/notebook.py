"""The lever notebook: what each small step of the search did to each
objective, worked out by code from the stored results and resent to the model
every round (optimize.search_memory()), so it stops re-deriving cause and
effect from raw numbers - and so a person can read what the search learned.

A line is written for a design that differs from its starting design (its
`base`, or the baseline) in one value ("single") or in two or three
("combined"). A design with no single line of its own also gets one against
the earliest tried design it differs from in exactly one value. Bigger steps
get no line; every design stays in the tried list regardless.

Pure functions of the tried list: no LLM, no solver. Entries are read by
attribute (label, base_label, scored.coilset, result), so anything shaped like
optimize._Tried works.
"""

import dataclasses

NOTEBOOK_MAX_LINES = 40  # newest lines resent each round - keeps the prompt flat however long the search runs


@dataclasses.dataclass
class _Step:
    kind: str        # "single" | "combined"
    start: str       # the starting design's label, or "baseline"
    labels: list     # the design(s) this step produced - several when identical steps were merged
    changes: dict    # field -> (value in the starting design, value in this design)
    effects: str

    def line(self) -> str:
        changes = ", ".join(f"{k} {a:g} -> {b:g}" for k, (a, b) in self.changes.items())
        return f"  {self.kind:<8} {self.start} -> {'='.join(self.labels)}: {changes} | {self.effects}"


def _diff(start: dict, design: dict) -> dict:
    return {k: (v, design[k]) for k, v in start.items() if design[k] != v}


def _effects(result: dict, start_result: dict | None) -> str:
    """Each objective's move from the starting design to this one: the change in
    its worst-distance result, in percentage points (both are % vs. the original
    baseline, so the baseline itself is 0). An absolute target shows the value
    before and after instead. `start_result` None = the start is the baseline."""
    parts = []
    start_objectives = start_result["objectives"] if start_result else [None] * len(result["objectives"])
    for o, s in zip(result["objectives"], start_objectives):
        if o.get("target_value") is not None:
            parts.append(f"{o['metric']} {(s['value'] if s else o['baseline_value']):g} -> {o['value']:g}")
        else:
            parts.append(f"{o['metric']} {o['worst_change_pct'] - (s['worst_change_pct'] if s else 0.0):+.1f} pts")
    return ", ".join(parts)


def _steps(tried, baseline, fixed_values) -> list[_Step]:
    designs = [t for t in tried if t.scored is not None and t.label]
    coils = {t.label: dataclasses.asdict(t.scored.coilset) for t in designs}
    by_label = {t.label: t for t in designs}
    merged: dict[tuple, _Step] = {}

    def add(kind, start, label, changes, effects):
        key = (kind, start, tuple(changes.items()))
        if key in merged:  # an identical step proposed again - one line, both labels
            merged[key].labels.append(label)
        else:
            merged[key] = _Step(kind, start, [label], changes, effects)

    for i, t in enumerate(designs):
        coil = coils[t.label]
        # The starting design, when it has a result to compare with. With pinned
        # values a base-less design starts from baseline + pins, which is never
        # simulated itself - so there is nothing to measure that step against.
        start_label = single = None
        if t.base_label in by_label:
            start_label, start_coil, start_result = t.base_label, coils[t.base_label], by_label[t.base_label].result
        elif t.base_label is None and not fixed_values:
            start_label, start_coil, start_result = "baseline", dataclasses.asdict(baseline), None
        if start_label is not None:
            changes = _diff(start_coil, coil)
            if len(changes) == 1:
                single = True
                add("single", start_label, t.label, changes, _effects(t.result, start_result))
            elif len(changes) in (2, 3):
                add("combined", start_label, t.label, changes, _effects(t.result, start_result))
        if not single:
            for other in designs[:i]:
                changes = _diff(coils[other.label], coil)
                if len(changes) == 1:
                    add("single", other.label, t.label, changes, _effects(t.result, other.result))
                    break
    return list(merged.values())


def notebook_block(tried, baseline, fixed_values) -> list[str]:
    """The notebook as memory lines, header first - [] while there is nothing to say."""
    lines = [s.line() for s in _steps(tried, baseline, fixed_values)]
    if not lines:
        return []
    cut = f" Newest {NOTEBOOK_MAX_LINES} of {len(lines)} lines." if len(lines) > NOTEBOOK_MAX_LINES else ""
    header = ("Lever notebook - what each small step did, measured (single = one value changed, combined = 2-3; "
              f"pts = change in that objective's worst-distance % vs. baseline):{cut}")
    return [header] + lines[-NOTEBOOK_MAX_LINES:]


def fields_without_a_line(tried, baseline, fixed_values, free_fields) -> list[str]:
    """The free fields no notebook step has changed yet, in free_fields order."""
    seen = {k for s in _steps(tried, baseline, fixed_values) for k in s.changes}
    return [f for f in free_fields if f not in seen]
