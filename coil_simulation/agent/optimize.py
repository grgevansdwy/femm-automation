"""The evaluator-optimizer loop body: propose -> evaluate -> feed back ->
repeat, up to spec.max_tool_calls rounds. Same search logic
orchestrator.py's old main() had, restructured to mutate a shared
AgentRunState in place each round (instead of printing) so a caller - a GUI
panel, or orchestrator.py's thin CLI wrapper - can poll live progress, and
to read spec.* instead of agent/config.py's module constants, and to solve
candidates through job_runner (cached, see scorer.evaluate_async) instead
of calling simulate() directly.
"""

import dataclasses
import json

from agent.llm_client import call_llm
from agent.prompts import system_prompt
from agent.schema import RATIONALE_KEY, build_tools, coerce_delta
from agent.scorer import Scored, evaluate, evaluate_async, objective_summary, rank_key, tool_result, wait_for_jobs
from agent.state import AgentRunStatus, CandidateResult, RoundState, RoundStatus, now
from agent.variables import allowed_fields, audit_changed_fields, free_fields_for


def optimize_loop(state, job_runner) -> None:
    spec = state.spec
    baseline = state.baseline_coilset
    fixed_values = spec.variable_spec.fixed_values  # applied to every candidate, see VariableSpec

    baseline_metrics = wait_for_jobs(evaluate_async(baseline, spec.distances, job_runner))

    free_fields = free_fields_for(baseline, spec.variable_spec)
    allowed = allowed_fields(baseline, spec.variable_spec)
    tools = build_tools(free_fields, spec.baseline_config_id)

    user_message = {"role": "user", "content": "Find a candidate that meets the objective. Start proposing."}
    system_message = {"role": "system", "content": system_prompt(baseline, free_fields, spec, baseline_metrics)}

    history_log: list[str] = []            # one-liner summary per round, read once a round passes keep_recent_rounds
    round_messages: list[list[dict]] = []  # each round's raw messages, resent verbatim while still "recent"

    best: Scored | None = None
    no_tool_call_streak = 0  # consecutive text-only replies

    for call_num in range(1, spec.max_tool_calls + 1):
        older_count = max(0, len(history_log) - spec.keep_recent_rounds)
        history_block = ""
        if older_count:
            history_block = "\n\nEarlier attempts (summarized, oldest first):\n" + "\n".join(history_log[:older_count])

        # history must be role "user" not "system" - the office server's chat
        # template hard-rejects a system message anywhere but the very first one.
        recent = [m for round_msgs in round_messages[older_count:] for m in round_msgs]
        history = {"role": "user", "content": history_block}
        send_messages = [system_message, user_message, history] + recent

        round_state = RoundState(round_num=call_num)
        state.rounds.append(round_state)

        try:
            msg = call_llm(send_messages, tools)
        except Exception as exc:
            round_state.status = RoundStatus.DONE
            round_state.done_at = now()
            round_state.reasoning_text = f"LLM request failed, stopping here: {exc}"
            state.error = str(exc)
            state.status = AgentRunStatus.FAILED
            return

        this_round = [msg]  # this round's own raw messages, appended to round_messages once complete
        # Prefer the model's actual chain-of-thought (a "reasoning_content"
        # field some backends - e.g. the office Qwen3.8 server - expose
        # alongside "content") over "content", which is often empty on a
        # tool-calling turn.
        round_state.reasoning_text = msg.get("reasoning_content") or msg.get("content") or ""

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            # A text-only reply isn't reliably "I'm done" - it can also be the
            # model thinking out loud before continuing. Give it one nudge
            # before treating a second text-only reply in a row as a genuine stop.
            no_tool_call_streak += 1
            round_state.status = RoundStatus.DONE
            round_state.done_at = now()
            if no_tool_call_streak >= 2:
                state.status = AgentRunStatus.STOPPED_BY_MODEL
                return
            this_round.append({"role": "user", "content": "Continue - call propose_coilset with your next candidate. Only stop calling the tool if you are completely finished searching."})
            round_messages.append(this_round)
            history_log.append(f"Round {call_num}: (no proposal - replied with text instead)")
            continue
        no_tool_call_streak = 0

        round_state.status = RoundStatus.EVALUATING
        round_state.evaluating_started_at = now()
        round_summaries = []
        for tc in tool_calls:
            delta = dict(tc["function"]["arguments"])
            rationale = str(delta.pop(RATIONALE_KEY, "") or "")  # not a CoilSet field - coerce_delta would reject it
            candidate_dict = None  # set as soon as a CoilSet can be built, even if it later fails validation/solving
            try:
                delta = coerce_delta(delta, free_fields)  # fix string-typed args before they hit real arithmetic
                candidate = dataclasses.replace(baseline, **fixed_values, **delta)  # baseline + pins + just this delta
                candidate_dict = dataclasses.asdict(candidate)
                # Checked before the solve, so a violation never costs a FEMM
                # run - and rejected through the same path as bad geometry.
                audit = audit_changed_fields(baseline, candidate, allowed)
                if audit["unexpected_fields"]:
                    raise ValueError(f"candidate changed field(s) outside the allowed set: {sorted(audit['unexpected_fields'])}")
                scored = evaluate(candidate, baseline, baseline_metrics, spec, job_runner)  # the real FEMM solve + scoring
                scored.changed_fields = audit["changed_fields"]
                result = tool_result(scored, spec)
                round_state.candidates.append(CandidateResult(
                    delta=delta, valid=True, tool_result=result, candidate_coilset=candidate_dict, rationale=rationale,
                ))
                round_summaries.append(f"{delta} -> goal_met={scored.goal_met}: {objective_summary(result)}")
                if best is None or rank_key(scored) < rank_key(best):
                    best = scored
                    state.best = best
            except Exception as exc:
                # Invalid geometry becomes a rejected tool result, not a crash.
                result = {"valid": False, "error": str(exc)}
                round_state.candidates.append(CandidateResult(
                    delta=delta, valid=False, error=str(exc), candidate_coilset=candidate_dict, rationale=rationale,
                ))
                round_summaries.append(f"{delta} -> INVALID: {exc}")

            this_round.append({"role": "tool", "content": json.dumps(result)})  # feedback for the next round

        round_state.status = RoundStatus.DONE
        round_state.done_at = now()
        round_messages.append(this_round)
        history_log.append(f"Round {call_num}: " + " | ".join(round_summaries))

        # Stop condition #1: goal met (every required objective passed).
        if best is not None and best.goal_met:
            state.status = AgentRunStatus.GOAL_MET
            return
    else:
        # Stop condition #2: call budget reached without the model stopping voluntarily.
        state.status = AgentRunStatus.BUDGET_EXHAUSTED
