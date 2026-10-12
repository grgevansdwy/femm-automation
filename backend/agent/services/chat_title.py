"""A short title for a chat, for the list of saved chats: the plan's goal text
(or, before there is a plan, the user's first message) summarised by the model
into a few words and stored in ai_runs.title.

A title is only a label. Nothing reads it back into a prompt, a plan or a
score, and a chat without one is listed under its full text, so a failed or
slow request costs nothing but a long row. Text that is already short is its
own title and needs no request.

ChatTitler runs the requests one at a time on its own thread. The server
answers one request at a time and a thinking model spends most of a minute on
a title, so the chat list asks for one title at a time and for none while a
search is running (agent_sidebar.AgentSidebarState.refresh()).
"""

import queue
import threading
import time

from agent.services.llm_client import LLMError, call_llm

TITLE_MAX_CHARS = 40     # about what one sidebar row shows; also the most a stored title may hold
_REPLY_MAX_TOKENS = 1200  # room for a thinking model's reasoning before a few words
_SOURCE_MAX_CHARS = 2000  # a pasted wall of text is cut before it is sent
_RETRY_S = 300.0         # a failed title is asked for again no sooner than this
_MAX_TRIES = 2           # per chat, per app session

_SYSTEM_PROMPT = """You name saved chats in an engineering tool that simulates wireless-power coil sets \
(Loop, Tank and Receive coils; inductances L, mutual inductances M12, M13, M23, resistances ESR, coupling k).

The user message is the goal of one chat. Reply with a title for it and nothing else:
- at most 5 words
- say which quantity changes and how, keeping the main number, e.g. "Cut tank ESR 10%" or \
"Loop ESR -10%, hold all M"; leave out the baseline's name, the distances and other details
- plain text on one line: no quotes, no full stop, no explanation
- if the message is not a goal, name what it is about in a few words

This is only a label for a list. Answer at once: the first reasonable title is good enough, and nothing \
needs to be counted or checked."""


def plain(text) -> str:
    """`text` on one line."""
    return " ".join((text or "").split())


def needs_title(text) -> bool:
    """Text longer than a title may be; shorter text is listed as it is."""
    return len(plain(text)) > TITLE_MAX_CHARS


def clean_title(reply) -> str | None:
    """The title in a model's reply, or None if there is none: its first
    non-empty line, without wrapping quotes or a closing full stop, cut at a
    word to TITLE_MAX_CHARS. Code enforces the length - the prompt only asks."""
    line = next((plain(line) for line in (reply or "").splitlines() if line.strip()), "")
    line = line.removeprefix("Title:").strip().strip("\"'`*").rstrip(".").strip()
    if len(line) > TITLE_MAX_CHARS:
        line = line[:TITLE_MAX_CHARS + 1].rsplit(" ", 1)[0][:TITLE_MAX_CHARS].rstrip(" ,;:-")
    return line or None


def generate_title(text, settings=None, should_abort=None, log=None) -> str | None:
    """Asks the model for a title for `text`. None if the reply held no usable
    title; raises LLMError if the request itself failed."""
    reply = call_llm([{"role": "system", "content": _SYSTEM_PROMPT},
                      {"role": "user", "content": plain(text)[:_SOURCE_MAX_CHARS]}],
                     None, settings=settings, max_tokens=_REPLY_MAX_TOKENS, should_abort=should_abort, log=log)
    return clean_title(reply.get("content"))


class ChatTitler:
    """Titles chats in the background, one request at a time. request() is
    cheap and safe to call again and again for the same chat (the chat list
    does, each time it is re-read): a chat already waiting, tried too often or
    failed a moment ago is skipped. `idle` is True while nothing is waiting."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._waiting: set[str] = set()
        self._tries: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._thread: threading.Thread | None = None

    @property
    def idle(self) -> bool:
        return not self._waiting

    def request(self, ai_run_id: str, text: str) -> bool:
        """Queues a title for this chat from `text`. False if nothing was queued."""
        if not needs_title(text) or ai_run_id in self._waiting or self._tries.get(ai_run_id, 0) >= _MAX_TRIES:
            return False
        if time.monotonic() < self._retry_at.get(ai_run_id, 0.0):
            return False
        self._waiting.add(ai_run_id)
        self._queue.put((ai_run_id, text))
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        return True

    def _work(self) -> None:
        while True:
            ai_run_id, text = self._queue.get()
            try:
                self._title_one(ai_run_id, text)
            finally:
                self._waiting.discard(ai_run_id)

    def _title_one(self, ai_run_id: str, text: str) -> None:
        from agent.repositories.ai_run_repository import set_title
        from agent.services.run_log import RunLog

        log = RunLog(ai_run_id).bind(phase="title")
        self._tries[ai_run_id] = self._tries.get(ai_run_id, 0) + 1
        self._retry_at[ai_run_id] = time.monotonic() + _RETRY_S
        try:
            title = generate_title(text, log=log)
            if title is not None:
                set_title(ai_run_id, title)
        except LLMError:
            pass  # in the run's log already (call_llm); the chat keeps its full text
        except Exception as exc:
            log.warning("ChatTitler._title_one", "a chat title could not be made or saved", exc)
