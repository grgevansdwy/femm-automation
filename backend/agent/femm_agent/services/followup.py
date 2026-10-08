"""The follow-up chat: once the user has started a search, the same chat keeps
going - "how's it going?" while it runs, "why did tank_esr drop?" after it
finishes - but it can no longer change anything. (Searching further with the
same goal is "continue", which code handles before a message ever gets here -
AgentRunner.continue_run().)

That lock is structural, not a prompt instruction: the model is called with
no tools at all, so it has no finalize_spec to re-plan with and no
propose_coilset to try a design with. The prompt only tells it what to say
instead (inputs are locked - Restart and start a new task).

Each turn rebuilds the run's context from the live AgentRunState (plan,
baseline numbers, every round so far, the best candidate), so answers track
the search as it progresses. That snapshot is built fresh rather than
reusing optimize.py's own message history, which is both much larger and
trimmed to its latest round plus the best few candidates.
"""

import dataclasses

from agent.services.llm_client import call_llm
from agent.femm_agent.services.scorer import objective_summary, tool_result
from agent.femm_agent.models.state import AgentRunStatus
from agent.settings.templating import render

_STATUS_TEXT = {
    AgentRunStatus.RUNNING: "still running",
    AgentRunStatus.GOAL_MET: "finished - goal met",
    AgentRunStatus.BUDGET_EXHAUSTED: "finished - reached a budget limit without meeting the goal",
    AgentRunStatus.STOPPED_BY_MODEL: "finished - the optimizer stopped proposing without meeting the goal",
    AgentRunStatus.STALLED: "finished - stopped early because it was no longer getting closer to the goal",
    AgentRunStatus.FAILED: "finished - failed with an error",
    AgentRunStatus.INTERRUPTED: "interrupted - the app was closed while it was searching",
    AgentRunStatus.CANCELLED: "stopped by the user before any design met the goal",
}


def _fmt(value) -> str:
    return f"{value:.6g}" if isinstance(value, (int, float)) else str(value)


def _metrics_lines(metrics: dict | None, indent: str) -> list[str]:
    from agent.input_chat.models.objective import SIM_RESULT_METRICS

    lines = []
    for distance, result in sorted((metrics or {}).items()):
        values = ", ".join(f"{m}={_fmt(result.get(m))}" for m in SIM_RESULT_METRICS if result.get(m) is not None)
        lines.append(f"{indent}at {distance:g} mm: {values}")
    return lines


def run_context(run_state) -> str:
    """Plain-text snapshot of one agent run for the follow-up model. Safe to
    call while the run's thread is still mutating it - it only reads, over
    copies of the lists."""
    spec = run_state.spec
    rounds = list(run_state.rounds)
    lines = [
        f"Search status: {_STATUS_TEXT.get(run_state.status, run_state.status.value)}.",
        f"Rounds so far: {len(rounds)}.",
    ]
    budget = run_state.budget
    if budget is not None:
        lines.append(
            f"Budget for this search{f' (segment {run_state.segment}, after a continue)' if run_state.segment > 1 else ''}: "
            f"{budget.rounds_used} of {budget.max_rounds} rounds, {budget.elapsed_s() / 60:.0f} of "
            f"{budget.time_limit_s / 60:.0f} minutes, {budget.simulations_used} of {budget.max_simulations} simulations used.")
    verdict = run_state.progress
    if verdict is not None:
        lines.append(f"Progress, judged by code: {verdict.verdict} ({verdict.rounds_since_improvement} round(s) since "
                     f"the last real improvement; {verdict.repeats} repeated and {verdict.near_copies} near-copy proposals).")
        if verdict.untried_fields:
            lines.append(f"Fields the search may change but hasn't yet: {', '.join(verdict.untried_fields)}")
    if run_state.outcome_report:
        lines.append(f"How the search ended (report written by code):\n{run_state.outcome_report}")
    if run_state.status == AgentRunStatus.RUNNING and rounds:
        current = rounds[-1]
        lines.append(f"Round {current.round_num} is currently: {current.status.value} "
                     "(proposing = waiting on the optimizer model, evaluating = FEMM solving, done = finished).")

    lines += ["", "PLAN", f"Objective (user's words): {spec.objective_text or '(none given)'}",
              f"Distances: {spec.distances} mm", "Objectives:"]
    for o in spec.objectives:
        lines.append(f"  - {o.metric}: {'Required' if o.required else 'Preferred'}, {o.describe()}")
    if spec.favourite_metric:
        lines.append(f"Designs that meet the goal are compared on {spec.favourite_metric} alone (the user's favourite).")
    vs = spec.variable_spec
    if vs.enabled_groups:
        lines.append(f"Newly enabled groups: {sorted(vs.enabled_groups)}")
    if vs.fixed_values:
        lines.append("Pinned values: " + ", ".join(f"{k}={_fmt(v)}" for k, v in vs.fixed_values.items()))
    if vs.excluded_fields:
        lines.append(f"Kept fixed: {sorted(vs.excluded_fields)}")

    failed_checks = [c for c in run_state.fragility if not c["passed"]]
    if failed_checks:
        lines.append("Designs that met the goal but failed the fragility check (never winners):")
        lines += [f"  - round {c['round']}: {c['delta']} -> {c['summary']}" for c in failed_checks]

    lines += ["", f"BASELINE ({spec.baseline_config_id})"]
    if run_state.baseline_metrics:
        lines += _metrics_lines(run_state.baseline_metrics, "  ")
    else:
        lines.append("  (baseline solve not finished yet)")

    lines += ["", "ROUNDS (each candidate = baseline + the listed changes)"]
    if not rounds:
        lines.append("  (no rounds yet)")
    for r in rounds:
        candidates = list(r.candidates)
        if not candidates:
            state_note = "no proposal yet" if r.status.value != "done" else "no proposal (text-only reply)"
            lines.append(f"  Round {r.round_num}: {state_note}")
            continue
        for i, c in enumerate(candidates, start=1):
            label = f"  Round {r.round_num}" + (f" candidate {i}" if len(candidates) > 1 else "")
            if c.label:  # the name the candidate cards show - what the user will ask about
                label += f" [{c.label}" + (f", built on {c.base_label}]" if c.base_label else "]")
            if c.valid and c.tool_result:
                lines.append(f"{label}: changes {c.delta} -> goal_met={c.tool_result.get('goal_met')}; "
                             f"{objective_summary(c.tool_result)}")
            else:
                lines.append(f"{label}: changes {c.delta} -> rejected: {c.error}")
            if c.rationale:
                lines.append(f"    optimizer's rationale: {c.rationale}")

    best = run_state.best
    lines += ["", "BEST CANDIDATE SO FAR" if run_state.status == AgentRunStatus.RUNNING else "PROPOSED DESIGN (best candidate)"]
    if best is None:
        lines.append("  (no valid candidate yet)")
    else:
        best_dict = dataclasses.asdict(best.coilset)
        found_in = next((r.round_num for r in rounds for c in list(r.candidates) if c.candidate_coilset == best_dict), None)
        if found_in is not None:
            lines.append(f"  Found in round {found_in}.")
        lines.append(f"  Changed from baseline: {best.changed_fields or '(unknown)'}")
        lines.append(f"  Goal met: {best.goal_met}")
        if best.goal_met:
            lines.append("  Fragility check (the goal must still be met with every dimension changed together by "
                         "-2%, -1%, +1% and +2%): "
                         + ("passed - this design is the winner." if run_state.winner is best
                            else "not done yet - it is not a winner until it passes."))
        for o in tool_result(best, spec)["objectives"]:
            worst = (f", worst distance {max(o['change_pct_by_distance'], key=lambda d: abs(o['change_pct_by_distance'][d]))} mm"
                     if o["change_pct_by_distance"] else "")
            lines.append(f"  - {o['metric']}: {_fmt(o['value'])} vs baseline {_fmt(o['baseline_value'])} "
                         f"({o['worst_change_pct']:+.2f}%{worst}), requirement {o['requirement']} -> "
                         f"{'PASS' if o['passed'] else 'FAIL'}{'' if o['required'] else ' (preference)'}")
        lines += _metrics_lines(best.metrics, "  ")
    return "\n".join(lines)


def followup_system_prompt(run_state) -> str:
    settings = run_state.spec.settings
    return render(settings.prompt("followup_system"), {
        "running": "yes" if run_state.status == AgentRunStatus.RUNNING else "",
        "run_data": run_context(run_state),
    })


def _conversation_messages(transcript: list[dict]) -> list[dict]:
    """The chat so far as plain user/assistant text. Tool calls and tool
    results from the planning phase are dropped (a request with no tools
    can't carry them), and reasoning_content is never resent."""
    messages = []
    for msg in transcript:
        role = msg.get("role")
        if role == "user":
            messages.append({"role": "user", "content": msg.get("content") or ""})
        elif role == "assistant":
            content = msg.get("content") or ("[I proposed the search plan described below.]" if msg.get("tool_calls") else "")
            if content:
                messages.append({"role": "assistant", "content": content})
    return messages


def run_followup_turn(conversation, user_message: str, run_state, should_abort=None, log=None) -> None:
    """One follow-up exchange, appended in place to `conversation.transcript`
    (the planning chat's agent.input_chat.services.interview.InterviewState - same chat, same
    transcript). Never touches conversation.spec/finalized, and calls the
    model with no tools. `log` (a RunLog), if given, records the request."""
    conversation.error = None
    conversation.live_reasoning = ""
    conversation.transcript.append({"role": "user", "content": user_message})
    send_messages = [{"role": "system", "content": followup_system_prompt(run_state)}]
    send_messages += _conversation_messages(conversation.transcript)
    try:
        msg = call_llm(
            send_messages, None, settings=run_state.spec.settings, should_abort=should_abort, log=log,
            on_update=lambda content, reasoning: setattr(conversation, "live_reasoning", reasoning or content),
        )
    except Exception as exc:
        conversation.error = str(exc)
        return
    conversation.transcript.append({"role": "assistant", "content": msg.get("content") or "(no reply)"})
