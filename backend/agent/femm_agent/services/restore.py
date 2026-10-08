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

from agent.femm_agent.models.state import AgentRunState, AgentRunStatus, CandidateResult, RoundState, RoundStatus
from agent.femm_agent.services import replan
from agent.input_chat.models.spec import spec_from_dict
from agent.input_chat.services.interview import InterviewState


class RestoreError(Exception):
    """The run can't be rebuilt; the message says why in one line."""


def stored_baseline(record):
    """The baseline design a stored run compares against: the copy made at
    Start Search, else its config as it is now. An imported chat only ever
    has the copy its file carried - a local config that happens to share the
    id is some other design. Raises RestoreError."""
    from engine.models.coilset import CoilSet
    from engine.repositories.coilset_repository import get_coilset
    stored = record.get("baseline_coilset")
    try:
        if stored:
            return CoilSet(**stored)
        if record.get("imported_at"):
            raise RestoreError("it was imported from a file that did not carry its baseline design")
        return get_coilset(record["baseline_config_id"])  # a run from before the baseline was copied into it
    except KeyError:
        raise RestoreError(f"its baseline design {record['baseline_config_id']!r} no longer exists") from None
    except TypeError as exc:
        raise RestoreError(f"its stored baseline no longer fits this version's design fields ({exc})") from None


def stored_spec(record: dict):
    """A stored run's plan. An imported chat keeps its goal, distances,
    objectives, pins and exclusions, but runs under THIS machine's settings
    in full - prompts, server, budgets and the Search fields table: what a
    file carries is shown, and never decides what a search here may do."""
    from agent.settings.settings import load_settings
    spec = spec_from_dict(record["spec"])
    if record.get("imported_at"):
        local = load_settings()
        spec = dataclasses.replace(
            spec, settings=local, max_tool_calls=local.max_tool_calls,
            candidates_per_round=local.candidates_per_round, keep_best_candidates=local.keep_best_candidates)
    return spec


def interview_transcript(messages: list[dict]) -> list[dict]:
    """The chat as the model saw it: each stored interview message's payload
    (the message dict InterviewRunner saved), in order."""
    rows = sorted((m for m in messages if m["phase"] == "interview"), key=lambda m: m["seq"])
    return [m["payload"] or {"role": m["role"], "content": m["content"]} for m in rows]


def restore_interview(record: dict, messages: list[dict]) -> InterviewState:
    """The chat side of a stored run, so it can be shown and carried on: the
    transcript, the plan if it has one (shown for confirmation only while the
    run is still 'plan_ready'), and the settings the plan was made under.
    Submits nothing. Raises RestoreError when the stored plan can't be read."""
    from agent.settings.settings import load_settings
    spec = None
    if record.get("spec"):
        try:
            spec = stored_spec(record)
        except Exception as exc:
            raise RestoreError(f"its stored plan can't be read by this version ({exc})") from None
    return InterviewState(
        baseline_config_id=record["baseline_config_id"], transcript=interview_transcript(messages), spec=spec,
        finalized=spec is not None and record["status"] == "plan_ready",
        settings=spec.settings if spec is not None else load_settings())


def restore_run(ai_run_id: str, job_runner) -> AgentRunState:
    """The AgentRunState of a stored run: its plan, every saved round, the
    tried list (the model's memory, design labels included), the best design
    and the progress verdict. The budget is not restored - what each segment spent is in the
    row's `budget`; a continuation starts a new one. Raises RestoreError
    when the run can't be rebuilt (no plan, baseline gone)."""
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    record = get_ai_run(ai_run_id)
    if record is None:
        raise RestoreError("this run no longer exists")
    if not record.get("spec"):
        raise RestoreError("it has no search plan yet")
    try:
        spec = stored_spec(record)
    except Exception as exc:
        raise RestoreError(f"its stored plan can't be read by this version ({exc})") from None
    baseline = stored_baseline(record)

    state = AgentRunState(id=ai_run_id, spec=spec, baseline_coilset=baseline,
                          status=AgentRunStatus(record["status"]), stop_reason=record.get("stop_reason"),
                          imported=bool(record.get("imported_at")))
    state.outcome_report = record.get("error")
    state.budget_history = list(record.get("budget") or [])
    if record.get("finished_at"):
        state.finished_at = datetime.fromisoformat(record["finished_at"])
    state.fragility = list(record.get("fragility") or [])
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
        state.rounds.append(round_state)

    state.rounds_persisted = len(state.rounds)
    state.segment = max([r.segment for r in state.rounds] + [len(state.budget_history), 1])
    state.segment_start_round = max([r.round_num for r in state.rounds if r.segment < state.segment] + [0])
    state.search_attempt = state.rounds[-1].search_attempt if state.rounds else 1

    # Which plan the search was working to, and where its memory begins - written
    # with each segment's budget entry (AgentRunner). A row left 'plan_ready' after
    # a search holds a revised plan the user has not started yet: the state comes
    # back as that plan would start, so what is shown is what Start Search will use.
    last = state.budget_history[-1] if state.budget_history else {}
    state.plan = last.get("plan") or 1
    state.memory_from_round = last.get("memory_from_round") or 0
    if state.status == AgentRunStatus.PLAN_READY and state.budget_history:
        old_distances = last.get("distances")
        mode = replan.memory_mode(old_distances, spec.distances) if old_distances else replan.FRESH
        state.plan += 1
        state.segment_memory = mode
        if mode == replan.FRESH:  # also when the earlier plan's distances are unknown: never solve old designs anew
            state.memory_from_round = len(state.rounds)
    replan.rebuild_memory(state, job_runner)
    return state
