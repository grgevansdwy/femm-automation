"""HTTP plumbing to talk to any OpenAI-compatible /v1/chat/completions
server. Nothing here knows what a coil is - just JSON over HTTP.

Normalizes the OpenAI response shape: returns the assistant message, and
decodes tool_calls[].function.arguments from a JSON STRING into a real dict
in place. Confirmed necessary live against the real target server:
arguments comes back as a string, not a dict - decode it here once instead
of scattering json.loads() calls through the loop logic.

Every request is **streamed**, for two reasons found on the office server:
- A hard time limit can only be enforced by closing the connection, and the
  server only stops generating when a *streaming* client disconnects (a
  non-streaming request keeps running on the server after the client gives
  up, slowing whatever runs next).
- A socket timeout only measures silence, so it isn't a real time limit. A
  round-5 request once ran ~63 minutes against a 600 s socket timeout.

So the request runs on a worker thread while the calling thread enforces a
wall-clock limit (settings.llm_time_limit_s, no matter what the server sends), caps the
reply at settings.llm_max_tokens, and can abort early (should_abort, e.g. Restart).
A failure raises LLMError with a `kind` saying *why* - see LLMError.

Sampling (settings.temperature / top_p / seed) is sent explicitly with every
request, so a run's behaviour doesn't silently change with the server's defaults.
With `log` (agent/services/run_log.py's RunLog) every request is also written to
the run's log file in full - what was sent, what came back or why it failed.

A caller can also watch the reply arrive: `on_update(content_so_far,
reasoning_so_far)`, if given, is called from the worker thread every time new
text lands - the model's chain-of-thought often runs for minutes before any
tool call, and "Round 3 [proposing]" or "Thinking..." with no visible content
looked indistinguishable from a hang. Callers write it straight onto shared,
lock-free state (agent/femm_agent/models/state.py's RoundState.reasoning_text,
agent/input_chat/services/interview.py's InterviewState.live_reasoning) - same GIL-atomicity
convention as everywhere else a background thread feeds a polling GUI.
"""

import hashlib
import http.client
import json
import socket
import threading
import time
from urllib.parse import urlsplit

from agent.settings.settings import AgentSettings, load_settings


class LLMError(RuntimeError):
    """A request that didn't produce a usable reply. `kind` is one of:

    - "unreachable":  couldn't connect to the server at all.
    - "http_error":   the server answered with an error (non-200, or an error event).
    - "no_response":  the time limit hit and the server had sent back no tokens
                      (busy with other requests, or still reading the prompt).
    - "too_slow":     the time limit hit while tokens were still arriving.
    - "length_limit": the reply hit max_tokens without finishing (runaway thinking).
    - "bad_reply":    the reply couldn't be parsed (broken stream or tool arguments).
    - "aborted":      should_abort() asked to stop (the user restarted).
    """

    def __init__(self, kind: str, detail: str, *, elapsed_s: float, tokens: int = 0,
                 first_token_s: float | None = None, time_limit_s: float | None = None,
                 max_tokens: int | None = None, base_url: str | None = None) -> None:
        self.kind, self.detail = kind, detail
        self.elapsed_s, self.tokens, self.first_token_s = elapsed_s, tokens, first_token_s
        self.time_limit_s, self.max_tokens = time_limit_s, max_tokens
        self.base_url = base_url
        super().__init__(self.describe())

    def describe(self) -> str:
        """One plain-language sentence saying why the request failed."""
        limit = f"{self.time_limit_s:.0f} s" if self.time_limit_s else "the time limit"
        if self.kind == "unreachable":
            return f"Couldn't connect to the AI server at {self.base_url or 'the configured address'}: {self.detail}"
        if self.kind == "http_error":
            return f"The AI server returned an error: {self.detail}"
        if self.kind == "no_response":
            return (f"The AI server sent nothing back within {limit} - it was likely busy with other "
                    "requests or still reading the prompt.")
        if self.kind == "too_slow":
            rate = f" ({self.tokens / self.elapsed_s:.0f} tokens/s)" if self.tokens and self.elapsed_s else ""
            return (f"The AI was still generating when the {limit} limit hit ({self.tokens} tokens "
                    f"received{rate}) - the server was too slow or the AI thought for too long.")
        if self.kind == "length_limit":
            cap = f"{self.max_tokens:,} token" if self.max_tokens else "token"
            return (f"The AI's reply hit the {cap} limit without finishing (after {self.elapsed_s:.0f} s) - "
                    "it kept thinking without reaching an answer.")
        if self.kind == "bad_reply":
            return f"The AI's reply couldn't be read: {self.detail}"
        if self.kind == "aborted":
            return "The request was cancelled."
        return f"{self.kind}: {self.detail}"

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.describe(), "elapsed_s": round(self.elapsed_s, 1),
                "tokens": self.tokens,
                "first_token_s": None if self.first_token_s is None else round(self.first_token_s, 1),
                # The limits THIS request actually ran under - not whatever
                # agent.config says right now, which could since have changed
                # (env var, or a future per-call override) - a later report
                # (agent/femm_agent/services/outcome.py) must describe what really happened.
                "time_limit_s": self.time_limit_s, "max_tokens": self.max_tokens}


class _Stream:
    """What the worker thread has received so far - read by the calling
    thread when the time limit hits, to tell no_response from too_slow."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.sock: socket.socket | None = None
        self.started = time.monotonic()
        self.first_token_at: float | None = None
        self.tokens = 0
        # Plain strings, grown with += as chunks arrive - not a list rejoined
        # on every on_update call, which would be quadratic over a reply that
        # can run to tens of thousands of tokens.
        self.content: str = ""
        self.reasoning: str = ""
        self.tool_calls: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.server_tokens: int | None = None
        self.prompt_tokens: int | None = None  # None if the server reports neither usage nor timings
        self.error: tuple[str, str] | None = None  # (kind, detail)
        self.closed_by_caller = False

    def close(self) -> None:
        self.closed_by_caller = True
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass


def _connection(url: str, timeout: float) -> http.client.HTTPConnection:
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return cls(parts.hostname, parts.port, timeout=timeout)


def _apply_chunk(stream: _Stream, chunk: dict) -> None:
    if "error" in chunk:
        err = chunk["error"]
        stream.error = ("http_error", err.get("message", str(err)) if isinstance(err, dict) else str(err))
        return
    timings = chunk.get("timings") or {}
    if timings.get("predicted_n") is not None:
        stream.server_tokens = timings["predicted_n"]
    if timings.get("prompt_n") is not None:
        # llama.cpp: prompt_n is only what it had to read this time; cache_n is
        # the part of the prompt it reused from the previous request.
        stream.prompt_tokens = timings["prompt_n"] + (timings.get("cache_n") or 0)
    usage = chunk.get("usage") or {}
    if usage.get("completion_tokens") is not None:
        stream.server_tokens = usage["completion_tokens"]
    if usage.get("prompt_tokens") is not None:
        stream.prompt_tokens = usage["prompt_tokens"]
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        got_token = False
        if delta.get("content"):
            stream.content += delta["content"]
            got_token = True
        if delta.get("reasoning_content"):
            stream.reasoning += delta["reasoning_content"]
            got_token = True
        for tc in delta.get("tool_calls") or []:
            slot = stream.tool_calls.setdefault(tc.get("index", len(stream.tool_calls)),
                                                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
            got_token = True
        if got_token:
            stream.tokens += 1
            if stream.first_token_at is None:
                stream.first_token_at = time.monotonic()
        if choice.get("finish_reason"):
            stream.finish_reason = choice["finish_reason"]


def _worker(stream: _Stream, body: bytes, socket_timeout: float, base_url: str, on_update=None) -> None:
    try:
        conn = _connection(base_url, socket_timeout)
        try:
            conn.connect()
        except OSError as exc:
            stream.error = ("unreachable", str(exc) or type(exc).__name__)
            return
        # Kept before the request: http.client drops conn.sock once a
        # streaming response takes over the socket, and close() needs it.
        stream.sock = conn.sock
        conn.request("POST", "/v1/chat/completions", body,
                     {"Content-Type": "application/json", "Accept": "text/event-stream"})
        resp = conn.getresponse()
        if resp.status != 200:
            stream.error = ("http_error", f"HTTP {resp.status}: {resp.read(2000).decode(errors='replace').strip()}")
            return
        for raw in resp:
            line = raw.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                break
            try:
                _apply_chunk(stream, json.loads(data))
            except json.JSONDecodeError as exc:
                stream.error = ("bad_reply", f"broken stream line ({exc})")
                return
            if stream.error:
                return
            if on_update is not None:
                try:
                    on_update(stream.content, stream.reasoning)
                except Exception:
                    # A bug in the caller's callback must never take down the
                    # network thread mid-stream (the request itself is still
                    # fine, and the caller already has the growing text via
                    # the shared state it wrote from on_update in the first
                    # place - this is purely defensive).
                    pass
    except (OSError, http.client.HTTPException) as exc:
        if not stream.closed_by_caller:
            stream.error = ("unreachable" if stream.tokens == 0 else "bad_reply",
                            f"connection lost: {str(exc) or type(exc).__name__}")
    finally:
        stream.done.set()


def build_payload(messages, tools, settings: AgentSettings, max_tokens: int) -> dict:
    """The request body for one call - the one place it is assembled, so a
    logged request can be rebuilt exactly (agent/report.py's rebuild_request)."""
    payload = {"model": settings.model, "messages": messages, "stream": True, "max_tokens": max_tokens,
               "temperature": settings.temperature, "top_p": settings.top_p, "seed": settings.seed,
               # Asks the server for a final usage block (prompt and reply token counts).
               "stream_options": {"include_usage": True}}
    if tools:  # no tools at all (the follow-up chat) - omit the key rather than send an empty list
        payload["tools"] = tools
    return payload


def encode_payload(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def tools_sha256(tools) -> str | None:
    return sha256_text(json.dumps(tools, sort_keys=True)) if tools else None


def _system_prompt_sha256(messages) -> str | None:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system" and isinstance(m.get("content"), str):
            return sha256_text(m["content"])
    return None


def call_llm(messages, tools, *, settings: AgentSettings | None = None, time_limit_s: float | None = None,
             max_tokens: int | None = None,
             should_abort=None, stats: dict | None = None, on_update=None, log=None):
    """One request/response round trip. messages is whatever the caller
    decides to send - chat APIs are stateless per request, so the server
    only knows what's actually in this list. `tools` is explicit rather than
    a module import since different phases (interview vs. optimize) use
    different tool sets; None/empty sends no tools at all.

    `settings` (default: whatever is saved right now) supplies the server, model,
    limits and sampling; time_limit_s / max_tokens override its limits for one call.
    time_limit_s is a hard wall-clock limit for the whole request (waiting in
    the server's queue, reading the prompt, and generating), enforced even
    when the server sends nothing. should_abort() is polled while waiting.
    stats, if given, is filled with elapsed_s/tokens/prompt_tokens/first_token_s for a
    successful request too. on_update(content_so_far, reasoning_so_far), if
    given, is called (from the request's own worker thread) every time new
    text arrives, so a caller can show live progress before the reply is
    done - see the module docstring. `log`, if given (a RunLog), gets one
    llm_call line for this request however it ends. Raises LLMError on any failure."""
    settings = settings or load_settings()
    time_limit_s = settings.llm_time_limit_s if time_limit_s is None else time_limit_s
    max_tokens = settings.llm_max_tokens if max_tokens is None else max_tokens
    body = encode_payload(build_payload(messages, tools, settings, max_tokens))

    stream = _Stream()
    message, error = None, None
    try:
        message = _request(stream, body, settings, time_limit_s, max_tokens, should_abort, on_update)
        return message
    except LLMError as exc:
        error = exc
        raise
    finally:
        reply_tokens = stream.server_tokens if stream.server_tokens is not None else stream.tokens
        first_token_s = None if stream.first_token_at is None else round(stream.first_token_at - stream.started, 1)
        elapsed_s = round(time.monotonic() - stream.started, 1)
        if message is not None and stats is not None:
            stats.update(elapsed_s=elapsed_s, tokens=reply_tokens, prompt_tokens=stream.prompt_tokens,
                         first_token_s=first_token_s)
        if log is not None and (message is not None or error is not None):
            entry = {
                "base_url": settings.api_base_url, "model": settings.model,
                "sampling": {"temperature": settings.temperature, "top_p": settings.top_p, "seed": settings.seed},
                "max_tokens": max_tokens, "time_limit_s": time_limit_s,
                "prompt_sha256": _system_prompt_sha256(messages), "tools_sha256": tools_sha256(tools),
                "messages": messages,
                "chars_in": len(body), "chars_out": len(stream.content) + len(stream.reasoning),
                "prompt_tokens": stream.prompt_tokens, "reply_tokens": reply_tokens,
                "elapsed_s": elapsed_s, "first_token_s": first_token_s,
            }
            if message is not None:
                entry["reply"] = message
            else:
                # What had arrived when it failed is kept too: a too_slow or
                # length_limit reply is mostly chain-of-thought worth reading.
                entry["error"] = {"kind": error.kind, "detail": error.detail, "message": error.describe(),
                                  "content": stream.content, "reasoning": stream.reasoning}
            log.llm_call(**entry)


def _request(stream: _Stream, body: bytes, settings: AgentSettings, time_limit_s: float, max_tokens: int,
             should_abort, on_update) -> dict:
    """Sends `body` and assembles the streamed reply into `stream`; returns the
    assistant message or raises LLMError."""
    worker = threading.Thread(target=_worker, args=(stream, body, time_limit_s + 60.0, settings.api_base_url, on_update), daemon=True)
    worker.start()

    def fail(kind: str, detail: str = "") -> LLMError:
        elapsed = time.monotonic() - stream.started
        tokens = stream.server_tokens if stream.server_tokens is not None else stream.tokens
        first = None if stream.first_token_at is None else stream.first_token_at - stream.started
        return LLMError(kind, detail, elapsed_s=elapsed, tokens=tokens, first_token_s=first,
                        time_limit_s=time_limit_s, max_tokens=max_tokens, base_url=settings.api_base_url)

    while not stream.done.wait(0.25):
        if should_abort is not None and should_abort():
            stream.close()
            raise fail("aborted")
        if time.monotonic() - stream.started >= time_limit_s:
            stream.close()  # a streaming disconnect also stops generation on the server
            raise fail("no_response" if stream.tokens == 0 else "too_slow")

    if stream.error:
        raise fail(*stream.error)
    if stream.finish_reason == "length":
        raise fail("length_limit")
    if stream.finish_reason is None and not (stream.content or stream.reasoning or stream.tool_calls):
        raise fail("bad_reply", "the stream ended without a reply")

    message = {"role": "assistant", "content": stream.content}
    if stream.reasoning:
        message["reasoning_content"] = stream.reasoning
    if stream.tool_calls:
        calls = []
        for index in sorted(stream.tool_calls):
            tc = stream.tool_calls[index]
            try:
                tc["function"]["arguments"] = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                raise fail("bad_reply", f"tool call {tc['function']['name']!r} had invalid JSON arguments ({exc})")
            calls.append(tc)
        message["tool_calls"] = calls
    return message
