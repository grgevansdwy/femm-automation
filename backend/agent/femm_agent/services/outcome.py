"""The end-of-run report: how a search ended and why, in plain words.

Built by code from the finished AgentRunState - the rule that ended the
search (state.stop_reason), what it spent, and the best or closest design:

- a design met the goal: which rule ended the search, and the design's score,
- no design did: "Target not achieved", the limit or rule that ended it, and
  the closest design with what it still missed,
- failed, AI server: which rounds failed, how often, and the cause the
  requests reported (no response / too slow / token limit / unreachable ...),
- failed, internal error: a bug in this app, not the server.

At a budget limit with the search still improving, it invites "continue".
Nothing here ever says a target is impossible - an untried lever may reach it.
Shown in the chat and Search Summary, given to the follow-up chat, and saved
in the run's `error` column so History shows it too.
"""

from collections import Counter

from agent.femm_agent.services.scorer import blended_score, tool_result


def _cause_text(failure: dict, settings) -> str:
    """The heading for one failure's `kind` - LLMError.describe() has the
    full per-request sentence; this is the short clause after "Search
    failed:". Uses the failure's OWN recorded time_limit_s/max_tokens
    (LLMError.as_dict()), not the run's current settings - falls back to
    them only for an older persisted failure that predates those fields
    being recorded."""
    limit = failure.get("time_limit_s") or settings.llm_time_limit_s
    cap = failure.get("max_tokens") or settings.llm_max_tokens
    kind = failure["kind"]
    if kind == "no_response":
        return f"the AI server sent nothing back within {limit:.0f} s (busy with other requests, or still reading the prompt)"
    if kind == "too_slow":
        return f"the AI was still generating when the {limit:.0f} s limit hit (server too slow, or the AI thought for too long)"
    if kind == "length_limit":
        return f"the AI's reply hit the {cap:,.0f} token limit without finishing (runaway thinking)"
    if kind == "unreachable":
        return "the AI server couldn't be reached"
    if kind == "http_error":
        return "the AI server returned an error"
    if kind == "bad_reply":
        return "the AI's reply couldn't be read"
    return kind


def _closest_text(state) -> str:
    best = state.best
    if best is None:
        return "No valid candidate was found."
    result = tool_result(best, state.spec)
    round_num = next((r.round_num for r in state.rounds for c in r.candidates
                      if c.valid and c.tool_result == result), None)
    where = f" (round {round_num})" if round_num is not None else ""
    misses = [f"{o['metric']} {o['worst_change_pct']:+.1f}% (needs {o['requirement']})"
              for o in result["objectives"] if o["required"] and not o["passed"]]
    if not misses:
        return f"Best candidate{where} meets every requirement."
    return f"Closest candidate{where} still missed: " + "; ".join(misses) + "."


def _ended_text(state) -> str:
    """The rule that ended the search, as a clause."""
    reason, settings, budget = state.stop_reason, state.spec.settings, state.budget
    if reason == "rounds":
        return f"the search used all {budget.max_rounds} rounds"
    if reason == "time":
        return f"the search reached its {budget.time_limit_s / 60:.0f}-minute time limit"
    if reason == "simulations":
        return f"the search reached its limit of {budget.max_simulations} simulations"
    if reason == "nothing_left":
        return "the search stopped at the first design that met the goal (nothing in the goal can be pushed further)"
    if reason == "plateau":
        return f"the search stopped after {settings.plateau_rounds} rounds in a row without a better design"
    if reason == "stalled":
        return ("the search stopped early: no design got closer to the target for "
                f"{state.progress.rounds_since_improvement if state.progress else settings.stall_rounds} rounds, "
                "even after the AI was told so")
    if reason == "model_stopped":
        return "the AI stopped proposing"
    if reason == "user_stopped":
        return "you stopped the search"
    if reason == "llm_failed":
        return "the AI server kept failing"
    return reason or "unknown"


def _continue_invite(state) -> str:
    """At a budget limit while still improving: one more budget may get further."""
    if state.stop_reason in ("rounds", "time", "simulations") and state.progress is not None \
            and state.progress.verdict == "improving":
        return ' The search was still improving when it ran out: reply "continue" to keep searching with a fresh budget.'
    return ""


def _llm_failure_lines(state) -> list[str]:
    settings, failures = state.spec.settings, state.llm_failures
    kinds = Counter(f["kind"] for f in failures)
    main_kind, _ = kinds.most_common(1)[0] if kinds else ("unknown", 0)
    # The heading describes the actual limit that failure hit, whichever
    # failure of that kind happened first.
    main_failure = next((f for f in failures if f["kind"] == main_kind), None)
    cause = _cause_text(main_failure, settings) if main_failure is not None else main_kind
    failed_rounds = sorted({(f["search_attempt"], f["round"]) for f in failures})
    lines = [f"Search failed: {cause}."]
    if len(failed_rounds) > 1:
        lines.append(f"A round's AI request failed {settings.round_llm_attempts} tries in a row, the search resumed "
                     f"with its memory kept, and it happened again ({len(failed_rounds)} failed rounds).")
    else:
        lines.append(f"A round's AI request failed {settings.round_llm_attempts} tries in a row.")
    lines.append("Failed AI requests:")
    for f in failures:
        lines.append(f"  - attempt {f['search_attempt']}, round {f['round']}, try {f['ai_try']}: {f['message']}")
    if len(kinds) > 1:
        lines.append("Causes: " + ", ".join(f"{k} x{n}" for k, n in kinds.most_common()) + ".")
    return lines


def outcome_report(state, internal_error: str | None = None, status=None) -> str:
    """A short report for any ending. `status`: the status the run is about to
    get (AgentRunner passes it before setting it); default state.status."""
    status = (status or state.status).value
    candidates = sum(len(r.candidates) for r in state.rounds)
    closest = _closest_text(state)
    spent = f"{len(state.rounds)} rounds, {candidates} candidates"

    if internal_error is not None:
        return (f"Failed with an internal error (a bug in this app, not the AI server): {internal_error}. "
                f"{closest}")

    if status == "goal_met":
        score = blended_score(tool_result(state.best, state.spec), state.spec.favourite_metric)
        scored = "" if score is None else (
            f" Its score is {score:+.1f} points past the bar"
            + (f" on {state.spec.favourite_metric}" if state.spec.favourite_metric else "") + ".")
        ended = _ended_text(state)
        return f"Goal met: {ended[0].upper() + ended[1:]} ({spent}). {closest}{scored}{_continue_invite(state)}"

    if state.stop_reason == "llm_failed":
        return "\n".join(_llm_failure_lines(state) + [f"Before failing: {spent}. {closest}"])

    report = f"Target not achieved: {_ended_text(state)} ({spent}). {closest}"
    verdict = state.progress
    if state.stop_reason == "stalled" and verdict is not None:
        if verdict.repeats or verdict.near_copies:
            report += (f" {verdict.repeats} proposal(s) repeated an earlier design and {verdict.near_copies} "
                       "were near-copies of one.")
        if verdict.untried_fields:
            report += f" Fields the search never changed: {', '.join(verdict.untried_fields)}."
    return report + _continue_invite(state)
