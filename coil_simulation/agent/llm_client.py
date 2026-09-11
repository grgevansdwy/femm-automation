"""HTTP plumbing to talk to any OpenAI-compatible /v1/chat/completions
server. Nothing here knows what a coil is - just JSON over HTTP.

Normalizes the OpenAI response shape: pulls the message out of
choices[0].message, and decodes tool_calls[].function.arguments from a
JSON STRING into a real dict in place. Confirmed necessary live against
the real target server: arguments comes back as a string, not a dict -
decode it here once instead of scattering json.loads() calls through the
loop logic.
"""

import json
import urllib.error
import urllib.request

from agent.config import API_BASE_URL, MODEL


def call_llm(messages, tools):
    """One request/response round trip (stdlib urllib, no new dependency).
    messages is whatever the caller decides to send this round - chat APIs
    are stateless per request, so the server only knows what's actually in
    this list. `tools` is explicit rather than a module import since
    different phases (interview vs. optimize) use different tool sets."""
    # OpenAI Chat Completion Format
    body = json.dumps({"model": MODEL, "messages": messages, "tools": tools, "stream": False}).encode()
    # HTTP Request
    req = urllib.request.Request(f"{API_BASE_URL}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:  # growing context / cold model loads can be slow
            raw = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Couldn't reach the LLM server at {API_BASE_URL} ({exc}). Is it running, and is AGENT_MODEL={MODEL!r} available?"
        ) from exc

    message = raw["choices"][0]["message"]
    for tc in message.get("tool_calls") or []:
        tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
    return message
