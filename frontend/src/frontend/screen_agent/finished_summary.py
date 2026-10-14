"""The chat message shown when a search ends: what was found, in plain words.

Written by code from the search's own numbers - no AI call - so it always matches
the result. It says nothing about scores or points (the GUI's result panel has
those); it says which quantities moved how far, what was changed in the coil, and
invites questions.
"""

import dataclasses
import math

from agent.input_chat.models.objective import QUANTITY_GLOSSES

_MAX_CHANGES = 6

_WHY_IT_STOPPED = {
    "plateau": "The search stopped because it had stopped finding better designs.",
    "nothing_left": "The search stopped because there was nothing left to improve.",
    "stalled": "The search stopped because it was no longer getting closer to the goal.",
    "rounds": "The search stopped because it used all its rounds.",
    "time": "The search stopped because it reached its time limit.",
    "simulations": "The search stopped because it reached its simulation limit.",
    "model_stopped": "The search stopped because the AI stopped proposing new designs.",
    "user_stopped": "The search was stopped by you.",
    "llm_failed": "The search stopped because requests to the AI kept failing.",
    "internal_error": "The search stopped because of an internal error.",
}


def _pct(x: float) -> str:
    return f"{abs(x):.1f}".rstrip("0").rstrip(".") + "%"


def _within(x: float) -> str:
    """A bound for "stays within": rounded up to 0.1, so a movement too small to show is never "0%"."""
    return f"{max(math.ceil(abs(x) * 10 - 1e-9) / 10, 0.1):g}%"


def _result_clauses(result: dict) -> tuple[list[str], list[str]]:
    """(what the best design achieves, what it misses) over the required objectives,
    each at the worst distance. Quantities held at baseline are grouped into one clause."""
    achieved, missed, held = [], [], []
    for o in result.get("objectives", []):
        if not o["required"]:
            continue
        change = o["worst_change_pct"]
        if o["kind"] == "match" and o.get("target_value") is None and not o.get("target_pct"):
            if o["passed"]:
                held.append(o)
            else:
                missed.append(f"{o['metric']} moves {_pct(change)} from baseline (allowed {o['requirement']})")
        elif o.get("target_value") is not None:
            (achieved if o["passed"] else missed).append(f"{o['metric']} {o['requirement']}")
        else:
            text = f"{o['metric']} is {_pct(change)} {'lower' if change < 0 else 'higher'}"
            achieved.append(text) if o["passed"] else missed.append(f"{text} (needed {o['requirement']})")
    if held:
        names = ", ".join(o["metric"] for o in held)
        achieved.append(f"{names} stay within {_within(max(abs(o['worst_change_pct']) for o in held))} of baseline")
    return achieved, missed


def _changes(baseline, best) -> list[str]:
    if baseline is None or best is None:
        return []
    old, new = dataclasses.asdict(baseline), dataclasses.asdict(best)
    lines = []
    for name, value in new.items():
        if name == "config_name" or value == old.get(name):
            continue
        label = QUANTITY_GLOSSES.get(name, name)
        lines.append(f"{label} {old.get(name):g} to {value:g}" if isinstance(value, (int, float))
                     and isinstance(old.get(name), (int, float)) else f"{label} changed")
    return lines


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def summary_text(status: str, stop_reason: str | None, result: dict | None, baseline, best,
                 error: str | None = None, can_continue: bool = True) -> str:
    """`result` is the best design's tool_result() dict (None if no valid design was found);
    `baseline` and `best` are CoilSets. `can_continue` False for a chat that is read-only."""
    paragraphs = []
    if status in ("failed", "interrupted") and result is None:
        paragraphs.append("Search finished, but it ended before finding a design." + (f" {error}" if error else ""))
    elif result is None:
        paragraphs.append("Search finished. No valid coil design was found.")
    else:
        achieved, missed = _result_clauses(result)
        if result.get("goal_met"):
            paragraphs.append("Search finished. Best coil design found!")
            paragraphs.append("It meets your goal: " + _join(achieved) + ".")
        else:
            paragraphs.append("Search finished. No design met every requirement. The closest one found "
                              + ("achieves: " + _join(achieved) + ". " if achieved else "") +
                              "It misses: " + _join(missed) + ".")
        changes = _changes(baseline, best)
        if changes:
            shown = changes[:_MAX_CHANGES]
            more = f", and {len(changes) - len(shown)} more" if len(changes) > len(shown) else ""
            paragraphs.append("What changed in the coil: " + "; ".join(shown) + more + ".")
    why = _WHY_IT_STOPPED.get(stop_reason)
    if why:
        paragraphs.append(why)
    ask = "Let me know if you have any questions about the run."
    if can_continue:
        ask += ' You can reply "continue" to search further toward the same goal, or tell me what to change in the goal.'
    paragraphs.append(ask)
    return "\n\n".join(paragraphs)
