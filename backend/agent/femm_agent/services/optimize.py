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
import time

from agent.services.llm_client import LLMError, call_llm
from agent.femm_agent.services.prompts import system_prompt
from agent.femm_agent.models.schema import PROPOSE_TOOL_NAME, RATIONALE_KEY, build_tools, coerce_delta
from agent.femm_agent.services.scorer import (
    Scored, evaluate_async, misses_summary, objective_summary, rank_key, score, tool_result, wait_for_jobs,
)
from agent.femm_agent.models.state import AgentRunStatus, CandidateResult, RoundState, RoundStatus, now
from agent.input_chat.models.variables import allowed_fields, audit_changed_fields, free_fields_for


class RoundFailed(Exception):
    """Every AI try for one round failed (see settings.round_llm_attempts). Carries
    the last LLMError; AgentRunner decides whether to start the search over."""

    def __init__(self, round_num: int, error: LLMError) -> None:
        super().__init__(f"round {round_num}: {error.describe()}")
        self.round_num, self.error = round_num, error


RETRY_PAUSE_S = 5.0  # between AI tries - long enough for a momentary server hiccup to clear


def _ask_for_proposals(state, round_state, send_messages, tools):
    """The round's AI request, tried up to settings.round_llm_attempts times. Each
    failed try is recorded on the round and the run (llm_failures). Returns
    the reply, None if the user restarted, or raises RoundFailed."""
    settings = state.spec.settings
    for ai_try in range(1, settings.round_llm_attempts + 1):
        if state.cancelled:
            return None
        round_state.ai_try = ai_try
        round_state.proposing_started_at = now()
        stats: dict = {}
        try:
            msg = call_llm(
                send_messages, tools, settings=settings, should_abort=lambda: state.cancelled, stats=stats,
                # Live chain-of-thought, visible in the GUI the instant it
                # arrives - not just after the whole round finishes (see
                # llm_client.call_llm's docstring). Overwritten with the
                # same value again just below once the reply lands, so the
                # two paths can never disagree.
                on_update=lambda content, reasoning: setattr(round_state, "reasoning_text", reasoning or content),
            )
            round_state.llm_stats = stats
            return msg
        except LLMError as exc:
            if exc.kind == "aborted":
                return None
            failure = {**exc.as_dict(), "search_attempt": state.search_attempt, "round": round_state.round_num,
                       "ai_try": ai_try, "at": now().isoformat()}
            round_state.llm_failures.append(failure)
            state.llm_failures.append(failure)
            if ai_try == settings.round_llm_attempts:
                raise RoundFailed(round_state.round_num, exc) from exc
            round_state.reasoning_text = f"AI try {ai_try} failed: {exc.describe()} Retrying..."
            deadline = time.monotonic() + RETRY_PAUSE_S
            while time.monotonic() < deadline and not state.cancelled:
                time.sleep(0.2)
    return None


def optimize_loop(state, job_runner, on_round_done=None) -> None:
    """One search attempt, from zero memory: nothing from an earlier attempt
    is sent to the model. Rounds are appended to state.rounds with run-wide
    numbering (so a restarted attempt's rounds follow the first attempt's),
    and state.best stays the best across attempts. Raises RoundFailed when a
    round's AI request fails every try - see AgentRunner._run().

    `on_round_done(state)`, if given, is called each time a round finishes (however it
    ended) - AgentRunner uses it to save the rounds so far."""
    spec = state.spec
    baseline = state.baseline_coilset
    fixed_values = spec.variable_spec.fixed_values  # applied to every candidate, see VariableSpec

    baseline_metrics = wait_for_jobs(evaluate_async(
        baseline, spec.distances, job_runner,
        config_id=spec.baseline_config_id, config_name=baseline.config_name))
    state.baseline_metrics = baseline_metrics

    free_fields = free_fields_for(baseline, spec.variable_spec)
    allowed = allowed_fields(baseline, spec.variable_spec)
    tools = build_tools(free_fields, spec.baseline_config_id)

    user_message = {"role": "user", "content": "Find a candidate that meets the objective. Start proposing."}
    system_message = {"role": "system", "content": system_prompt(baseline, free_fields, spec, baseline_metrics)}

    # The search's memory, rebuilt into each request instead of accumulating
    # raw messages round after round (a batched run's raw history hit ~129 KB
    # by round 5 and timed out - see docs/Agent.md §6):
    # - the latest round, raw: its tool calls and every full tool result,
    # - the spec.keep_best_candidates best candidates so far, one line each,
    # - every other candidate already tried, a shorter line each.
    tried: list[_Tried] = []
    last_round: list[dict] = []

    no_tool_call_streak = 0  # consecutive text-only replies

    def finish_round(round_state):
        round_state.status = RoundStatus.DONE
        round_state.done_at = now()
        if on_round_done is not None:
            on_round_done(state)

    for call_num in range(1, spec.max_tool_calls + 1):
        # Cancellation checkpoints (see AgentRunState.cancelled): here, after
        # the LLM call returns, before a round's solves are queued, and
        # before each candidate's result is collected - an
        # in-flight LLM request or FEMM solve can't be interrupted, only
        # abandoned once it comes back.
        if state.cancelled:
            return

        # memory must be role "user" not "system" - the office server's chat
        # template hard-rejects a system message anywhere but the very first one.
        memory = search_memory(tried, call_num - 1, spec.keep_best_candidates)
        send_messages = [system_message, user_message] + ([{"role": "user", "content": memory}] if memory else []) + last_round

        round_state = RoundState(round_num=len(state.rounds) + 1, search_attempt=state.search_attempt,
                                 attempt_round=call_num)
        state.rounds.append(round_state)

        try:
            msg = _ask_for_proposals(state, round_state, send_messages, tools)
        except RoundFailed as exc:
            round_state.reasoning_text = f"No proposal - every AI try failed. Last: {exc.error.describe()}"
            finish_round(round_state)
            raise
        if msg is None or state.cancelled:
            return

        # Resent next round without "reasoning_content": the chain-of-thought
        # was ~28% of that 129 KB prompt, and the tool calls + results are
        # what the model actually builds on.
        this_round = [{k: v for k, v in msg.items() if k != "reasoning_content"}]
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
            finish_round(round_state)
            if no_tool_call_streak >= 2:
                state.status = AgentRunStatus.STOPPED_BY_MODEL
                return
            this_round.append({"role": "user", "content": "Continue - call propose_coilset with your next candidate(s). Only stop calling the tool if you are completely finished searching."})
            last_round = this_round
            continue
        no_tool_call_streak = 0

        round_state.status = RoundStatus.EVALUATING
        round_state.evaluating_started_at = now()

        # The round's candidates are solved concurrently, in three passes:
        # 1. prepare - build/validate every candidate (instant, no FEMM),
        # 2. launch  - queue every candidate's solves on the shared pool at
        #              once (non-blocking),
        # 3. collect - wait for and score each candidate in proposal order.
        # Starting all solves before waiting on any is what lets them run side
        # by side; each is still waited on and scored in its own try/except,
        # so one bad candidate never costs the others their results.
        proposals = [
            _prepare(tc, i, spec.candidates_per_round, baseline, fixed_values, free_fields, allowed)
            for i, tc in enumerate(tool_calls)
        ]

        if state.cancelled:
            return
        launched: dict[str, dict] = {}  # candidate JSON -> its jobs, so a repeat within the round isn't solved twice
        for p in proposals:
            if p.error is not None:
                continue
            key = json.dumps(p.candidate_dict, sort_keys=True, default=str)
            try:
                if key not in launched:
                    launched[key] = evaluate_async(p.candidate, spec.distances, job_runner,
                                                    cloned_from=spec.baseline_config_id)
                p.jobs = launched[key]
            except Exception as exc:
                p.error = str(exc)

        for p in proposals:
            if state.cancelled:
                return
            if p.error is None:
                try:
                    scored = score(p.candidate, wait_for_jobs(p.jobs), baseline, baseline_metrics, spec)
                    scored.changed_fields = p.changed_fields
                    result = tool_result(scored, spec)
                    round_state.candidates.append(CandidateResult(
                        delta=p.delta, valid=True, tool_result=result, candidate_coilset=p.candidate_dict,
                        rationale=p.rationale,
                    ))
                    tried.append(_Tried(call_num, p.delta, scored, result))
                    # Best across every search attempt, not just this one.
                    if state.best is None or rank_key(scored) < rank_key(state.best):
                        state.best = scored
                except Exception as exc:
                    p.error = str(exc)
            if p.error is not None:
                # Invalid geometry becomes a rejected tool result, not a crash.
                result = {"valid": False, "error": p.error}
                round_state.candidates.append(CandidateResult(
                    delta=p.delta, valid=False, error=p.error, candidate_coilset=p.candidate_dict,
                    rationale=p.rationale,
                ))
                if not p.over_limit:  # never evaluated, so nothing to remember - it's only in this round's raw results
                    tried.append(_Tried(call_num, p.delta, None, result))

            # Feedback for the next round. tool_call_id pairs this result with
            # the call that proposed it - with several calls in one reply,
            # that's how the model tells which result is which.
            tool_message = {"role": "tool", "content": json.dumps(result)}
            if p.tool_call_id:
                tool_message["tool_call_id"] = p.tool_call_id
            this_round.append(tool_message)

        finish_round(round_state)
        last_round = this_round

        # Stop condition #1: goal met (every required objective passed).
        if state.best is not None and state.best.goal_met:
            state.status = AgentRunStatus.GOAL_MET
            return
    else:
        # Stop condition #2: call budget reached without the model stopping voluntarily.
        state.status = AgentRunStatus.BUDGET_EXHAUSTED


@dataclasses.dataclass
class _Proposal:
    """One propose_coilset call on its way through a round. `error` set
    means rejected without a solve (or, after collection, a failed solve);
    `jobs` is set once its solves are queued (scorer.evaluate_async)."""
    tool_call_id: str | None
    delta: dict
    rationale: str
    candidate: object = None
    candidate_dict: dict | None = None  # set as soon as a CoilSet can be built, even if it later fails validation/solving
    changed_fields: dict | None = None
    error: str | None = None
    over_limit: bool = False  # past spec.candidates_per_round - rejected unseen, not remembered as tried
    jobs: dict | None = None


def _prepare(tc, index, limit, baseline, fixed_values, free_fields, allowed) -> _Proposal:
    """Builds and validates one tool call's candidate - everything that can
    reject it before a FEMM solve is spent on it. Whatever shape the model's call
    has, this returns a rejected _Proposal (its error is fed back to the model)
    rather than raising - one malformed call must not cost the round's other
    candidates, or the run."""
    function = tc.get("function") or {}
    raw_args = function.get("arguments")
    delta = dict(raw_args) if isinstance(raw_args, dict) else {}
    rationale = str(delta.pop(RATIONALE_KEY, "") or "")  # not a CoilSet field - coerce_delta would reject it
    p = _Proposal(tool_call_id=tc.get("id"), delta=delta, rationale=rationale)
    if index >= limit:
        p.over_limit = True
        p.error = (f"not simulated: over the limit of {limit} candidates per round - "
                   "propose it again next round if it's still worth trying")
        return p
    if function.get("name") != PROPOSE_TOOL_NAME:
        p.error = f"unknown tool {function.get('name')!r} - the only tool is {PROPOSE_TOOL_NAME}"
        return p
    if not isinstance(raw_args, dict):
        p.error = (f"{PROPOSE_TOOL_NAME}'s arguments must be a JSON object of field values, "
                   f"got {type(raw_args).__name__}")
        return p
    try:
        p.delta = coerce_delta(delta, free_fields)  # fix string-typed args before they hit real arithmetic
        candidate = dataclasses.replace(baseline, **fixed_values, **p.delta)  # baseline + pins + just this delta
        p.candidate_dict = dataclasses.asdict(candidate)
        # Checked before the solve, so a violation never costs a FEMM run -
        # and rejected through the same path as bad geometry.
        audit = audit_changed_fields(baseline, candidate, allowed)
        if audit["unexpected_fields"]:
            raise ValueError(f"candidate changed field(s) outside the allowed set: {sorted(audit['unexpected_fields'])}")
        # A candidate identical to the baseline is a wasted solve slot (and, when the baseline
        # already meets the goal, a false "goal met"). With pins the pinned start point is
        # itself a different design, so an empty delta stays a legitimate proposal then.
        if not audit["changed_fields"]:
            raise ValueError("this changes nothing from the baseline - change at least one field")
        p.candidate, p.changed_fields = candidate, audit["changed_fields"]
    except Exception as exc:
        p.error = str(exc)
    return p


@dataclasses.dataclass(eq=False)  # identity equality: search_memory() tells "in the top list" apart by object, not value
class _Tried:
    """One candidate the search has evaluated, as its memory keeps it.
    `scored` is None for a rejected candidate (bad field, invalid geometry,
    failed solve); `result` is what the model was sent for it."""
    round_num: int
    delta: dict
    scored: Scored | None
    result: dict


def search_memory(tried: list[_Tried], latest_round: int, keep_best: int) -> str:
    """Everything the model should remember besides its latest round (which
    it gets raw, full results and all): the `keep_best` best candidates so
    far, ranked by scorer.rank_key() and summarized per objective, then a
    shorter line for every other candidate already tried, so it doesn't
    propose them again. Rebuilt from scratch each round, so the prompt stays
    roughly flat instead of growing by every result ever sent. "" before
    anything has been tried."""
    valid = sorted((t for t in tried if t.scored is not None), key=lambda t: rank_key(t.scored))
    top = valid[:keep_best]
    others = [t for t in tried if t not in top and t.round_num != latest_round]
    if not top and not others:
        return ""

    lines = []
    if top:
        lines.append(f"Best {len(top)} candidate(s) so far, best first (each = baseline + the listed changes):")
        lines += [f"  #{i} round {t.round_num}: {t.delta} -> goal_met={t.result['goal_met']}: {objective_summary(t.result)}"
                  for i, t in enumerate(top, start=1)]
    if others:
        lines.append("Other candidates already tried - don't propose these again:")
        lines += [f"  round {t.round_num}: {t.delta} -> " +
                  (f"misses: {misses_summary(t.result)}" if t.scored is not None else f"rejected: {t.result['error'][:160]}")
                  for t in others]
    if any(t.round_num == latest_round for t in tried):
        lines.append("Your latest turn's full results follow.")
    return "\n".join(lines)
