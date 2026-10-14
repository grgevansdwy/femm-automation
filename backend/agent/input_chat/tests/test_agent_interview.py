# =============================================================================
# test_agent_interview.py
#
# Checks run_interview_turn()'s outcomes with a scripted fake LLM (no
# server): the model asks its own question (no tool call), finalize_spec
# succeeds, finalize_spec is rejected (must trigger exactly one extra call,
# with NO tools offered, so that call can only end in a question for the
# user, never a silent second finalize_spec attempt), and flag_off_topic
# (must never reach _build_spec at all, and must never trigger the
# rejection follow-up - it isn't a rejection).
#     python3 -m pytest agent/input_chat/tests/test_agent_interview.py  (run from backend/)
# =============================================================================
import json

from agent.input_chat.services import interview
from agent.input_chat.services.interview import InterviewState, run_interview_turn


def _finalize(objectives, distances=(5.0,), **extra):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "finalize_spec", "arguments": {
            "objective_text": "test", "distances": list(distances), "objectives": objectives, **extra,
        }}}]}


def _off_topic(reason="asked about something unrelated"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "flag_off_topic", "arguments": {"reason": reason}}}]}


def _text(content):
    return {"role": "assistant", "content": content}


def _run(monkeypatch, script):
    """script: one entry per AI request, in order. Returns (state, every call's
    (messages, tools) pair) so a test can inspect exactly what each request sent."""
    calls = []

    def fake_call_llm(messages, tools, **kwargs):
        calls.append((messages, tools))
        return script[len(calls) - 1]

    monkeypatch.setattr(interview, "call_llm", fake_call_llm)
    state = InterviewState(baseline_config_id="base_coil")
    run_interview_turn(state, "match L_loop within 5%")
    return state, calls


def test_a_plain_question_makes_one_call_and_finalizes_nothing(monkeypatch):
    state, calls = _run(monkeypatch, [_text("Which distances would you like?")])

    assert len(calls) == 1, "the model just asked a question - no reason to call again"
    assert not state.finalized and state.spec is None
    assert state.transcript[-1]["content"] == "Which distances would you like?"
    print("  ok  a plain clarifying question makes exactly one call")


def test_a_valid_finalize_spec_makes_one_call(monkeypatch):
    good = _finalize([{"metric": "L_loop", "kind": "reduce", "tolerance_pct": 5.0, "required": True}])
    state, calls = _run(monkeypatch, [good])

    assert len(calls) == 1, "success needs no follow-up call"
    assert state.finalized and state.spec is not None
    print("  ok  a valid finalize_spec call finalizes on the first try, no extra call")


def test_a_rejected_finalize_spec_triggers_exactly_one_toolless_followup(monkeypatch):
    # Every objective is a preference (required=False) - _build_spec rejects
    # this ("objectives needs at least one required entry").
    bad = _finalize([{"metric": "L_loop", "kind": "reduce", "tolerance_pct": 5.0, "required": False}])
    followup_question = "Should matching L_loop actually be required to pass, or just a preference?"
    state, calls = _run(monkeypatch, [bad, _text(followup_question)])

    assert len(calls) == 2, "a rejection must trigger exactly one extra call"
    assert not state.finalized and state.spec is None

    (_, first_tools), (second_messages, second_tools) = calls
    assert first_tools, "the first call offers finalize_spec"
    assert not second_tools, "the follow-up call must offer NO tools - a reply can only be plain text"

    # The follow-up call must actually see the rejection reason, not just the
    # original request - otherwise it can't know what to ask about.
    sent_texts = [m.get("content", "") for m in second_messages]
    assert any("required entry" in t for t in sent_texts), "the follow-up call must see the specific error"

    assert state.transcript[-1]["content"] == followup_question, "the user sees the question, not the raw error"
    print("  ok  a rejected finalize_spec makes one toolless follow-up call and asks the user")


def test_the_followup_reply_is_never_finalized_even_if_it_looks_like_a_tool_call(monkeypatch):
    # A defensive check: even if a fake/misbehaving server ignored tools=None
    # and returned tool_calls anyway, run_interview_turn must not act on it -
    # it only ever reads .get("content") for this reply, never .get("tool_calls").
    bad = _finalize([{"metric": "L_loop", "kind": "reduce", "tolerance_pct": 5.0, "required": False}])
    rogue_reply = _finalize([{"metric": "L_loop", "kind": "reduce", "tolerance_pct": 5.0, "required": True}])
    state, calls = _run(monkeypatch, [bad, rogue_reply])

    assert len(calls) == 2
    assert not state.finalized and state.spec is None, "the follow-up reply must never be treated as a finalize attempt"
    print("  ok  the follow-up call's reply is never acted on as a tool call, even if one is present")


def test_both_tools_are_offered_on_a_normal_turn(monkeypatch):
    state, calls = _run(monkeypatch, [_text("Which distances?")])

    (_, tools), = calls
    names = {t["function"]["name"] for t in tools}
    assert names == {"finalize_spec", "flag_off_topic", "propose_search_fields_change"}, \
        "every tool must be offered every normal turn"
    print("  ok  finalize_spec and flag_off_topic are both offered on a normal turn")


def test_flag_off_topic_triggers_one_toolless_followup_and_never_touches_build_spec(monkeypatch):
    # A malformed "objectives" (not even a list) would make _build_spec blow
    # up with a TypeError if it were ever reached - it must not be reached.
    # The follow-up mirrors a rejection's: one more call, no tools offered,
    # so the model can naturally pick back up (e.g. re-ask a pending
    # question) instead of the fixed redirect line looking like a restart.
    followup_question = "Which separation distances should I evaluate at?"
    state, calls = _run(monkeypatch, [_off_topic(), _text(followup_question)])

    assert len(calls) == 2, "flag_off_topic must trigger exactly one follow-up call, same as a rejection"
    assert not state.finalized and state.spec is None

    (_, first_tools), (_, second_tools) = calls
    assert first_tools, "the first call offers both tools"
    assert not second_tools, "the follow-up call must offer NO tools"

    outcome = json.loads(state.transcript[-2]["content"])
    assert outcome == {"valid": False, "off_topic": True}, "off_topic must be a distinct, code-recognisable outcome"
    assert state.transcript[-1]["content"] == followup_question, "the follow-up reply is what the user sees next"
    print("  ok  flag_off_topic triggers one toolless follow-up call and never reaches _build_spec")


def test_a_flag_off_topic_with_no_reason_is_ignored_and_the_reply_text_kept(monkeypatch):
    # Seen live: an on-topic clarifying question, plus flag_off_topic(reason="n/a").
    question = "Do you mean at least 10% lower than baseline?"
    stray = {**_off_topic(reason="n/a"), "content": question}
    state, calls = _run(monkeypatch, [stray])
    assert len(calls) == 1 and state.transcript[-1] == {"role": "assistant", "content": question}

    # With no text either, the model is asked again without tools.
    state, calls = _run(monkeypatch, [_off_topic(reason=""), _text(question)])
    assert len(calls) == 2 and calls[1][1] is None
    assert [m["role"] for m in state.transcript] == ["user", "assistant"] and state.transcript[-1]["content"] == question


def test_flag_off_topics_tool_result_carries_no_explanation_text(monkeypatch):
    # The raw transcript DOES still carry the model's own tool-call arguments
    # (including its stated reason) - that's normal, needed conversation
    # history for the model itself, not a leak. What must stay minimal is
    # the code-authored tool RESULT interview.py appends: exactly
    # {"valid": False, "off_topic": True}, nothing from the model's reason
    # folded in - the frontend's display layer (tested separately, a
    # different package) supplies fixed user-facing text and never reads
    # this outcome's fields beyond checking the tool name itself.
    state, calls = _run(monkeypatch, [_off_topic(reason="asked me to write a poem"), _text("continuing on")])

    tool_result_msg = state.transcript[-2]  # the follow-up reply is now last
    assert tool_result_msg["role"] == "tool"
    assert json.loads(tool_result_msg["content"]) == {"valid": False, "off_topic": True}
    print("  ok  the tool result interview.py appends carries no explanation text, just the flag")


_TWO_CUTS = [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 10.0},
             {"metric": "rc_esr", "kind": "reduce", "tolerance_pct": 10.0},
             {"metric": "M23", "kind": "match", "tolerance_pct": 5.0}]


def test_a_favourite_must_be_a_required_reduce_or_increase_objective(monkeypatch):
    state, _ = _run(monkeypatch, [_finalize(_TWO_CUTS, favourite_metric="tank_esr")])
    assert state.finalized and state.spec.favourite_metric == "tank_esr"

    state, _ = _run(monkeypatch, [_finalize(_TWO_CUTS)])
    assert state.finalized and state.spec.favourite_metric is None, "no favourite = the blended score"

    for bad in ("M23", "loop_esr"):  # a match objective; not in the plan at all
        state, calls = _run(monkeypatch, [_finalize(_TWO_CUTS, favourite_metric=bad), _text("Which one?")])
        assert not state.finalized and len(calls) == 2
        assert "favourite_metric" in json.loads(state.transcript[-2]["content"])["error"]
    print("  ok  favourite_metric is checked by code: only a required reduce/increase objective of this plan")


def test_malformed_tool_arguments_end_in_a_question_not_a_dead_turn(monkeypatch):
    broken = [
        _finalize(_TWO_CUTS, fixed_values=[1, 2]),  # a list where an object belongs: TypeError inside _build_spec
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "finalize_spec",
                                                                                      "arguments": "not json"}}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},  # no function at all
        _finalize([{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": "lots"}]),
    ]
    for msg in broken:
        state, calls = _run(monkeypatch, [msg, _text("Could you say that again?")])
        assert not state.finalized and state.error is None
        assert len(calls) == 2 and calls[1][1] is None, "one tool-less follow-up turns the rejection into a question"
        assert json.loads(state.transcript[-2]["content"])["valid"] is False
        assert state.transcript[-1]["content"] == "Could you say that again?"
    print("  ok  whatever shape finalize_spec's arguments have, the user gets a reply")


# ---- the Field Config table: the model may propose a change, never apply one -------------------

def _propose_fields(changes, reason="you asked to tune the tank ferrite"):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "propose_search_fields_change", "arguments": {
            "changes": changes, "reason": reason}}}]}


def test_proposing_a_search_fields_change_shows_it_and_writes_nothing(monkeypatch):
    from agent.repositories import agent_settings_repository as repository
    from agent.settings.settings import AgentSettings, load_settings

    state, calls = _run(monkeypatch, [_propose_fields([{"field": "tank_mu", "enabled": True, "max": 2000}]),
                                      _text("I've proposed opening tank_mu - click Apply if that's right.")])

    assert state.pending_field_change == {"changes": [{"field": "tank_mu", "enabled": True, "max": 2000}],
                                          "reason": "you asked to tune the tank ferrite"}
    assert repository.load_overrides() == {} and load_settings() == AgentSettings(), "nothing is saved"
    assert state.settings.search_fields == {}, "this conversation's own table is untouched too"
    assert not state.finalized and state.spec is None
    assert len(calls) == 2 and calls[1][1] is None, "the reply after a proposal is offered no tools"
    assert json.loads(state.transcript[-2]["content"])["valid"] is True


def test_a_malformed_search_fields_proposal_is_rejected_and_never_shown(monkeypatch):
    for bad in ([{"field": "not_a_field", "enabled": True}], [], [{"field": "tank_mu", "min": "low"}], "tank_mu"):
        state, _ = _run(monkeypatch, [_propose_fields(bad), _text("Which field did you mean?")])
        assert state.pending_field_change is None, bad
        assert json.loads(state.transcript[-2]["content"])["valid"] is False


def test_nothing_the_model_sends_can_open_a_field_or_widen_a_limit(monkeypatch):
    """Injection (P9): a finalize_spec that claims a closed field is open, by
    the old enabled_groups argument or any made-up one, changes nothing - the
    table alone decides, and an objective on a closed field is refused."""
    from agent.input_chat.models.variables import free_fields_for, search_field_table

    objectives = [{"metric": "L_loop", "kind": "match", "tolerance_pct": 5}]
    state, _ = _run(monkeypatch, [_finalize(objectives, enabled_groups=["tank_ferrite"],
                                            search_fields={"tank_mu": {"enabled": True}},
                                            enabled_fields=["tank_mu"])])
    assert state.finalized
    assert state.spec.variable_spec.enabled_groups == set()
    assert state.spec.settings.search_fields == {}
    baseline = interview_baseline()
    free = free_fields_for(baseline, state.spec.variable_spec, search_field_table(state.spec.settings))
    assert "tank_mu" not in free

    state, _ = _run(monkeypatch, [_finalize([{"metric": "tank_mu", "kind": "increase", "tolerance_pct": 5}]),
                                  _text("tank_mu is closed - want me to propose opening it?")])
    assert not state.finalized, "an objective on a closed field cannot be planned"

    state, _ = _run(monkeypatch, [_finalize(objectives, fixed_values={"num_tank_layers": 12}),
                                  _text("12 layers is above the limit of 8.")])
    assert not state.finalized, "a pin cannot go past a limit"
    assert "1 to 8" in json.loads(state.transcript[-2]["content"])["error"]


def interview_baseline():
    from engine.repositories.coilset_repository import get_coilset
    return get_coilset("base_coil")


def test_the_interview_prompt_lists_open_fields_with_limits_and_names_the_closed_ones():
    from agent.settings.settings import AgentSettings
    baseline = interview_baseline()
    prompt = interview.interview_system_prompt("base_coil", baseline)
    assert "num_tank_layers: Number of Tank Layers, baseline 1 - allowed: 1 to 8" in prompt
    assert "must be one of [0.035, 0.07, 0.14]" in prompt
    closed_line = next(line for line in prompt.splitlines() if line.startswith("Every other field is closed:"))
    assert "tank_mu" in closed_line and "rc_ferrite_y_offset" in closed_line and "loop_r" not in closed_line
    assert "enabled_groups" not in prompt and "{{" not in prompt

    opened = AgentSettings(search_fields={"tank_mu": {"enabled": True, "min": None, "max": 2000}})
    prompt = interview.interview_system_prompt("base_coil", baseline, opened)
    assert f"tank_mu: Tank Ferrite Permeabilitiy, baseline {baseline.tank_mu:g} - allowed: 0 to 2000" in prompt


def test_finalize_spec_no_longer_offers_enabled_groups():
    properties = interview.FINALIZE_SPEC_TOOL[0]["function"]["parameters"]["properties"]
    assert "enabled_groups" not in properties


def test_the_first_message_is_saved_when_it_is_sent_not_when_the_reply_lands(monkeypatch):
    import threading
    import time

    from agent.input_chat.services.runner import InterviewRunner
    from agent.repositories.ai_run_repository import list_chat_rows, list_messages

    release = threading.Event()

    def slow_llm(messages, tools, **kwargs):
        release.wait(5)
        return _text("Which distances would you like?")

    monkeypatch.setattr(interview, "call_llm", slow_llm)
    runner = InterviewRunner()
    state = runner.start("base_coil")
    assert list_chat_rows() == [], "nothing is saved before the first message"

    runner.send(state, "cut tank ESR")
    assert runner.busy, "the reply is still on its way"
    (row,) = list_chat_rows()
    assert row["id"] == runner.ai_run_id and row["first_message"] == "cut tank ESR", "listed by its text at once"

    release.set()
    deadline = time.monotonic() + 5
    while runner.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    stored = list_messages(runner.ai_run_id, phase="interview")
    assert [(m["seq"], m["role"], m["content"]) for m in stored] == [
        (0, "user", "cut tank ESR"), (1, "assistant", "Which distances would you like?")], "each message saved once"
    assert [m["role"] for m in state.transcript] == ["user", "assistant"]
    print("  ok  a chat's first message is saved the moment it is sent")
