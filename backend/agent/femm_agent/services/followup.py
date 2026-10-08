"""The follow-up chat: once the user has started a search, the same chat keeps
going - "how's it going?" while it runs, "why did tank_esr drop?" after it
finishes. (Searching further with the same goal is "continue", which code
handles before a message ever gets here - AgentRunner.continue_run().)

While a search is running the chat cannot change anything, and that lock is
structural, not a prompt instruction: the model is called with no tools at
all, so it has no finalize_spec to re-plan with and no propose_coilset to try
a design with. The prompt only tells it what to say instead (stop the search
first).

Once a search has ended, a caller may let the user change the goal here
(`allow_replan`): the model is then offered finalize_spec and
propose_search_fields_change, and nothing else. Each call is a proposal - code
checks a plan exactly as it checks an interview's
(interview.accept_finalize_call), against the baseline the run was started
with, and a Search fields change is only ever shown to the user. An accepted plan is left on the conversation as its `spec` and nothing
is searched: the user confirms it with Start Search
(AgentRunner.start_revised()). The model still never proposes a design.

Each turn rebuilds the run's context from the live AgentRunState (plan,
baseline numbers, every round so far, the best candidate), so answers track
the search as it progresses. That snapshot is built fresh rather than
reusing optimize.py's own message history, which is both much larger and
trimmed to its latest round plus the best few candidates.
"""

import dataclasses
import json

from agent.services.llm_client import call_llm
from agent.femm_agent.services import replan
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


def _plan_lines(spec) -> list[str]:
    lines = [f"Objective (user's words): {spec.objective_text or '(none given)'}",
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
    return lines


def _earlier_search_lines(run_state, rounds) -> list[str]:
    """One line per segment that lies outside the search's memory (a later
    plan added a distance, so the search started over) - enough to say it
    happened, without listing designs that can't be compared with today's."""
    lines = []
    for segment in sorted({r.segment for r in rounds}):
        in_segment = [r for r in rounds if r.segment == segment]
        entry = next((e for e in run_state.budget_history if e.get("segment") == segment), {})
        simulated = sum(1 for r in in_segment for c in list(r.candidates) if c.valid)
        lines.append(f"  Rounds {in_segment[0].round_num}-{in_segment[-1].round_num}: an earlier plan"
                     f"{f' at distances {entry['distances']} mm' if entry.get('distances') else ''}, "
                     f"{simulated} design(s) simulated. Not comparable with the current plan and not in the "
                     "search's memory.")
    return lines


def run_context(run_state, pending_spec=None, show_plan_arguments: bool = False) -> str:
    """Plain-text snapshot of one agent run for the follow-up model. Safe to
    call while the run's thread is still mutating it - it only reads, over
    copies of the lists. `pending_spec`: a revised plan the user has been
    shown but not started. `show_plan_arguments`: also give the plan in force
    as finalize_spec arguments (when the model may revise it)."""
    spec = run_state.spec
    rounds = list(run_state.rounds)
    plans = replan.segment_plans(run_state)
    lines = [
        f"Search status: {_STATUS_TEXT.get(run_state.status, run_state.status.value)}.",
        f"Rounds so far: {len(rounds)}.",
    ]
    budget = run_state.budget
    if budget is not None:
        lines.append(
            f"Budget for this search{f' (segment {run_state.segment} of this chat)' if run_state.segment > 1 else ''}: "
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

    lines += ["", "PLAN" + (f" (revised {run_state.plan - 1} time(s) in this chat)" if run_state.plan > 1 else "")]
    lines += _plan_lines(spec)
    if show_plan_arguments:
        from agent.input_chat.services.interview import spec_arguments
        lines.append("The same plan as finalize_spec arguments: " + json.dumps(spec_arguments(spec)))
    if pending_spec is not None:
        lines += ["", "REVISED PLAN (you proposed it; the user has not started it yet - nothing below was run under it)"]
        lines += _plan_lines(pending_spec)
        changes = replan.plan_diff(spec, pending_spec)
        lines += ["Changes from the plan above:"] + [f"  - {c}" for c in changes or ["none"]]
        lines.append(replan.memory_line(spec, pending_spec, run_state.tried))

    failed_checks = [c for c in run_state.fragility if not c["passed"]]
    if failed_checks:
        lines.append("Designs that met the goal but failed the fragility check (never winners):")
        lines += [f"  - round {c['round']}: {c['delta']} -> {c['summary']}" for c in failed_checks]

    lines += ["", f"BASELINE ({spec.baseline_config_id})"]
    if run_state.baseline_metrics:
        lines += _metrics_lines(run_state.baseline_metrics, "  ")
    else:
        lines.append("  (baseline solve not finished yet)")

    outside = [r for r in rounds if r.round_num <= run_state.memory_from_round]
    if outside:
        lines += ["", "EARLIER SEARCH"] + _earlier_search_lines(run_state, outside)
    lines += ["", "ROUNDS (each candidate = baseline + the listed changes)"]
    if len(rounds) == len(outside):
        lines.append("  (no rounds yet)")
    for r in rounds[len(outside):]:
        candidates = list(r.candidates)
        earlier_plan = "" if plans.get(r.segment, 1) == run_state.plan else " [result as scored under the earlier plan]"
        if not candidates:
            state_note = "no proposal yet" if r.status.value != "done" else "no proposal (text-only reply)"
            lines.append(f"  Round {r.round_num}: {state_note}")
            continue
        for i, c in enumerate(candidates, start=1):
            label = f"  Round {r.round_num}" + (f" candidate {i}" if len(candidates) > 1 else "")
            if c.valid and c.tool_result:
                lines.append(f"{label}: changes {c.delta} -> goal_met={c.tool_result.get('goal_met')}; "
                             f"{objective_summary(c.tool_result)}{earlier_plan}")
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


def followup_system_prompt(run_state, pending_spec=None, can_replan: bool = False, plan_settings=None) -> str:
    """`can_replan`: the model is being offered finalize_spec this turn (a
    search has ended and the caller allows a revised plan) - the prompt then
    explains how to revise the plan instead of pointing at Restart.
    `plan_settings`: the settings a revised plan would be built with - their
    Search fields table is the one the prompt lists (default: the run's own)."""
    from agent.input_chat.services.interview import quantity_lines

    settings = run_state.spec.settings
    running = run_state.status == AgentRunStatus.RUNNING
    return render(settings.prompt("followup_system"), {
        "running": "yes" if running else "",
        "can_replan": "yes" if can_replan else "",
        "locked": "" if running or can_replan else "yes",
        "run_data": run_context(run_state, pending_spec, show_plan_arguments=can_replan),
        **quantity_lines(run_state.baseline_coilset, plan_settings or settings),
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


def run_followup_turn(conversation, user_message: str, run_state, should_abort=None, log=None,
                      allow_replan: bool = False, settings=None) -> None:
    """One follow-up exchange, appended in place to `conversation.transcript`
    (the planning chat's agent.input_chat.services.interview.InterviewState - same chat, same
    transcript). `log` (a RunLog), if given, records the request.

    While the search runs, or without `allow_replan`, the model is called
    with no tools and conversation.spec is never touched. With `allow_replan`
    and a search that has ended, it is offered two tools:
    - finalize_spec: an accepted call replaces conversation.spec with the
      revised plan (run_state.spec stays the plan in force until the user
      starts the new one); a rejected one is turned into a question by a
      second, tool-less call.
    - propose_search_fields_change: the proposal is left on
      conversation.pending_field_change for the user to Apply or Dismiss.
      Nothing is written here.
    `settings`: the settings a revised plan is built with - the saved ones
    as they are now by default (so a Search fields change the user applied
    counts), like a first plan."""
    from agent.input_chat.services.interview import (
        PROPOSE_FIELDS_TOOL_NAME, PROPOSE_SEARCH_FIELDS_TOOL, accept_field_change_call, accept_finalize_call,
        finalize_spec_tool,
    )

    conversation.error = None
    conversation.live_reasoning = ""
    conversation.transcript.append({"role": "user", "content": user_message})
    can_replan = allow_replan and run_state.status != AgentRunStatus.RUNNING
    if can_replan and settings is None:
        from agent.settings.settings import load_settings
        settings = load_settings()
    pending = conversation.spec if conversation.spec is not None and conversation.spec != run_state.spec else None
    send_messages = [{"role": "system", "content": followup_system_prompt(run_state, pending, can_replan, settings)}]
    send_messages += _conversation_messages(conversation.transcript)

    def ask(messages, tools):
        return call_llm(
            messages, tools, settings=run_state.spec.settings, should_abort=should_abort, log=log,
            on_update=lambda content, reasoning: setattr(conversation, "live_reasoning", reasoning or content),
        )

    try:
        msg = ask(send_messages, finalize_spec_tool(settings.max_distances) + PROPOSE_SEARCH_FIELDS_TOOL if can_replan else None)
    except Exception as exc:
        conversation.error = str(exc)
        return
    tool_calls = msg.get("tool_calls") if can_replan else None
    if not tool_calls:
        conversation.transcript.append({"role": "assistant", "content": msg.get("content") or "(no reply)"})
        return

    conversation.transcript.append(msg)
    call = tool_calls[0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        function = {}
    if function.get("name") == PROPOSE_FIELDS_TOOL_NAME:
        # Shown to the user as a card with Apply / Dismiss. The reply after it is
        # tool-less, as in the interview: the model says what it proposed and why.
        spec, (proposal, result) = None, accept_field_change_call(function)
        if proposal is not None:
            conversation.pending_field_change = proposal
    elif function.get("name") != "finalize_spec":
        spec, result = None, {"valid": False, "error": f"unknown tool {function.get('name')!r}"}
    elif run_state.status == AgentRunStatus.RUNNING:
        # The search was started again while this turn was in flight.
        spec, result = None, {"valid": False, "error": "a search is running - its plan cannot be changed until it is stopped"}
    else:
        # Against the baseline the run was started with, not the config as it is today.
        spec, result = accept_finalize_call(function, conversation.baseline_config_id, run_state.baseline_coilset,
                                            settings, log, where="run_followup_turn")
    conversation.transcript.append({"role": "tool", "content": json.dumps(result)})
    if spec is not None:
        conversation.spec, conversation.finalized = spec, True
        return

    # A rejection is a raw validation string, and a Search fields proposal needs a
    # sentence to go with its card: one more call, with no tools, puts either into
    # words for the user - the same way the interview does it, so the model cannot
    # quietly try a second plan nobody saw.
    try:
        reply = ask(send_messages + [{k: v for k, v in msg.items() if k != "reasoning_content"},
                                     {"role": "tool", "content": json.dumps(result)}], None)
    except Exception as exc:
        conversation.error = str(exc)
        return
    conversation.transcript.append({"role": "assistant", "content": reply.get("content") or "(no reply)"})
