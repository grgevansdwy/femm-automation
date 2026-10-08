"""The evaluator-optimizer loop body: propose -> evaluate -> feed back ->
repeat, until a stop rule ends it. It mutates a shared AgentRunState in place
each round so a caller - a GUI panel, or orchestrator.py's thin CLI wrapper -
can poll live progress, reads spec.* for its settings, and solves candidates
through job_runner (cached, see scorer.evaluate_async).

The search does not stop at the first design that meets the goal: it keeps
looking for a better one and is ended by code, never by the model's say-so
(the model can only stop by not proposing). The stop rules, their order, and
the budget are in optimize_loop() and state.Budget.

Meeting the goal is not yet winning: when the search is about to stop, the
best passing design is put through the fragility check (services/fragility.py),
and only a design that survives it is the winner (state.winner).
"""

import dataclasses
import json
import time

from agent.services.llm_client import LLMError, call_llm
from agent.femm_agent.services.prompts import system_prompt
from agent.femm_agent.models.schema import (
    BASE_KEY, NEVER_EVALUATED, PROPOSE_TOOL_NAME, RATIONALE_KEY, Rejection, build_tools, coerce_delta,
)
from agent.femm_agent.services import fragility, progress
from agent.femm_agent.services.notebook import fields_without_a_line, notebook_block
from agent.femm_agent.services.scorer import (
    SimulationLimit, SolveFailed, evaluate_async, misses_summary, objective_summary, rank_key, score, tool_result,
    wait_for_jobs,
)
from agent.femm_agent.models.state import Budget, CandidateResult, RoundState, RoundStatus, Tried, now
from agent.input_chat.models.variables import (
    NUMERIC_FIELDS, allowed_fields, audit_changed_fields, free_fields_for, search_field_table,
)
from engine.core import validate_coilset


class RoundFailed(Exception):
    """Every AI try for one round failed (see settings.round_llm_attempts). Carries
    the last LLMError; AgentRunner decides whether to start the search over."""

    def __init__(self, round_num: int, error: LLMError) -> None:
        super().__init__(f"round {round_num}: {error.describe()}")
        self.round_num, self.error = round_num, error


RETRY_PAUSE_S = 5.0  # between AI tries - long enough for a momentary server hiccup to clear
MIN_REQUEST_S = 1.0  # with less than this left of the search's time limit, no AI request is started



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

    Whichever rule fires (except a stop by the user), passing designs that have
    not been checked go through the fragility check first (fragility.run_episode).
    If one survives, the search ends with it as the winner. If designs passed but
    none survived and the rule was not a budget limit, the search carries on
    instead of stopping: the model is told which designs failed, they no longer
    count as passes, and the no-improvement count starts again. The budget keeps
    a reserve for the check (Budget.reserved_simulations / reserved_s).

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
    budget.reserved_simulations = fragility.simulation_reserve(spec, budget.max_simulations)

    # The baseline's own solves are free: no budget. (Cache hits after the first time.)
    baseline_metrics = wait_for_jobs(evaluate_async(
        baseline, spec.distances, job_runner,
        config_id=spec.baseline_config_id, config_name=baseline.config_name))
    state.baseline_metrics = baseline_metrics

    # The run's own copy of the Search fields table (spec.settings is the
    # snapshot taken when the search started), never the live settings.
    table = search_field_table(spec.settings)
    free_fields = free_fields_for(baseline, spec.variable_spec, table)
    allowed = allowed_fields(baseline, spec.variable_spec, table)
    tools = build_tools(free_fields, spec.baseline_config_id)

    user_message = {"role": "user", "content": "Find the best candidate you can that meets the objective. Start proposing."}
    system_message = {"role": "system", "content": system_prompt(baseline, free_fields, spec, baseline_metrics)}

    # The search's memory, rebuilt into each request instead of accumulating
    # raw messages round after round (a batched run's raw history hit ~129 KB
    # by round 5 and timed out - see docs/Agent.md §6):
    # - the latest round, raw: its tool calls and every full tool result,
    # - the spec.keep_best_candidates best candidates so far, one line each,
    # - every other candidate already tried, a shorter line each,
    # - the lever notebook (notebook.py): what each small step did.
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
        state.progress = progress.assess(tried, counted, spec, free_fields,
                                         max(state.segment_start_round, state.progress_reset_round))
        # Once a design is owed a check, the search stops early enough to make it.
        budget.reserved_s = fragility.time_reserve(budget.time_limit_s) if fragility.unchecked_passing(state) else 0.0
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

    def conclude(reason: str) -> list | None:
        """A stop rule fired. Runs the fragility check on what is owed one and
        returns None when the search really ends (state.stop_reason is set), or
        the checks that failed when it should carry on instead."""
        state.stop_reason = reason
        if reason == "user_stopped":
            # No solve is started for a search the user stopped: nothing is checked.
            state.unchecked_pass = bool(fragility.unchecked_passing(state))
            fragility.refresh_best(state)
            return None
        episode = fragility.run_episode(state, job_runner)
        carry_on = (state.winner is None and episode.failed and not episode.out_of_budget and not state.cancelled
                    and reason not in ("rounds", "time", "simulations") and budget.exhausted() is None)
        if not carry_on:
            return None
        state.stop_reason = None
        state.progress_reset_round = len(state.rounds)
        state.stalled_notice_sent = False
        return episode.failed

    # A "continue" after a search that ended with a passing design it could
    # not check starts with that check.
    if fragility.unchecked_passing(state):
        fragility.run_episode(state, job_runner)

    while True:
        # Checkpoints for a user stop (see AgentRunState.cancelled) and the
        # budget: here, after the LLM call returns, before a round's solves
        # are queued, and before each candidate's result is collected - an
        # in-flight FEMM solve can't be interrupted, only waited out.
        reason = "user_stopped" if state.cancelled else budget.exhausted()
        if reason is not None:
            conclude(reason)
            return

        # memory must be role "user" not "system" - the office server's chat
        # template hard-rejects a system message anywhere but the very first one.
        notebook = notebook_block(tried, baseline, fixed_values)
        # The nudge: only once code's verdict says the search has stopped improving.
        untried = ([] if state.progress is None or state.progress.verdict == "improving"
                   else fields_without_a_line(tried, baseline, fixed_values, free_fields))
        memory = search_memory(tried, last_round_num, spec.keep_best_candidates, favourite, notebook, untried)
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
            round_state.reasoning_text = ("No proposal - the search was stopped." if state.cancelled
                                          else "No proposal - the search's time limit was reached.")
            finish_round(round_state)
            conclude("user_stopped" if state.cancelled else "time")
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

        reason = stop_rule() or ("model_stopped" if no_tool_call_streak >= 2 else None)
        if reason is not None:
            failed_checks = conclude(reason)
            if failed_checks is None:
                return
            # Designs passed, none survived the check, and the budget is not spent: carry on.
            notes.append(fragility.resume_note(failed_checks))
            no_tool_call_streak = 0
            judge(round_state)  # the failed designs no longer count as passes
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
    # Simulated designs by label - what a proposal's `base` may name. state.tried
    # holds every design of the run, so a label stays usable after a resume or a
    # "continue". This round's own designs are added as they are scored, so
    # candidates of one round can't build on each other.
    designs = {t.label: t for t in state.tried if t.label}
    round_state.status = RoundStatus.EVALUATING
    round_state.evaluating_started_at = now()

    proposals = [
        _prepare(tc, i, spec.candidates_per_round, baseline, fixed_values, free_fields, allowed, designs)
        for i, tc in enumerate(tool_calls)
    ]
    for p in proposals:
        if p.exception is not None:
            _warn(state, round_state, "optimize_loop.prepare", f"could not prepare {p.delta}", p.exception)

    launched: dict[str, dict] = {}  # candidate JSON -> its jobs, so a repeat within the round isn't solved twice
    refuse = None  # set once nothing more may be queued this round: the rejection code sent back for the rest
    if state.cancelled:
        refuse = "search_stopped"
    elif budget.remaining_s() <= 0:
        refuse = "time_limit"
    for p in proposals:
        if p.rejection is not None:
            continue
        key = json.dumps(p.candidate_dict, sort_keys=True, default=str)
        try:
            if refuse is None and key not in launched:
                launched[key] = evaluate_async(p.candidate, spec.distances, job_runner, budget=budget)
            if refuse is None:
                p.jobs = launched[key]
        except SimulationLimit:
            refuse = "simulation_limit"  # this candidate and every later one
        except Exception as exc:
            p.rejection = Rejection("solver_failed")
            _warn(state, round_state, "optimize_loop.launch", f"could not queue solves for {p.delta}", exc)
        if refuse is not None and p.rejection is None:
            p.rejection = Rejection(refuse)

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
        if state.cancelled and p.rejection is None:
            p.rejection = Rejection("search_stopped")  # its solves are not waited for
        if p.rejection is None:
            try:
                metrics = wait_for_jobs(p.jobs, on_wait=drop_queued_when_time_is_up)
                scored = score(p.candidate, metrics, baseline, baseline_metrics, spec)
                scored.changed_fields = p.changed_fields
                stored = tool_result(scored, spec)
                label = f"C{len(designs) + 1}"  # run-wide, in the order results are collected
                round_state.candidates.append(CandidateResult(
                    delta=p.delta, valid=True, tool_result=stored, candidate_coilset=p.candidate_dict,
                    rationale=p.rationale, label=label, base_label=p.base_label, step=p.step,
                ))
                designs[label] = Tried(round_state.round_num, p.delta, scored, stored, state.segment,
                                       label=label, base_label=p.base_label, step=p.step)
                state.tried.append(designs[label])
                # Best across the whole run - every attempt and segment - among
                # designs that have not failed a fragility check.
                fragility.refresh_best(state)
                result = {"label": label, **stored}  # what the model is sent: the label is how it names this design later
            except Exception as exc:
                if any(id(job) in dropped for job in p.jobs.values()):
                    p.rejection = Rejection("time_limit")
                else:
                    timed_out = isinstance(exc, SolveFailed) and exc.timed_out
                    p.rejection = Rejection("solver_timeout" if timed_out else "solver_failed")
                    _warn(state, round_state, "optimize_loop.collect", f"solve or scoring failed for {p.delta}", exc)
        if p.rejection is not None:
            # A rejection is a tool result in the fixed format, not a crash - and
            # never carries the exception's own text (that went to the run log).
            result = p.rejection.as_result()
            round_state.candidates.append(CandidateResult(
                delta=p.delta, valid=False, error=p.rejection.text(), error_code=p.rejection.code,
                rejection=result, candidate_coilset=p.candidate_dict, rationale=p.rationale,
                base_label=p.base_label, step=p.step,
            ))
            if p.rejection.code not in NEVER_EVALUATED:  # else nothing to remember - it's only in this round's raw results
                state.tried.append(Tried(round_state.round_num, p.delta, None, result, state.segment,
                                         base_label=p.base_label, step=p.step))

        # Feedback for the next round. tool_call_id pairs this result with
        # the call that proposed it - with several calls in one reply,
        # that's how the model tells which result is which.
        tool_message = {"role": "tool", "content": json.dumps(result)}
        if p.tool_call_id:
            tool_message["tool_call_id"] = p.tool_call_id
        this_round.append(tool_message)


@dataclasses.dataclass
class _Proposal:
    """One propose_coilset call on its way through a round. `rejection` set
    means turned down without a solve (or, after collection, a failed solve);
    `jobs` is set once its solves are queued (scorer.evaluate_async)."""
    tool_call_id: str | None
    delta: dict  # every value changed from the baseline: the base design's changes + this call's step
    rationale: str
    base_label: str | None = None  # the earlier design this call starts from; None = the baseline
    step: dict = dataclasses.field(default_factory=dict)  # only the values this call itself gave
    candidate: object = None
    candidate_dict: dict | None = None  # set as soon as a CoilSet can be built, even if it later fails validation/solving
    changed_fields: dict | None = None
    # Why it was turned down (None while it is still in the running). A code in
    # schema.NEVER_EVALUATED - past spec.candidates_per_round, or the search's
    # simulation or time limit was reached first - is not remembered as tried.
    rejection: Rejection | None = None
    jobs: dict | None = None
    exception: Exception | None = None  # an unexpected error while preparing, kept for the run log

    @property
    def error(self) -> str | None:
        """The rejection as one line for a person, or None."""
        return self.rejection.text() if self.rejection is not None else None


def _prepare(tc, index, limit, baseline, fixed_values, free_fields, allowed, designs=None) -> _Proposal:
    """Builds and validates one tool call's candidate - everything that can
    reject it before a FEMM solve is spent on it, in this order: over the
    per-round limit, tool name, argument shape, the `base` label, each field
    (open in this search, right type, inside its limit), the build, the audit
    against the baseline, something actually changed, and the geometry (the
    same check simulate() runs). Whatever shape the model's call has, this
    returns a rejected _Proposal (schema.Rejection, fed back to the model in
    its fixed format) rather than raising - one malformed call must not cost
    the round's other candidates, or the run.

    `designs` maps a label to the simulated design it names (a state.Tried): a call
    with `base` builds on that design's changes instead of on the bare baseline.
    Allowed fields are still audited against the ORIGINAL baseline, so nothing
    can enter through a base that a direct proposal couldn't make."""
    function = tc.get("function") or {}
    raw_args = function.get("arguments")
    delta = dict(raw_args) if isinstance(raw_args, dict) else {}
    rationale = str(delta.pop(RATIONALE_KEY, "") or "")  # not a CoilSet field - coerce_delta would reject it
    raw_base = delta.pop(BASE_KEY, None)  # nor is this - resolved below
    p = _Proposal(tool_call_id=tc.get("id"), delta=delta, rationale=rationale)
    try:
        if index >= limit:
            raise Rejection("over_candidate_limit", allowed=f"at most {limit} per round")
        if function.get("name") != PROPOSE_TOOL_NAME:
            raise Rejection("unknown_tool")
        if not isinstance(raw_args, dict):
            raise Rejection("bad_arguments")
        base = None
        if raw_base not in (None, ""):
            base = (designs or {}).get(str(raw_base).strip())
            if base is None:
                # Not the labels themselves: this rejection stays in memory, where a list would go stale.
                raise Rejection("unknown_base", BASE_KEY, "a label shown in your results or memory, or no base")
            p.base_label = base.label
        p.step = coerce_delta(delta, free_fields, NUMERIC_FIELDS)  # fix string-typed args before they hit real arithmetic
        p.delta = {**base.delta, **p.step} if base is not None else p.step
        candidate = dataclasses.replace(baseline, **fixed_values, **p.delta)  # baseline + pins + base's changes + this step
        p.candidate_dict = dataclasses.asdict(candidate)
        # Checked before the solve, so a violation never costs a FEMM run.
        audit = audit_changed_fields(baseline, candidate, allowed)
        if audit["unexpected_fields"]:
            raise Rejection("not_enabled", sorted(audit["unexpected_fields"])[0])
        # A candidate identical to the baseline is a wasted solve slot (and, when the baseline
        # already meets the goal, a false "goal met"). With pins the pinned start point is
        # itself a different design, so an empty delta stays a legitimate proposal then.
        if base is not None and p.candidate_dict == dataclasses.asdict(base.scored.coilset):
            raise Rejection("no_change", detail=f"This changes nothing from {base.label}. Change at least one field.")
        if not audit["changed_fields"]:
            raise Rejection("no_change")
        problems = validate_coilset(candidate)
        if problems:
            coil, reason = problems[0]
            raise Rejection("invalid_geometry", coil, detail=_geometry_detail(coil, reason))
        p.candidate, p.changed_fields = candidate, audit["changed_fields"]
    except Rejection as rejection:
        p.rejection = rejection
    except Exception as exc:
        p.rejection, p.exception = Rejection("bad_arguments"), exc
    return p


def _geometry_detail(coil: str, reason: str) -> str:
    """The validity check's own sentence for one coil (numbers only, built by
    femm_service.check_valid_coil - no exception text), tidied into one line."""
    return f"The {coil} coil is not valid: {reason.strip().rstrip(',')}."


def search_memory(tried: list[Tried], latest_round: int | None, keep_best: int, favourite: str | None = None,
                  notebook=(), untried=()) -> str:
    """Everything the model should remember besides its latest round (which
    it gets raw, full results and all): the `keep_best` best candidates so
    far, ranked by scorer.rank_key() and summarized per objective, then a
    shorter line for every other candidate already tried, so it doesn't
    propose them again, then the lever notebook (`notebook`, from
    notebook.notebook_block()) and - only when the caller found the search is
    no longer improving - `untried`, the free fields with no notebook line.
    Rebuilt from scratch each round, so the prompt stays roughly flat instead
    of growing by every result ever sent. "" before anything has been tried.
    `latest_round` None = no round is being sent raw (the search just
    resumed), so every candidate is listed here."""
    valid = sorted((t for t in tried if t.scored is not None and not t.check_failed),
                   key=lambda t: rank_key(t.scored, favourite))
    top = valid[:keep_best]
    # Met the goal but did not survive the fragility check: never a winner. Always
    # listed, whichever round they came from, so the model stops building on them.
    fragile = [t for t in tried if t.check_failed]
    others = [t for t in tried if t not in top and t not in fragile and t.round_num != latest_round]
    if not top and not others and not fragile and not notebook and not untried:
        return ""

    lines = []
    if top:
        lines.append(f"Best {len(top)} candidate(s) so far, best first (each = baseline + the listed changes):")
        lines += [f"  #{i} {t.name}: {t.delta} -> goal_met={t.result['goal_met']}: {objective_summary(t.result)}"
                  + (" [survived the fragility check]" if t.check is not None else "")
                  for i, t in enumerate(top, start=1)]
    if fragile:
        lines.append("Met the goal but failed the fragility check (the goal must still be met with every dimension "
                     "changed together by -2% to +2%) - not winners:")
        lines += [f"  {t.name}: {t.delta} -> {t.check['summary']}" for t in fragile]
    if others:
        lines.append("Other candidates already tried - don't propose these again:")
        lines += [f"  {t.name}: {t.delta} -> " +
                  (f"misses: {misses_summary(t.result)}" if t.scored is not None else f"rejected: {_rejection_summary(t.result)}")
                  for t in others]
    lines += notebook
    if untried:
        lines.append(f"Fields with no notebook line yet: {', '.join(untried)}.")
    if latest_round is not None and any(t.round_num == latest_round for t in tried):
        lines.append("Your latest turn's full results follow.")
    return "\n".join(lines)


def _rejection_summary(result: dict) -> str:
    """A rejected candidate's tool result (schema.Rejection.as_result()) as a
    few words for the already-tried list: its code, the field, the allowed values."""
    parts = [str(result.get("error"))]
    if result.get("field"):
        parts.append(str(result["field"]))
    if result.get("allowed"):
        parts.append(f"(allowed: {result['allowed']})")
    return " ".join(parts)
