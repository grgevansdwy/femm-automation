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
    assert names == {"finalize_spec", "flag_off_topic"}, "both tools must be offered every normal turn"
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
