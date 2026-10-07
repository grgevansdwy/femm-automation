"""One JSON-lines file per agent run: every AI request in full, plus warnings and
swallowed errors. A developer tool - the app writes these files and never reads
them (agent/report.py and a person do).

File: <LOG_DIR>/<ai_run_id>.jsonl, one JSON object per line, created on the first
write. Event types and their fields are in SPEC-DETAILED.md section 2.5:
run_start, llm_call, warning, run_end.

A log write that fails must never fail what it is logging: it is caught, reported
once on stderr, and the caller carries on.
"""

import dataclasses
import json
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Looked up fresh on every write (not captured at import), so a test or the
# benchmark can point it elsewhere - same convention as engine.repositories.db.DB_PATH.
LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "agent_logs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


class RunLog:
    def __init__(self, run_id: str, *, _shared: "RunLog | None" = None, _context: dict | None = None) -> None:
        self.run_id = run_id
        self._root = _shared or self
        self._context = _context or {}
        if _shared is None:
            self._lock = threading.Lock()
            self._write_failed = False

    @property
    def path(self) -> Path:
        return LOG_DIR / f"{self.run_id}.jsonl"

    def bind(self, **context) -> "RunLog":
        """The same log file with these fields (phase, round, search_attempt,
        ai_try, ...) added to every llm_call and warning written through it."""
        return RunLog(self.run_id, _shared=self._root, _context={**self._context, **context})

    def write(self, event: str, **fields) -> None:
        root = self._root
        try:
            line = json.dumps({"event": event, "ts": _now_iso(), "run_id": self.run_id, **fields},
                              default=_json_default)
            with root._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:
            if not root._write_failed:
                root._write_failed = True
                print(f"agent run log: could not write {self.path} ({exc!r}) - carrying on without it",
                      file=sys.stderr)

    def run_start(self, **fields) -> None:
        self.write("run_start", **fields)

    def llm_call(self, **fields) -> None:
        self.write("llm_call", **{**self._context, **fields})

    def warning(self, where: str, message: str, exception: BaseException | None = None) -> None:
        """The only place a raw exception (with its traceback) is written."""
        text = None
        if exception is not None:
            text = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
        self.write("warning", **{**self._context, "where": where, "message": message, "exception": text})

    def run_end(self, **fields) -> None:
        self.write("run_end", **fields)


def read_log(path) -> list[dict]:
    """Every event in a log file, in order. A line that isn't valid JSON (a
    write cut short by a crash) is skipped."""
    events = []
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events
