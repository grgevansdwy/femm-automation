"""Rebuilds a search's live state from what the database holds, so a run that
is no longer in memory (the app was restarted, or an older chat is reopened)
can be shown and continued with everything it had tried.

Nothing is trusted from the stored scores: each stored candidate's solves are
requested again - cache hits, since the run stored its geometry and the
solver cache is keyed by geometry and distance - and scored again against
the run's objectives. A solve that isn't cached any more is run again.
"""

import dataclasses
from datetime import datetime

from agent.femm_agent.models.state import (
    AgentRunState, AgentRunStatus, CandidateResult, RoundState, RoundStatus, Tried,
)
from agent.femm_agent.services import fragility, progress
from agent.femm_agent.services.scorer import evaluate_async, rank_key, score, tool_result, wait_for_jobs
from agent.input_chat.models.spec import spec_from_dict
from agent.femm_agent.models.schema import NEVER_EVALUATED
from agent.input_chat.models.variables import (
    allowed_fields, audit_changed_fields, free_fields_for, search_field_table,
)


class RestoreError(Exception):
    """The run can't be rebuilt; the message says why in one line."""


def _baseline(record):
    from engine.models.coilset import CoilSet
    from engine.repositories.coilset_repository import get_coilset
    stored = record.get("baseline_coilset")
    try:
        if stored:
            return CoilSet(**stored)
        return get_coilset(record["baseline_config_id"])  # a run from before the baseline was copied into it
    except KeyError:
        raise RestoreError(f"its baseline design {record['baseline_config_id']!r} no longer exists") from None
    except TypeError as exc:
        raise RestoreError(f"its stored baseline no longer fits this version's design fields ({exc})") from None


def restore_run(ai_run_id: str, job_runner) -> AgentRunState:
    """The AgentRunState of a stored run: its plan, every saved round, the
    tried list (the model's memory), the best design and the progress
    verdict. The budget is not restored - what each segment spent is in the
    row's `budget`; a continuation starts a new one. Raises RestoreError
    when the run can't be rebuilt (no plan, baseline gone)."""
    from engine.models.coilset import CoilSet
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    record = get_ai_run(ai_run_id)
    if record is None:
        raise RestoreError("this run no longer exists")
    if not record.get("spec"):
        raise RestoreError("it has no search plan yet")
    try:
        spec = spec_from_dict(record["spec"])
    except Exception as exc:
        raise RestoreError(f"its stored plan can't be read by this version ({exc})") from None
    baseline = _baseline(record)

    state = AgentRunState(id=ai_run_id, spec=spec, baseline_coilset=baseline,
                          status=AgentRunStatus(record["status"]), stop_reason=record.get("stop_reason"))
    state.outcome_report = record.get("error")
    state.budget_history = list(record.get("budget") or [])
    if record.get("finished_at"):
        state.finished_at = datetime.fromisoformat(record["finished_at"])
    state.baseline_metrics = wait_for_jobs(evaluate_async(
        baseline, spec.distances, job_runner, config_id=spec.baseline_config_id, config_name=baseline.config_name))
    table = search_field_table(spec.settings)  # the run's own copy of the Search fields table
    allowed = allowed_fields(baseline, spec.variable_spec, table)
    candidate_fields = {f.name for f in dataclasses.fields(CandidateResult)}

    for message in sorted(list_messages(ai_run_id, phase="optimize"), key=lambda m: m["round_num"]):
        payload = message["payload"] or {}
        round_state = RoundState(
            round_num=message["round_num"], status=RoundStatus.DONE, reasoning_text=message["content"] or "",
            search_attempt=payload.get("search_attempt") or 1, attempt_round=payload.get("attempt_round") or 1,
            llm_failures=payload.get("llm_failures") or [], llm_stats=payload.get("llm_stats") or {},
            segment=payload.get("segment") or 1, no_reply=bool(payload.get("no_reply")),
            verdict=payload.get("verdict"), rounds_since_improvement=payload.get("rounds_since_improvement"))
        for stored in payload.get("candidates") or []:
            candidate = CandidateResult(**{k: v for k, v in stored.items() if k in candidate_fields})
            round_state.candidates.append(candidate)
            if not candidate.valid:
                # A proposal turned away unseen (over a limit) was never part of the memory.
                never_evaluated = (candidate.error_code in NEVER_EVALUATED
                                   or (candidate.error or "").startswith("not simulated"))  # rows from before the codes
                if not never_evaluated:
                    state.tried.append(Tried(round_state.round_num, candidate.delta, None,
                                             candidate.rejection or {"valid": False, "error": candidate.error or ""},
                                             round_state.segment))
                continue
            coilset = CoilSet(**candidate.candidate_coilset)
            scored = score(coilset, wait_for_jobs(evaluate_async(coilset, spec.distances, job_runner)),
                           baseline, state.baseline_metrics, spec)
            scored.changed_fields = audit_changed_fields(baseline, coilset, allowed)["changed_fields"]
            candidate.tool_result = tool_result(scored, spec)
            state.tried.append(Tried(round_state.round_num, candidate.delta, scored, candidate.tool_result,
                                     round_state.segment))
        state.rounds.append(round_state)

    # The fragility checks already made: a design is never checked twice, and
    # the winner is the best design that survived one.
    for check in record.get("fragility") or []:
        fragility.record_check(state, check)
    survivors = [t.scored for t in state.tried if t.check is not None and t.check["passed"]]
    if survivors:
        state.winner = min(survivors, key=lambda s: rank_key(s, spec.favourite_metric))
    fragility.refresh_best(state)
    state.unchecked_pass = state.winner is None and bool(fragility.unchecked_passing(state))

    state.rounds_persisted = len(state.rounds)
    state.segment = max([r.segment for r in state.rounds] + [len(state.budget_history), 1])
    state.segment_start_round = max([r.round_num for r in state.rounds if r.segment < state.segment] + [0])
    state.search_attempt = state.rounds[-1].search_attempt if state.rounds else 1
    if state.rounds:
        state.progress = progress.assess(
            state.tried, [r.round_num for r in state.rounds if not r.no_reply], spec,
            free_fields_for(baseline, spec.variable_spec, table), state.segment_start_round)
    return state
