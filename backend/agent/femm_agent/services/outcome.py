"""The end-of-run report: why a search didn't meet its goal, in plain words.

Built from the finished AgentRunState, for every ending except a met goal:

- failed, AI server: which rounds failed, how often, and the cause the
  requests reported (no response / too slow / token limit / unreachable ...),
- failed, internal error: a bug in this app, not the server,
- budget exhausted: every round used without meeting the goal,
- stopped by model: the AI stopped proposing on its own,

each followed by the closest candidate found and what it still missed.
Shown in the chat and Search Summary, given to the follow-up chat, and saved
in the run's `error` column so History shows it too.
"""

from collections import Counter

from agent.femm_agent.services.scorer import tool_result


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


def outcome_report(state, internal_error: str | None = None) -> str | None:
    """None for a met goal (nothing to explain), otherwise a short report."""
    status = state.status.value
    settings = state.spec.settings
    if status == "goal_met":
        return None
    candidates = sum(len(r.candidates) for r in state.rounds)
    closest = _closest_text(state)

    if internal_error is not None:
        return (f"Failed with an internal error (a bug in this app, not the AI server): {internal_error}. "
                f"{closest}")

    if status == "failed":
        failures = state.llm_failures
        kinds = Counter(f["kind"] for f in failures)
        main_kind, _ = kinds.most_common(1)[0] if kinds else ("unknown", 0)
        # The heading describes the actual limit that failure hit, whichever
        # failure of that kind happened first.
        main_failure = next((f for f in failures if f["kind"] == main_kind), None)
        cause = _cause_text(main_failure, settings) if main_failure is not None else main_kind
        failed_rounds = sorted({(f["search_attempt"], f["round"]) for f in failures})
        lines = [f"Search failed: {cause}."]
        if settings.search_attempts > 1 and state.search_attempt > 1:
            lines.append(f"Each time, a round's AI request failed {settings.round_llm_attempts} tries in a row, so the search "
                         f"restarted from zero and failed again ({len(failed_rounds)} failed rounds across "
                         f"{state.search_attempt} attempts).")
        else:
            lines.append(f"A round's AI request failed {settings.round_llm_attempts} tries in a row.")
        lines.append("Failed AI requests:")
        for f in failures:
            lines.append(f"  - attempt {f['search_attempt']}, round {f['round']}, try {f['ai_try']}: {f['message']}")
        if len(kinds) > 1:
            lines.append("Causes: " + ", ".join(f"{k} x{n}" for k, n in kinds.most_common()) + ".")
        lines.append(f"Before failing: {len(state.rounds)} rounds, {candidates} candidates. {closest}")
        return "\n".join(lines)

    restarted = (f" (after a restart from zero: attempt 1 failed on AI requests)" if state.search_attempt > 1 else "")
    if status == "budget_exhausted":
        return (f"Goal not met: used every round ({state.spec.max_tool_calls} per attempt){restarted} - "
                f"{len(state.rounds)} rounds, {candidates} candidates - without a candidate meeting every "
                f"requirement. {closest}")
    if status == "stopped_by_model":
        return (f"Goal not met: the AI stopped proposing after {len(state.rounds)} rounds ({candidates} candidates)"
                f"{restarted}. {closest}")
    return None
