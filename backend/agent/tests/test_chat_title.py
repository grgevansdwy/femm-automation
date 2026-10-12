# =============================================================================
# test_chat_title.py
#
# Checks agent/services/chat_title.py with a fake LLM (no server): what counts
# as a usable title, that short text is never sent, and that ChatTitler stores
# a title once and gives up quietly when the server fails.
#
# Run from the repo root:
#   uv run --package frontend --group test pytest backend/agent/tests/test_chat_title.py
# =============================================================================
import threading
import time

from agent.repositories import ai_run_repository as ar
from agent.services import chat_title
from agent.services.llm_client import LLMError

LONG_GOAL = "Reduce tank ESR by at least 10% compared with the Gen2_coil baseline, evaluating at 5 mm and 10 mm."


def _wait_idle(titler, timeout=5.0):
    deadline = time.monotonic() + timeout
    while titler._waiting and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not titler._waiting, "the titler did not finish"


def test_a_reply_is_cut_down_to_one_short_plain_line():
    assert chat_title.clean_title('\n "Cut tank ESR 10%."\nBecause the user asked...') == "Cut tank ESR 10%"
    assert chat_title.clean_title("Title: **Hold all M within 5%**") == "Hold all M within 5%"
    long = chat_title.clean_title("Reduce tank ESR by at least ten percent compared with the baseline coil")
    assert long == "Reduce tank ESR by at least ten percent" and len(long) <= chat_title.TITLE_MAX_CHARS
    assert len(chat_title.clean_title("x" * 200)) == chat_title.TITLE_MAX_CHARS, "one long word is still cut"
    assert chat_title.clean_title("") is None and chat_title.clean_title(None) is None
    assert chat_title.clean_title(' "" \n') is None
    assert not chat_title.needs_title("cut   tank\nESR") and chat_title.needs_title(LONG_GOAL)
    print("  ok  clean_title() keeps one short plain line, or nothing")


def test_the_titler_stores_one_title_per_long_chat_and_asks_once(monkeypatch):
    calls, release = [], threading.Event()

    def fake_llm(messages, tools, **kwargs):
        calls.append((messages, tools, kwargs))
        release.wait(5)
        return {"role": "assistant", "content": '"Cut tank ESR 10%"'}

    monkeypatch.setattr(chat_title, "call_llm", fake_llm)
    run_id = ar.create_ai_run("base_coil")["id"]
    titler = chat_title.ChatTitler()

    assert not titler.request(run_id, "cut tank ESR"), "short text is its own title: no request"
    assert titler.request(run_id, LONG_GOAL)
    assert not titler.request(run_id, LONG_GOAL), "already waiting"
    assert not titler.idle
    release.set()
    _wait_idle(titler)
    assert titler.idle

    assert ar.get_ai_run(run_id)["title"] == "Cut tank ESR 10%"
    (messages, tools, kwargs), = calls
    assert tools is None and messages[-1] == {"role": "user", "content": LONG_GOAL}
    assert kwargs["max_tokens"] == chat_title._REPLY_MAX_TOKENS
    print("  ok  a long goal is titled once, in the background")


def test_a_failed_title_leaves_the_chat_untitled_and_is_not_asked_again_at_once(monkeypatch):
    calls = []

    def failing_llm(messages, tools, **kwargs):
        calls.append(1)
        raise LLMError("unreachable", "no route", elapsed_s=0.0)

    monkeypatch.setattr(chat_title, "call_llm", failing_llm)
    run_id = ar.create_ai_run("base_coil")["id"]
    titler = chat_title.ChatTitler()
    assert titler.request(run_id, LONG_GOAL)
    _wait_idle(titler)
    assert ar.get_ai_run(run_id)["title"] is None
    assert not titler.request(run_id, LONG_GOAL), "not again until the retry delay has passed"

    for _ in range(chat_title._MAX_TRIES - 1):
        titler._retry_at.clear()  # as if the delay had passed
        assert titler.request(run_id, LONG_GOAL)
        _wait_idle(titler)
    titler._retry_at.clear()
    assert not titler.request(run_id, LONG_GOAL), "and never more than _MAX_TRIES times"
    assert len(calls) == chat_title._MAX_TRIES and ar.get_ai_run(run_id)["title"] is None
    print("  ok  a title that can't be made is retried a few times, then left")
