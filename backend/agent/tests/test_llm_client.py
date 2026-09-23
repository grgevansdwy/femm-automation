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

from agent.services import llm_client
from agent.services.llm_client import LLMError, call_llm
from agent.settings.settings import AgentSettings


def _chunk(delta=None, finish=None, **extra):
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], **extra}


class _FakeServer:
    """Serves one scripted behaviour per test. `behaviour(handler)` writes
    the response; `requests` records each request body, `disconnects` counts
    writes that failed because the client had gone away."""

    def __init__(self, behaviour):
        server = self
        self.requests, self.disconnects = [], 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
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


def test_settings_choose_model_and_limits_and_sampling_is_left_to_the_server(serve):
    server = serve(lambda h: h.sse([_chunk({"content": "OK"}, finish="stop")]))
    settings = AgentSettings(api_base_url=server.url, model="other-model", llm_max_tokens=123)
    call_llm([{"role": "user", "content": "hi"}], None, settings=settings)
    body = server.requests[0]
    assert (body["model"], body["max_tokens"]) == ("other-model", 123)
    assert "temperature" not in body and "top_p" not in body, "sampling is never sent - the server's own defaults apply"


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
