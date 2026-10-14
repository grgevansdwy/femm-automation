# =============================================================================
# test_llm_client.py
#
# Checks agent/services/llm_client.py against a local fake OpenAI-style streaming
# server - no real LLM: a normal streamed reply (reasoning, content, tool
# call arguments split across chunks), and every failure kind with the reason
# it reports. The time limit must hold even when the server sends nothing,
# and hitting it must close the connection (which is what stops generation
# on the real llama.cpp server).
#     python3 -m pytest agent/tests/test_llm_client.py  (run from backend/)
# =============================================================================
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent import report
from agent.femm_agent.models.schema import build_tools
from agent.services import llm_client, run_log
from agent.services.llm_client import LLMError, call_llm
from agent.services.run_log import RunLog, read_log
from agent.settings.settings import AgentSettings


def _chunk(delta=None, finish=None, **extra):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


class _FakeServer:
    """Serves one scripted behaviour per test. `behaviour(handler)` writes
    the response; `requests` records each request body (`raw_requests` its exact bytes), `disconnects` counts
    writes that failed because the client had gone away."""

    def __init__(self, behaviour):
        server = self
        self.requests, self.raw_requests, self.disconnects = [], [], 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                server.raw_requests.append(raw)
                server.requests.append(json.loads(raw))
                try:
                    behaviour(self)
                except (BrokenPipeError, ConnectionResetError):
                    server.disconnects += 1

            def sse(self, chunks, pause=0.0):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in chunks:
                    self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(pause)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def serve(monkeypatch):
    servers = []

    def start(behaviour):
        s = _FakeServer(behaviour)
        servers.append(s)
        monkeypatch.setattr(llm_client, "load_settings", lambda: AgentSettings(api_base_url=s.url))
        return s

    yield start
    for s in servers:
        s.close()


def test_on_update_streams_reasoning_and_content_live_before_the_call_returns(serve):
    """on_update must fire as each chunk arrives, with the growing text so
    far - not just once at the end - so a caller can mirror it onto shared
    state a GUI polls (agent/femm_agent/services/optimize.py's round_state.reasoning_text,
    agent/input_chat/services/interview.py's InterviewState.live_reasoning)."""
    serve(lambda h: h.sse([
        _chunk({"reasoning_content": "Let me "}),
        _chunk({"reasoning_content": "think "}),
        _chunk({"content": "he"}),
        _chunk({"content": "llo"}, finish="stop"),
    ]))
    seen = []
    msg = call_llm([{"role": "user", "content": "hi"}], None,
                   on_update=lambda content, reasoning: seen.append((content, reasoning)))

    assert seen == [
        ("", "Let me "), ("", "Let me think "), ("he", "Let me think "), ("hello", "Let me think "),
    ], "each call carries the full text so far, growing incrementally"
    assert seen[-1] == (msg["content"], msg["reasoning_content"]), "the last update matches the final message"
    print("  ok  on_update streams the growing content/reasoning live, before call_llm returns")


def test_a_broken_on_update_does_not_break_the_request(serve):
    """A bug in the caller's callback (e.g. a GUI redraw crash) must never
    take down the request itself - the reply still comes back normally."""
    serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))

    def bad_update(content, reasoning):
        raise ValueError("boom")

    msg = call_llm([{"role": "user", "content": "hi"}], None, on_update=bad_update)
    assert msg["content"] == "OK"
    print("  ok  an on_update callback that raises doesn't break the request")


def test_streamed_reply_is_assembled_with_tool_arguments_decoded(serve):
    server = serve(lambda h: h.sse([
        _chunk({"role": "assistant", "content": None}),
        _chunk({"reasoning_content": "Let me "}), _chunk({"reasoning_content": "think."}),
        _chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                "function": {"name": "propose_coilset", "arguments": "{\"tank_center_r\": "}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": "70.0}"}}]}),
        _chunk({"tool_calls": [{"index": 1, "id": "c2", "type": "function",
                                "function": {"name": "propose_coilset", "arguments": "{}"}}]}),
        _chunk({}, finish="tool_calls", timings={"predicted_n": 42}),
    ]))
    stats = {}
    msg = call_llm([{"role": "user", "content": "go"}], [{"type": "function"}], stats=stats)

    assert msg["reasoning_content"] == "Let me think."
    assert [tc["id"] for tc in msg["tool_calls"]] == ["c1", "c2"]
    assert msg["tool_calls"][0]["function"]["arguments"] == {"tank_center_r": 70.0}
    assert stats["tokens"] == 42 and stats["elapsed_s"] < 5
    body = server.requests[0]
    assert body["stream"] is True and body["max_tokens"] == AgentSettings().llm_max_tokens == 32000
    assert body["tools"] == [{"type": "function"}]
    print("  ok  a streamed reply (reasoning, content, split tool arguments) is assembled")


def test_settings_choose_model_limits_and_sampling_which_is_always_sent(serve):
    server = serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    settings = AgentSettings(api_base_url=server.url, model="other-model", llm_max_tokens=123,
                             temperature=0.4, top_p=0.8, seed=7)
    call_llm([{"role": "user", "content": "hi"}], None, settings=settings)
    body = server.requests[0]
    assert (body["model"], body["max_tokens"]) == ("other-model", 123)
    assert (body["temperature"], body["top_p"], body["seed"]) == (0.4, 0.8, 7)

    call_llm([{"role": "user", "content": "hi"}], None, settings=AgentSettings(api_base_url=server.url))
    body = server.requests[1]
    assert (body["temperature"], body["top_p"], body["seed"]) == (1.0, 0.95, -1), \
        "the defaults are sent too - sampling is never left to whatever the server defaults to"
    assert body["stream_options"] == {"include_usage": True}


def test_no_tools_omits_the_key(serve):
    server = serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    assert call_llm([{"role": "user", "content": "hi"}], None)["content"] == "OK"
    assert "tools" not in server.requests[0]


def test_silent_server_hits_the_hard_time_limit(serve):
    """The server accepts the request and never sends a byte - the case a
    socket timeout can't bound. The limit must still hold, and the
    connection must be closed."""
    release = threading.Event()

    def silent(h):
        release.wait(10)
        h.sse([_chunk({"content": "too late"}, finish="stop")])

    server = serve(silent)
    t = time.monotonic()
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, time_limit_s=1.0)
    took = time.monotonic() - t
    release.set()
    assert err.value.kind == "no_response" and err.value.tokens == 0
    assert 1.0 <= took < 2.0, took
    assert "sent nothing back within 1 s" in str(err.value)
    time.sleep(0.5)
    assert server.disconnects == 1, "the late reply found the connection closed"
    print("  ok  a silent server is cut off at the time limit and reported as no_response")


def test_slow_generation_is_too_slow_and_the_connection_is_closed(serve):
    server = serve(lambda h: h.sse([_chunk({"reasoning_content": "hmm "})] * 1000, pause=0.1))
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, time_limit_s=1.0)
    assert err.value.kind == "too_slow" and err.value.tokens > 3
    assert "still generating" in str(err.value)
    deadline = time.monotonic() + 3
    while server.disconnects == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.disconnects == 1, "the server stops streaming once the client is gone"
    print("  ok  tokens still arriving at the limit -> too_slow, connection closed")


def test_thinking_past_the_thinking_limit_is_stopped_and_handed_back(serve):
    server = serve(lambda h: h.sse([_chunk({"reasoning_content": "hmm "})] * 1000, pause=0.05))
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, time_limit_s=30.0, thinking_limit_s=0.5)
    assert err.value.kind == "thinking_limit" and err.value.elapsed_s < 3
    assert err.value.reasoning.startswith("hmm hmm ") and "still thinking after" in str(err.value)
    deadline = time.monotonic() + 3
    while server.disconnects == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.disconnects == 1, "the server stops generating once the client is gone"


def test_a_reply_that_has_started_its_answer_is_not_stopped_by_the_thinking_limit(serve):
    serve(lambda h: h.sse([_chunk({"reasoning_content": "hmm "})] + [_chunk({"content": "a"})] * 12
                          + [_chunk({}, finish="stop")], pause=0.1))
    msg = call_llm([{"role": "user", "content": "hi"}], None, time_limit_s=30.0, thinking_limit_s=0.5)
    assert msg["content"] == "a" * 12


def test_thinking_off_is_sent_as_a_template_switch_with_a_required_tool_call(serve):
    server = serve(lambda h: h.sse([_chunk({"content": "ok"}), _chunk({}, finish="stop")]))
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}]
    call_llm([{"role": "user", "content": "hi"}], tools)
    call_llm([{"role": "user", "content": "hi"}], tools, thinking=False)
    plain, off = server.requests
    assert "chat_template_kwargs" not in plain and "tool_choice" not in plain
    assert off["chat_template_kwargs"] == {"enable_thinking": False} and off["tool_choice"] == "required"


def test_hitting_max_tokens_is_length_limit(serve):
    serve(lambda h: h.sse([_chunk({"reasoning_content": "loop "}), _chunk({}, finish="length")]))
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, max_tokens=100)
    assert err.value.kind == "length_limit" and "100 token limit" in str(err.value)


def test_http_error_and_error_events(serve):
    def fail(h):
        h.send_response(500)
        h.send_header("Content-Length", "13")
        h.end_headers()
        h.wfile.write(b"model crashed")

    serve(fail)
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None)
    assert err.value.kind == "http_error" and "HTTP 500: model crashed" in str(err.value)

    serve(lambda h: h.sse([{"error": {"message": "context size exceeded"}}]))
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None)
    assert err.value.kind == "http_error" and "context size exceeded" in str(err.value)


def test_unreachable_server(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again before the request - nothing listens here
    settings = AgentSettings(api_base_url=f"http://127.0.0.1:{port}")
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, settings=settings, time_limit_s=5)
    assert err.value.kind == "unreachable" and f"127.0.0.1:{port}" in str(err.value)


def test_should_abort_stops_quickly(serve):
    serve(lambda h: h.sse([_chunk({"reasoning_content": "x"})] * 1000, pause=0.1))
    started = time.monotonic()
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], None, should_abort=lambda: time.monotonic() - started > 0.5)
    assert err.value.kind == "aborted" and time.monotonic() - started < 1.5


def test_broken_tool_arguments_are_bad_reply(serve):
    serve(lambda h: h.sse([
        _chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "propose_coilset", "arguments": "{oops"}}]}),
        _chunk({}, finish="tool_calls"),
    ]))
    with pytest.raises(LLMError) as err:
        call_llm([{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert err.value.kind == "bad_reply" and "invalid JSON arguments" in str(err.value)


# --- the run log (agent/services/run_log.py) ---------------------------------

_MESSAGES = [{"role": "system", "content": "You design coils."}, {"role": "user", "content": "Start proposing."}]


def test_a_logged_request_holds_exactly_what_was_sent_and_what_came_back(serve):
    server = serve(lambda h: h.sse([
        _chunk({"reasoning_content": "thinking"}),
        _chunk({"content": "OK"}, finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 41, "completion_tokens": 9}},
    ]))
    log, stats = RunLog("run1"), {}
    reply = call_llm(_MESSAGES, None, log=log.bind(phase="search", round=3, search_attempt=1, ai_try=2), stats=stats)

    (entry,) = read_log(log.path)
    assert entry["event"] == "llm_call" and entry["run_id"] == "run1"
    assert (entry["phase"], entry["round"], entry["search_attempt"], entry["ai_try"]) == ("search", 3, 1, 2)
    assert entry["messages"] == server.requests[0]["messages"] == _MESSAGES
    assert entry["reply"] == reply and entry["reply"]["reasoning_content"] == "thinking"
    assert (entry["base_url"], entry["model"]) == (server.url, AgentSettings().model)
    assert entry["sampling"] == {"temperature": 1.0, "top_p": 0.95, "seed": -1}
    assert entry["prompt_sha256"] == llm_client.sha256_text("You design coils.") and entry["tools_sha256"] is None
    assert (entry["prompt_tokens"], entry["reply_tokens"]) == (41, 9) and stats["prompt_tokens"] == 41
    assert entry["chars_in"] == len(server.raw_requests[0]) and entry["chars_out"] == len("thinkingOK")
    assert "error" not in entry and entry["elapsed_s"] >= 0


def test_prompt_tokens_are_null_when_the_server_sends_no_usage(serve):
    serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    log = RunLog("run1")
    call_llm(_MESSAGES, None, log=log)
    assert read_log(log.path)[0]["prompt_tokens"] is None


def test_llama_cpp_timings_give_the_prompt_tokens_when_there_is_no_usage_block(serve):
    serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop",
                                  timings={"prompt_n": 5, "cache_n": 30, "predicted_n": 2})]))
    log = RunLog("run1")
    call_llm(_MESSAGES, None, log=log)
    entry = read_log(log.path)[0]
    assert (entry["prompt_tokens"], entry["reply_tokens"]) == (35, 2)


def test_a_failed_request_is_logged_with_its_kind_and_what_had_arrived(serve):
    serve(lambda h: h.sse([_chunk({"reasoning_content": "still thinking"}, finish="length")]))
    log = RunLog("run1")
    with pytest.raises(LLMError):
        call_llm(_MESSAGES, None, log=log)
    (entry,) = read_log(log.path)
    assert entry["error"]["kind"] == "length_limit" and entry["error"]["reasoning"] == "still thinking"
    assert "reply" not in entry and entry["messages"] == _MESSAGES


def test_an_unreachable_server_is_logged_too(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = RunLog("run1")
    with pytest.raises(LLMError):
        call_llm(_MESSAGES, None, settings=AgentSettings(api_base_url=f"http://127.0.0.1:{port}"), time_limit_s=5,
                 log=log)
    assert read_log(log.path)[0]["error"]["kind"] == "unreachable"


def test_a_log_that_cannot_be_written_does_not_fail_the_request(serve, monkeypatch, tmp_path, capsys):
    serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    blocker = tmp_path / "a_file_not_a_folder"
    blocker.write_text("")
    monkeypatch.setattr(run_log, "LOG_DIR", blocker / "logs")
    log = RunLog("run1")
    assert call_llm(_MESSAGES, None, log=log)["content"] == "OK"
    assert call_llm(_MESSAGES, None, log=log)["content"] == "OK"
    assert capsys.readouterr().err.count("could not write") == 1, "reported once, not per request"


def test_a_search_request_can_be_rebuilt_from_the_log_byte_for_byte(serve):
    """The exact request the model saw can be replayed from the log."""
    server = serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    tools = build_tools({"tank_center_r": {"type": "number"}, "num_tank_turns": {"type": "integer"}}, "base_coil")
    settings = AgentSettings(api_base_url=server.url, model="m", llm_max_tokens=777, temperature=0.3, seed=11)
    messages = _MESSAGES + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "propose_coilset", "arguments": {"tank_center_r": 70.5, "rationale": "é shrink"}}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"valid": True, "x": 1e-07})},
    ]
    log = RunLog("run1")
    log.run_start(segment=1, tools=tools)
    for round_num in (1, 2):
        call_llm(messages[:2] if round_num == 1 else messages, tools, settings=settings,
                 log=log.bind(phase="search", round=round_num, search_attempt=1, ai_try=1))

    events = read_log(log.path)
    assert report.rebuild_request(events, 1) == server.raw_requests[0]
    assert report.rebuild_request(events, 2) == server.raw_requests[1]
    with pytest.raises(ValueError, match="no search request for round 9"):
        report.rebuild_request(events, 9)
    events[0]["tools"] = tools[:0]
    with pytest.raises(ValueError, match="tool schema"):
        report.rebuild_request(events, 1)

