"""The evaluator-optimizer loop body: propose -> evaluate -> feed back ->
repeat, until a stop rule ends it. It mutates a shared AgentRunState in place
each round so a caller - a GUI panel, or orchestrator.py's thin CLI wrapper -
can poll live progress, reads spec.* for its settings, and solves candidates
through job_runner (cached, see scorer.evaluate_async).

The search does not stop at the first design that meets the goal: it keeps
looking for a better one and is ended by code, never by the model's say-so
(the model can only stop by not proposing). The stop rules, their order, and
the budget are in optimize_loop() and state.Budget.
"""

import dataclasses
import json
import time

from agent.services.llm_client import LLMError, call_llm
from agent.femm_agent.services.prompts import system_prompt
from agent.femm_agent.models.schema import PROPOSE_TOOL_NAME, RATIONALE_KEY, build_tools, coerce_delta
from agent.femm_agent.services import progress
from agent.femm_agent.services.scorer import (
    SimulationLimit, evaluate_async, misses_summary, objective_summary, rank_key, score, tool_result, wait_for_jobs,
)
from agent.femm_agent.models.state import Budget, CandidateResult, RoundState, RoundStatus, Tried, now
from agent.input_chat.models.variables import allowed_fields, audit_changed_fields, free_fields_for


class RoundFailed(Exception):
    """Every AI try for one round failed (see settings.round_llm_attempts). Carries
    the last LLMError; AgentRunner decides whether to start the search over."""

    def __init__(self, round_num: int, error: LLMError) -> None:
        super().__init__(f"round {round_num}: {error.describe()}")
        self.round_num, self.error = round_num, error


RETRY_PAUSE_S = 5.0  # between AI tries - long enough for a momentary server hiccup to clear
MIN_REQUEST_S = 1.0  # with less than this left of the search's time limit, no AI request is started

_SIMULATION_LIMIT_ERROR = ("not simulated: the search's simulation limit was reached - "
                           "this proposal was not tried")
_TIME_LIMIT_ERROR = "not simulated: the search's time limit was reached - this proposal was not tried"


def _ask_for_proposals(state, round_state, send_messages, tools):
    """The round's AI request, tried up to settings.round_llm_attempts times. Each
    failed try is recorded on the round and the run (llm_failures). Returns
    the reply; None if the request was abandoned - the user stopped the
    search, or the search's time limit ran out (the caller tells which); or
    raises RoundFailed.

    No request starts after the search's time limit, and each one gets at
    most the time that is left - so a request cut short by that limit is the
    limit ending the search, not a failed try."""
    settings = state.spec.settings
    budget = state.budget
    for ai_try in range(1, settings.round_llm_attempts + 1):
        if state.cancelled or budget.remaining_s() < MIN_REQUEST_S:
            return None
        round_state.ai_try = ai_try
        round_state.proposing_started_at = now()
        stats: dict = {}
        try:
            msg = call_llm(
                send_messages, tools, settings=settings, should_abort=lambda: state.cancelled, stats=stats,
                time_limit_s=min(settings.llm_time_limit_s, budget.remaining_s()),
                log=_bound_log(state, round_state),
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
            if exc.kind in ("no_response", "too_slow") and budget.remaining_s() <= 0:
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


def _bound_log(state, round_state):
    """The run's log with this round's position attached, or None (no log)."""
    if state.log is None:
        return None
    return state.log.bind(phase="search", round=round_state.round_num, search_attempt=state.search_attempt,
                          ai_try=round_state.ai_try)


def _warn(state, round_state, where: str, message: str, exc: Exception) -> None:
    """Writes the full exception to the run's log - the model and the GUI only
    ever get str(exc)."""
    log = _bound_log(state, round_state)
    if log is not None:
        log.warning(where, message, exc)


def optimize_loop(state, job_runner, on_round_done=None) -> None:
    """Runs the search until a stop rule ends it, leaving which one in
    state.stop_reason (AgentRunner turns that into the run's status). Raises
    RoundFailed when a round's AI request fails every try - see AgentRunner._run().

    Resumable: everything the search has learned is on the state (state.tried,
    state.best, state.budget), so calling this again - after a failed round,
    or for a "continue" segment with a fresh budget - carries on with the
    model's memory intact. Rounds are appended to state.rounds with run-wide numbering.

    Stop rules, checked after every round, in this order:
      1. the user stopped the search                          -> "user_stopped"
      2. a budget limit is reached (state.Budget)             -> "rounds" | "time" | "simulations"
      3. a design meets the goal and the goal has nothing
         that could be pushed further (only `match` entries)  -> "nothing_left"
      4. after a pass: settings.plateau_rounds rounds in a
         row without a better design                          -> "plateau"
      5. before any pass: settings.stall_rounds rounds without
         getting closer - the model is told once, with the
         fields it hasn't tried; still nothing
         progress.STALL_GRACE_ROUNDS rounds later             -> "stalled"
      6. two text-only replies in a row                       -> "model_stopped"
    The time limit is also checked before an AI request and before solves are
    queued: neither starts after it.

    `on_round_done(state)`, if given, is called each time a round finishes (however it
    ended) - AgentRunner uses it to save the rounds so far."""
    spec = state.spec
    settings = spec.settings
    baseline = state.baseline_coilset
    fixed_values = spec.variable_spec.fixed_values  # applied to every candidate, see VariableSpec
    favourite = spec.favourite_metric
    if state.budget is None:
        state.budget = Budget.for_spec(spec)
    budget = state.budget
    budget.start()

    # The baseline's own solves are free: no budget. (Cache hits after the first time.)
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
    # After a resume or a "continue" there is no raw latest round, so every
    # candidate tried so far is in the one-line memory instead.
    tried = state.tried
    last_round: list[dict] = []
    last_round_num: int | None = None

    no_tool_call_streak = 0  # consecutive text-only replies
    rounds_this_call = 0

    def finish_round(round_state):
        round_state.status = RoundStatus.DONE
        round_state.done_at = now()
        if on_round_done is not None:
            on_round_done(state)

    def judge(round_state):
        """Code's verdict after this round, kept on the state and the round."""
        counted = [r.round_num for r in state.rounds if not r.no_reply]
        state.progress = progress.assess(tried, counted, spec, free_fields, state.segment_start_round)
        round_state.verdict = state.progress.verdict
        round_state.rounds_since_improvement = state.progress.rounds_since_improvement

    def stop_rule() -> str | None:
        """Rules 1-5 above; rule 6 is checked by the caller, which owns the streak."""
        if state.cancelled:
            return "user_stopped"
        limit = budget.exhausted()
        if limit is not None:
            return limit
        verdict = state.progress
        if verdict is None:
            return None
        if verdict.passed:
            if not progress.can_improve_after_pass(spec):
                return "nothing_left"
            if verdict.rounds_since_improvement >= settings.plateau_rounds:
                return "plateau"
        elif (state.stalled_notice_sent
              and verdict.rounds_since_improvement >= settings.stall_rounds + progress.STALL_GRACE_ROUNDS):
            return "stalled"
        return None

    def stalled_notice_due() -> bool:
        verdict = state.progress
        return (verdict is not None and not verdict.passed and not state.stalled_notice_sent
                and verdict.rounds_since_improvement >= settings.stall_rounds)

    while True:
        # Checkpoints for a user stop (see AgentRunState.cancelled) and the
        # budget: here, after the LLM call returns, before a round's solves
        # are queued, and before each candidate's result is collected - an
        # in-flight FEMM solve can't be interrupted, only waited out.
        state.stop_reason = "user_stopped" if state.cancelled else budget.exhausted()
        if state.stop_reason is not None:
            return

        # memory must be role "user" not "system" - the office server's chat
        # template hard-rejects a system message anywhere but the very first one.
        memory = search_memory(tried, last_round_num, spec.keep_best_candidates, favourite)
        send_messages = [system_message, user_message] + ([{"role": "user", "content": memory}] if memory else []) + last_round

        rounds_this_call += 1
        round_state = RoundState(round_num=len(state.rounds) + 1, search_attempt=state.search_attempt,
                                 attempt_round=rounds_this_call, segment=state.segment)
        state.rounds.append(round_state)
        budget.rounds_used += 1  # every round counts, a text-only reply or a failed request included

        try:
            msg = _ask_for_proposals(state, round_state, send_messages, tools)
        except RoundFailed as exc:
            round_state.no_reply = True
            round_state.reasoning_text = f"No proposal - every AI try failed. Last: {exc.error.describe()}"
            finish_round(round_state)
            raise
        if msg is None:
            # Abandoned: the user stopped the search, or its time limit ran out.
            round_state.no_reply = True
            state.stop_reason = "user_stopped" if state.cancelled else "time"
            round_state.reasoning_text = ("No proposal - the search was stopped." if state.cancelled
                                          else "No proposal - the search's time limit was reached.")
            finish_round(round_state)
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
        notes: list[str] = []  # code's own messages to the model, sent after this round's results

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            # A text-only reply isn't reliably "I'm done" - it can also be the
            # model thinking out loud before continuing. Give it one nudge
            # before treating a second text-only reply in a row as a genuine stop.
            no_tool_call_streak += 1
            notes.append("Continue - call propose_coilset with your next candidate(s). Only stop calling the "
                         "tool if you are completely finished searching.")
        else:
            no_tool_call_streak = 0
            _evaluate_round(state, round_state, tool_calls, this_round, job_runner, baseline, baseline_metrics,
                            fixed_values, free_fields, allowed)

        judge(round_state)
        finish_round(round_state)

        state.stop_reason = stop_rule() or ("model_stopped" if no_tool_call_streak >= 2 else None)
        if state.stop_reason is not None:
            return
        if stalled_notice_due():
            notes.append(progress.stalled_notice(state.progress))
            state.stalled_notice_sent = True
        if notes:
            this_round.append({"role": "user", "content": "\n\n".join(notes)})
        last_round, last_round_num = this_round, round_state.round_num


def _evaluate_round(state, round_state, tool_calls, this_round, job_runner, baseline, baseline_metrics,
                    fixed_values, free_fields, allowed) -> None:
    """Solves and scores one round's proposals, appending each result to the
    round, to state.tried and - as the tool result the model sees next - to
    `this_round`.

    The round's candidates are solved concurrently, in three passes:
    1. prepare - build/validate every candidate (instant, no FEMM),
    2. launch  - queue every candidate's solves on the shared pool at
                 once (non-blocking), in proposal order while the
                 simulation budget allows,
    3. collect - wait for and score each candidate in proposal order.
    Starting all solves before waiting on any is what lets them run side
    by side; each is still waited on and scored in its own try/except,
    so one bad candidate never costs the others their results."""
    spec, budget = state.spec, state.budget
    round_state.status = RoundStatus.EVALUATING
    round_state.evaluating_started_at = now()

    proposals = [
        _prepare(tc, i, spec.candidates_per_round, baseline, fixed_values, free_fields, allowed)
        for i, tc in enumerate(tool_calls)
    ]

    launched: dict[str, dict] = {}  # candidate JSON -> its jobs, so a repeat within the round isn't solved twice
    refuse = None  # set once nothing more may be queued this round: the reason sent back for the rest
    if state.cancelled:
        refuse = "not simulated: the search was stopped"
    elif budget.remaining_s() <= 0:
        refuse = _TIME_LIMIT_ERROR
    for p in proposals:
        if p.error is not None:
            continue
        key = json.dumps(p.candidate_dict, sort_keys=True, default=str)
        try:
            if refuse is None and key not in launched:
                launched[key] = evaluate_async(p.candidate, spec.distances, job_runner, budget=budget)
            if refuse is None:
                p.jobs = launched[key]
        except SimulationLimit:
            refuse = _SIMULATION_LIMIT_ERROR  # this candidate and every later one
        except Exception as exc:
            p.error = str(exc)
            _warn(state, round_state, "optimize_loop.launch", f"could not queue solves for {p.delta}", exc)
        if refuse is not None and p.error is None:
            p.error, p.over_limit = refuse, True

    dropped: set[int] = set()  # id() of each job dropped from the queue because time ran out
    time_up = False

    def drop_queued_when_time_is_up():
        """No solve starts after the search's time limit: once it has passed,
        every solve of this round still waiting in the queue is dropped (one
        already running is waited out). A dropped solve was never run, so it
        is not charged to the simulation budget."""
        nonlocal time_up
        if time_up or budget.remaining_s() > 0:
            return
        time_up = True
        for jobs in launched.values():
            for job in jobs.values():
                if not isinstance(job, dict) and job.status not in ("done", "failed") and job_runner.cancel(job.id):
                    dropped.add(id(job))
                    budget.simulations_used -= 1

    for p in proposals:
        if state.cancelled and p.error is None:
            p.error, p.over_limit = "not simulated: the search was stopped", True  # its solves are not waited for
        if p.error is None:
            try:
                metrics = wait_for_jobs(p.jobs, on_wait=drop_queued_when_time_is_up)
                scored = score(p.candidate, metrics, baseline, baseline_metrics, spec)
                scored.changed_fields = p.changed_fields
                result = tool_result(scored, spec)
                round_state.candidates.append(CandidateResult(
                    delta=p.delta, valid=True, tool_result=result, candidate_coilset=p.candidate_dict,
                    rationale=p.rationale,
                ))
                state.tried.append(Tried(round_state.round_num, p.delta, scored, result, state.segment))
                # Best across the whole run - every attempt and segment.
                if state.best is None or rank_key(scored, spec.favourite_metric) < rank_key(state.best, spec.favourite_metric):
                    state.best = scored
            except Exception as exc:
                if any(id(job) in dropped for job in p.jobs.values()):
                    p.error, p.over_limit = _TIME_LIMIT_ERROR, True
                else:
                    p.error = str(exc)
                    _warn(state, round_state, "optimize_loop.collect", f"solve or scoring failed for {p.delta}", exc)
        if p.error is not None:
            # Invalid geometry becomes a rejected tool result, not a crash.
            result = {"valid": False, "error": p.error}
            round_state.candidates.append(CandidateResult(
                delta=p.delta, valid=False, error=p.error, candidate_coilset=p.candidate_dict,
                rationale=p.rationale,
            ))
            if not p.over_limit:  # never evaluated, so nothing to remember - it's only in this round's raw results
                state.tried.append(Tried(round_state.round_num, p.delta, None, result, state.segment))

        # Feedback for the next round. tool_call_id pairs this result with
        # the call that proposed it - with several calls in one reply,
        # that's how the model tells which result is which.
        tool_message = {"role": "tool", "content": json.dumps(result)}
        if p.tool_call_id:
            tool_message["tool_call_id"] = p.tool_call_id
        this_round.append(tool_message)


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
    # Never evaluated - past spec.candidates_per_round, or the search's simulation
    # or time limit was reached first. Rejected unseen, not remembered as tried.
    over_limit: bool = False
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


def search_memory(tried: list[Tried], latest_round: int | None, keep_best: int, favourite: str | None = None) -> str:
    """Everything the model should remember besides its latest round (which
    it gets raw, full results and all): the `keep_best` best candidates so
    far, ranked by scorer.rank_key() and summarized per objective, then a
    shorter line for every other candidate already tried, so it doesn't
    propose them again. Rebuilt from scratch each round, so the prompt stays
    roughly flat instead of growing by every result ever sent. "" before
    anything has been tried. `latest_round` None = no round is being sent
    raw (the search just resumed), so every candidate is listed here."""
    valid = sorted((t for t in tried if t.scored is not None), key=lambda t: rank_key(t.scored, favourite))
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
    if latest_round is not None and any(t.round_num == latest_round for t in tried):
        lines.append("Your latest turn's full results follow.")
    return "\n".join(lines)
