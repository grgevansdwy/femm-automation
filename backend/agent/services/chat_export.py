"""A chat as a file: export to JSON (complete) and PDF (a readable report),
and import of an exported JSON as a new chat.

An import file is outside data entering a tool whose results steer real
hardware, so validate() checks everything this version reads before a single
row is written, and rejects the whole file on the first failure. Nothing in a
file is ever an instruction: its text is stored and shown, its numbers are
shown as imported results, and no solver result from it enters the solve
cache. The checksum catches a truncated or accidentally edited file; it is no
protection against a deliberate edit, which is why every other check runs too.

Pure code: no imgui, no LLM, no solver.
"""

import dataclasses
import hashlib
import json
import math
import textwrap
import warnings
from datetime import datetime

FORMAT = "femm-agent-chat"
VERSION = 1

MAX_FILE_CHARS = 20 * 1024 * 1024
MAX_MESSAGES = 5000
MAX_TEXT_CHARS = 200_000
MAX_CANDIDATES_PER_ROUND = 100

_TOP_KEYS = {"format", "version", "content_sha256", "run", "messages", "baseline_coilset"}
_RUN_KEYS = {"baseline_config_id", "spec", "status", "stop_reason", "created_at", "started_at", "finished_at",
             "error", "best_round", "best_coilset", "best_tool_result", "budget", "fragility"}
_MESSAGE_KEYS = {"phase", "seq", "role", "round_num", "content", "payload", "created_at"}


class ChatFileError(ValueError):
    """A chat file failed one of validate()'s checks. `check` is the check's
    fixed name, `detail` one sentence saying what was wrong."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check} check failed: {detail}")
        self.check, self.detail = check, detail


# --- export -----------------------------------------------------------------------------------------

def build_export(ai_run_id: str) -> dict:
    """The exportable content of one stored chat: {"run", "messages",
    "baseline_coilset"}. Raises ValueError for a chat that doesn't exist or
    is still searching (its rows are still changing).

    The baseline is the copy the run made at Start Search. A chat still being
    planned has no copy yet, so it carries its config as it is now - that is
    its baseline until Start Search. A run whose search started before
    baselines were copied carries none (null): its config may have been
    edited since, and a guess must not travel as the baseline."""
    from agent.repositories.ai_run_repository import get_ai_run, list_messages
    record = get_ai_run(ai_run_id)
    if record is None:
        raise ValueError("this chat no longer exists")
    if record["status"] == "running":
        raise ValueError("this chat's search is still running - export it when the search has ended")
    baseline = record.get("baseline_coilset")
    if baseline is None and record["status"] in ("interviewing", "plan_ready"):
        from engine.repositories.coilset_repository import get_coilset
        try:
            baseline = dataclasses.asdict(get_coilset(record["baseline_config_id"]))
        except KeyError:
            baseline = None
    return {
        "run": {key: record.get(key) for key in sorted(_RUN_KEYS)},
        "messages": [{key: m.get(key) for key in sorted(_MESSAGE_KEYS)} for m in list_messages(ai_run_id)],
        "baseline_coilset": baseline,
    }


def dumps_export(data: dict) -> str:
    """build_export()'s dict as the text of a chat file. The text is checked
    with validate() before it is returned, so a file this app would refuse to
    import is never written; raises ChatFileError naming what doesn't fit."""
    body = {key: data[key] for key in ("run", "messages", "baseline_coilset")}
    try:
        document = {"format": FORMAT, "version": VERSION, "content_sha256": _checksum(body), **body}
        text = json.dumps(document, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ChatFileError("json", f"this chat holds a value that can't be written to a file ({exc})") from None
    validate(text)
    return text


def _checksum(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- validation -------------------------------------------------------------------------------------

def _need(condition, check: str, detail: str) -> None:
    if not condition:
        raise ChatFileError(check, detail)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_time(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


# What a key's value must be, wherever this version reads that key. A key that
# is absent is fine (an older file); None is fine only where listed.
def _opt(test):
    return lambda value: value is None or test(value)


_is_str = lambda value: isinstance(value, str)      # noqa: E731
_is_bool = lambda value: isinstance(value, bool)    # noqa: E731
_is_dict = lambda value: isinstance(value, dict)    # noqa: E731
_is_list = lambda value: isinstance(value, list)    # noqa: E731
_is_dict_list = lambda value: isinstance(value, list) and all(isinstance(v, dict) for v in value)  # noqa: E731

_ROUND_PAYLOAD_TYPES = {
    "candidates": _opt(_is_dict_list), "search_attempt": _opt(_is_int), "attempt_round": _opt(_is_int),
    "segment": _opt(_is_int), "no_reply": _opt(_is_bool), "verdict": _opt(_is_str),
    "rounds_since_improvement": _opt(_is_int), "llm_seconds": _opt(_is_number), "femm_seconds": _opt(_is_number),
    "llm_failures": _opt(_is_dict_list), "llm_stats": _opt(_is_dict),
}
_CANDIDATE_TYPES = {
    "delta": _is_dict, "valid": _is_bool, "tool_result": _opt(_is_dict), "error": _opt(_is_str),
    "candidate_coilset": _opt(_is_dict), "rationale": _is_str, "error_code": _opt(_is_str),
    "rejection": _opt(_is_dict), "label": _opt(_is_str), "base_label": _opt(_is_str), "step": _opt(_is_dict),
}
_TOOL_RESULT_TYPES = {"valid": _is_bool, "goal_met": _is_bool, "error": _opt(_is_str), "objectives": _is_dict_list,
                      "fragility": _opt(_is_str)}
_FRAGILITY_TYPES = {"round": _opt(_is_int), "delta": _is_dict, "passed": _is_bool, "summary": _opt(_is_str),
                    "nudges": _is_dict_list}
_NUDGE_TYPES = {"pct": _is_number, "passed": _is_bool, "reason": _opt(_is_str), "tool_result": _opt(_is_dict)}
_OBJECTIVE_TYPES = {
    "metric": _is_str, "kind": _is_str, "requirement": _is_str, "required": _is_bool, "passed": _is_bool,
    "tolerance_pct": _is_number, "target_pct": _opt(_is_number), "target_value": _opt(_is_number),
    "worst_change_pct": _is_number, "value": _is_number, "baseline_value": _is_number, "margin": _is_number,
    "points": _is_number,
    "change_pct_by_distance": lambda v: isinstance(v, dict) and all(_is_number(x) for x in v.values()),
}
# The objective keys the result tables index directly: a result that lists objectives must have them.
_OBJECTIVE_REQUIRED = ("metric", "kind", "requirement", "required", "passed", "tolerance_pct")
_BUDGET_TYPES = {key: _opt(_is_number) for key in (
    "segment", "max_rounds", "time_limit_s", "max_simulations", "rounds_used", "simulations_used", "cache_hits",
    "seconds_used", "continues")}
_CHAT_PAYLOAD_TYPES = {"role": _is_str, "content": _opt(_is_str), "tool_calls": _opt(_is_dict_list)}


def _check_types(value: dict, types: dict, check: str, where: str) -> None:
    """Known keys must hold the right kind of value. Keys this version doesn't
    know are left alone: it never reads them, and another version's file may
    carry more than this one writes."""
    for key, test in types.items():
        if key in value:
            _need(test(value[key]), check, f"{where}: {key!r} holds the wrong kind of value")


def _check_tree(value, depth: int = 0) -> None:
    """Every text value within the size limit, every number finite."""
    _need(depth <= 40, "size", "the file is nested too deeply")
    if isinstance(value, str):
        _need(len(value) <= MAX_TEXT_CHARS, "size", f"a text value is longer than {MAX_TEXT_CHARS:,} characters")
    elif isinstance(value, float):
        _need(math.isfinite(value), "json", "a number is not finite")
    elif isinstance(value, list):
        for item in value:
            _check_tree(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _need(len(key) <= MAX_TEXT_CHARS, "size", f"a key is longer than {MAX_TEXT_CHARS:,} characters")
            _check_tree(item, depth + 1)


def _check_coilset(value, where: str) -> None:
    """Exactly this version's CoilSet fields, numbers where numbers belong.
    The same rule frontend/files_backup/config_io.py applies to an imported
    config, written again here because agent/ must not import frontend/."""
    from engine.models.coilset import CoilSet
    _need(isinstance(value, dict), "coilset", f"{where} is not a design")
    types = {f.name: f.type for f in dataclasses.fields(CoilSet)}
    missing, extra = sorted(set(types) - set(value)), sorted(set(value) - set(types))
    _need(not missing and not extra, "coilset",
          f"{where} doesn't have this version's design fields (missing {missing}, extra {extra})")
    for name, field_type in types.items():
        if field_type in (str, "str"):
            _need(isinstance(value[name], str), "coilset", f"{where}: {name!r} should be text")
        else:
            _need(_is_number(value[name]), "coilset", f"{where}: {name!r} should be a number")


def _check_tool_result(value: dict, where: str) -> None:
    _check_types(value, _TOOL_RESULT_TYPES, "payload", where)
    for objective in value.get("objectives") or []:
        missing = [key for key in _OBJECTIVE_REQUIRED if key not in objective]
        _need(not missing, "payload", f"{where}: an objective result has no {missing[0]!r}" if missing else "")
        _check_types(objective, _OBJECTIVE_TYPES, "payload", f"{where}, objective {objective['metric']!r}")


def _check_run(run) -> None:
    from agent.repositories.ai_run_repository import STATUSES, STOP_REASONS
    _need(isinstance(run, dict) and set(run) == _RUN_KEYS, "run",
          "the run section doesn't have exactly the expected fields")
    _need(run["status"] in STATUSES, "run", f"unknown status {run['status']!r}")
    _need(run["status"] != "running", "run", "the file was written while its search was still running")
    _need(run["stop_reason"] is None or run["stop_reason"] in STOP_REASONS, "run",
          f"unknown stop reason {run['stop_reason']!r}")
    _need(_is_time(run["created_at"]), "run", "created_at is not a date and time")
    for key in ("started_at", "finished_at"):
        _need(run[key] is None or _is_time(run[key]), "run", f"{key} is not a date and time")
    _need(_opt(_is_str)(run["baseline_config_id"]), "run", "baseline_config_id is not text")
    _need(_opt(_is_str)(run["error"]), "run", "the outcome report is not text")
    _need(_opt(_is_int)(run["best_round"]), "run", "best_round is not a whole number")
    _need(_opt(_is_dict_list)(run["budget"]), "run", "budget is not a list of segments")
    for segment in run["budget"] or []:
        _check_types(segment, _BUDGET_TYPES, "run", "budget")
    _need(_opt(_is_dict_list)(run["fragility"]), "run", "fragility is not a list of checks")
    for check in run["fragility"] or []:
        _check_types(check, _FRAGILITY_TYPES, "run", "a fragility check")
        for nudge in check.get("nudges") or []:
            _check_types(nudge, _NUDGE_TYPES, "run", "a fragility check's copy")
            if nudge.get("tool_result") is not None:
                _check_tool_result(nudge["tool_result"], "a fragility check's copy")
    if run["spec"] is not None:
        _check_spec(run["spec"])
    if run["best_tool_result"] is not None:
        _need(isinstance(run["best_tool_result"], dict), "payload", "the best design's result is not a result")
        _check_tool_result(run["best_tool_result"], "the best design's result")
    if run["best_coilset"] is not None:
        _check_coilset(run["best_coilset"], "the best design")


def _check_spec(spec) -> None:
    """The plan must load the way a stored run's plan loads, and the values
    the screens read from the raw dict must be what they expect."""
    from agent.input_chat.models.spec import spec_from_dict
    _need(isinstance(spec, dict), "run", "the plan is not a plan")
    distances = spec.get("distances")
    _need(isinstance(distances, list) and distances and all(_is_number(d) for d in distances), "run",
          "the plan's distances are not a list of numbers")
    _need(isinstance(spec.get("objective_text", ""), str), "run", "the plan's goal text is not text")
    _need(_opt(_is_dict)(spec.get("settings")), "run", "the plan's settings are not settings")
    _need(_opt(_is_dict)(spec.get("variable_spec")), "run", "the plan's field list is not a field list")
    _need(_is_dict_list(spec.get("objectives") or []), "run", "the plan's objectives are not a list")
    try:
        loaded = spec_from_dict(spec)
        problems = loaded.settings.validate()
        for objective in loaded.objectives:
            objective.describe()
    except Exception as exc:
        raise ChatFileError("run", f"the plan can't be read by this version ({exc})") from None
    _need(not problems, "run", f"the plan's settings are out of range ({'; '.join(problems)})")


def _check_messages(messages) -> None:
    from agent.repositories.ai_run_repository import PHASES, ROLES
    _need(isinstance(messages, list), "messages", "the messages section is not a list")
    _need(len(messages) <= MAX_MESSAGES, "messages", f"more than {MAX_MESSAGES:,} messages")
    seen = set()
    for index, message in enumerate(messages, start=1):
        where = f"message {index}"
        _need(isinstance(message, dict) and set(message) == _MESSAGE_KEYS, "messages",
              f"{where} doesn't have exactly the expected fields")
        _need(message["phase"] in PHASES, "messages", f"{where}: unknown phase {message['phase']!r}")
        _need(message["role"] in ROLES, "messages", f"{where}: unknown role {message['role']!r}")
        _need(_is_int(message["seq"]), "messages", f"{where}: seq is not a whole number")
        _need(_opt(_is_int)(message["round_num"]), "messages", f"{where}: round_num is not a whole number")
        _need(_opt(_is_str)(message["content"]), "messages", f"{where}: content is not text")
        _need(_is_time(message["created_at"]), "messages", f"{where}: created_at is not a date and time")
        _need(_opt(_is_dict)(message["payload"]), "messages", f"{where}: payload is not an object")
        key = (message["phase"], message["seq"])
        _need(key not in seen, "messages", f"{where} repeats phase {key[0]!r}, seq {key[1]}")
        seen.add(key)
        if message["phase"] == "optimize":
            _need(_is_int(message["round_num"]), "messages", f"{where}: a search round has no round number")
            _check_round_payload(message["payload"] or {}, f"round {message['round_num']}")
        else:
            _check_chat_payload(message["payload"] or {}, where)


def _check_round_payload(payload: dict, where: str) -> None:
    _check_types(payload, _ROUND_PAYLOAD_TYPES, "payload", where)
    candidates = payload.get("candidates") or []
    _need(len(candidates) <= MAX_CANDIDATES_PER_ROUND, "payload",
          f"{where} has more than {MAX_CANDIDATES_PER_ROUND} candidates")
    for index, candidate in enumerate(candidates, start=1):
        at = f"{where}, candidate {index}"
        _need("delta" in candidate and "valid" in candidate, "payload", f"{at} has no 'delta' or no 'valid'")
        _check_types(candidate, _CANDIDATE_TYPES, "payload", at)
        if candidate.get("candidate_coilset") is not None:
            _check_coilset(candidate["candidate_coilset"], at)
        if candidate.get("tool_result") is not None:
            _check_tool_result(candidate["tool_result"], at)
        if candidate["valid"]:
            _need(candidate.get("candidate_coilset") is not None and candidate.get("tool_result") is not None,
                  "payload", f"{at} is marked simulated but has no design or no result")


def _check_chat_payload(payload: dict, where: str) -> None:
    _check_types(payload, _CHAT_PAYLOAD_TYPES, "payload", where)
    for call in payload.get("tool_calls") or []:
        function = call.get("function")
        _need(isinstance(function, dict) and isinstance(function.get("name"), str)
              and isinstance(function.get("arguments", {}), (dict, str)), "payload",
              f"{where}: a tool call is malformed")


def _reject_constant(name):
    raise ValueError(f"{name} is not a number a chat file may hold")


def validate(text: str) -> dict:
    """Checks the text of a chat file and returns its content: {"run",
    "messages", "baseline_coilset"}. Raises ChatFileError naming the first
    check that failed. Reads and writes nothing else."""
    _need(isinstance(text, str) and len(text) <= MAX_FILE_CHARS, "size",
          f"the file is larger than {MAX_FILE_CHARS // (1024 * 1024)} MB")
    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ChatFileError("json", f"the file is not valid JSON, or is cut short ({exc})") from None
    _need(isinstance(document, dict) and set(document) == _TOP_KEYS, "json",
          "the file is not a chat exported by this app")
    _need(document["format"] == FORMAT, "format", "the file is not a chat exported by this app")
    version = document["version"]
    _need(_is_int(version), "version", "the file's version is not a whole number")
    _need(version <= VERSION, "version", f"the file was made by a newer version of the app (file version {version})")
    _need(version == VERSION, "version", f"file version {version} is not supported")
    _check_tree(document)
    body = {key: document[key] for key in ("run", "messages", "baseline_coilset")}
    _need(document["content_sha256"] == _checksum(body), "checksum",
          "the file was changed or damaged after it was exported")
    _check_run(body["run"])
    if body["baseline_coilset"] is not None:
        _check_coilset(body["baseline_coilset"], "the baseline design")
    _check_messages(body["messages"])
    return body


# --- import -----------------------------------------------------------------------------------------

def import_chat(text: str) -> str:
    """Stores the chat in `text` as a new run and returns its id. Raises
    ChatFileError (and writes nothing) if the file fails any check. Adds no
    config to the library and no solve to the cache; the local settings are
    not touched."""
    from agent.repositories.ai_run_repository import insert_imported_run
    body = validate(text)
    return insert_imported_run({**body["run"], "baseline_coilset": body["baseline_coilset"]}, body["messages"])


# --- PDF report -------------------------------------------------------------------------------------

_STATUS_WORDS = {
    "interviewing": "Planning (no search yet)", "plan_ready": "Plan ready, search not started",
    "goal_met": "Goal met", "budget_exhausted": "Target not achieved (budget used up)",
    "stopped_by_model": "Target not achieved (the AI stopped proposing)",
    "stalled": "Target not achieved (no progress)", "failed": "Failed",
    "interrupted": "Interrupted (the app was closed mid-search)", "cancelled": "Stopped by the user",
}
_STOP_WORDS = {
    "nothing_left": "a design passed and nothing was left to improve", "plateau": "no further improvement after a pass",
    "stalled": "no improvement before any pass", "rounds": "the round limit", "time": "the time limit",
    "simulations": "the simulation limit", "model_stopped": "the AI stopped proposing",
    "user_stopped": "stopped by the user", "llm_failed": "the AI server kept failing",
    "internal_error": "an internal error",
}

_PAGE_W, _PAGE_H = 8.27, 11.69   # A4 portrait, inches
_LEFT, _RIGHT, _TOP, _BOTTOM = 0.07, 0.93, 0.95, 0.07   # the text area, as fractions of the page
_CHAR_EM = 0.56                  # average character width over font size, for wrapping


def _num(value) -> str:
    return f"{value:.6g}" if _is_number(value) else ("-" if value is None else str(value))


def _when(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return "-"


def _plain(text) -> str:
    """One printable line: control characters would break the page layout."""
    return "".join(ch if ch.isprintable() else " " for ch in str(text))


class _Report:
    """Lays text and tables out down A4 pages, starting a new page when one
    is full. Every string is drawn with parse_math=False: a `$` in stored
    text is printed, never interpreted."""

    def __init__(self) -> None:
        self.figures = []
        self.y = _BOTTOM  # forces the first page

    def _page(self) -> None:
        from matplotlib.figure import Figure
        self.figures.append(Figure(figsize=(_PAGE_W, _PAGE_H)))
        self.y = _TOP

    def _line_h(self, size: float) -> float:
        return size * 1.4 / 72 / _PAGE_H

    def _wrap(self, text, size: float, width: float) -> list[str]:
        chars = max(int(width * _PAGE_W * 72 / (size * _CHAR_EM)), 4)
        lines = []
        for part in str(text).splitlines() or [""]:
            lines.extend(textwrap.wrap(_plain(part), chars) or [""])
        return lines

    def _room(self, height: float) -> None:
        if self.y - height < _BOTTOM:
            self._page()

    def _draw(self, x: float, text: str, size: float, bold: bool = False, color: str = "black") -> None:
        self.figures[-1].text(x, self.y, text, fontsize=size, va="top", ha="left", color=color,
                              fontweight="bold" if bold else "normal", parse_math=False)

    def gap(self, lines: float = 1.0) -> None:
        self.y -= self._line_h(10) * lines

    def text(self, text, size: float = 10, bold: bool = False, color: str = "black") -> None:
        for line in self._wrap(text, size, _RIGHT - _LEFT):
            self._room(self._line_h(size))
            self._draw(_LEFT, line, size, bold, color)
            self.y -= self._line_h(size)

    def heading(self, text) -> None:
        self._room(self._line_h(13) * 3)  # never leave a heading alone at the foot of a page
        self.gap(0.6)
        self.text(text, size=13, bold=True)
        self.gap(0.3)

    def table(self, header: list, rows: list[list], widths: list[float], size: float = 8) -> None:
        """`widths`: each column's share of the text area. A cell's text wraps
        inside its column; the header is repeated on each new page."""
        from matplotlib.lines import Line2D
        total = sum(widths)
        spans = [(_RIGHT - _LEFT) * w / total for w in widths]
        starts = [_LEFT + sum(spans[:i]) for i in range(len(spans))]
        pad = 0.004

        def draw_row(cells, bold):
            wrapped = [self._wrap(cell, size, span - 2 * pad) for cell, span in zip(cells, spans)]
            height = max(len(lines) for lines in wrapped) * self._line_h(size) + self._line_h(size) * 0.5
            top = self.y
            for lines, start in zip(wrapped, starts):
                self.y = top - self._line_h(size) * 0.25
                for line in lines:
                    self._draw(start + pad, line, size, bold)
                    self.y -= self._line_h(size)
            self.y = top - height
            self.figures[-1].add_artist(Line2D([_LEFT, _RIGHT], [self.y, self.y], color="0.75", linewidth=0.5))

        def row_height(cells):
            return (max(len(self._wrap(c, size, s - 2 * pad)) for c, s in zip(cells, spans)) + 0.5) * self._line_h(size)

        self._room(row_height(header) + (row_height(rows[0]) if rows else 0))
        draw_row(header, True)
        for cells in rows:
            if self.y - row_height(cells) < _BOTTOM:
                self._page()
                draw_row(header, True)
            draw_row(cells, False)
        self.gap(0.5)

    def save(self, path, footer: str) -> None:
        from matplotlib.backends.backend_pdf import PdfPages
        with PdfPages(path) as pdf:
            for number, figure in enumerate(self.figures, start=1):
                figure.text(_LEFT, 0.035, _plain(footer), fontsize=7, color="0.4", va="bottom", parse_math=False)
                figure.text(_RIGHT, 0.035, f"Page {number} of {len(self.figures)}", fontsize=7, color="0.4",
                            va="bottom", ha="right", parse_math=False)
                pdf.savefig(figure)


def _objective_cells(objective: dict) -> list:
    by_distance = objective.get("change_pct_by_distance") or {}
    changes = "; ".join(f"{d} mm: {c:+.2f}%" for d, c in by_distance.items() if _is_number(c))
    if not changes and _is_number(objective.get("worst_change_pct")):
        changes = f"{objective['worst_change_pct']:+.2f}%"
    return [objective.get("metric", "?") + ("" if objective.get("required", True) else " (preference)"),
            objective.get("requirement", ""), _num(objective.get("baseline_value")), _num(objective.get("value")),
            changes, "pass" if objective.get("passed") else "MISS"]


def _required_summary(result: dict) -> str:
    parts = []
    for objective in result.get("objectives") or []:
        if not objective.get("required", True):
            continue
        shown = (_num(objective.get("value")) if objective.get("target_value") is not None
                 else f"{objective['worst_change_pct']:+.1f}%" if _is_number(objective.get("worst_change_pct")) else "?")
        parts.append(f"{objective.get('metric', '?')} {shown} {'ok' if objective.get('passed') else 'MISS'}")
    return "; ".join(parts)


def _candidate_cells(round_num, candidate: dict) -> list:
    delta = candidate.get("delta") or {}
    changed = ", ".join(f"{name}={_num(value)}" for name, value in delta.items()) or "-"
    result = candidate.get("tool_result") or {}
    if not candidate.get("valid"):
        outcome = "rejected: " + (candidate.get("error") or result.get("error") or "no reason stored")
    else:
        outcome = "goal met" if result.get("goal_met") else "simulated, target missed"
    label = candidate.get("label")
    return [str(round_num) + (f" ({label})" if label else ""), changed, outcome, _required_summary(result)]


def _fragility_cells(check: dict) -> list:
    delta = check.get("delta") or {}
    copies = []
    for nudge in check.get("nudges") or []:
        pct = f"{nudge['pct']:+g}%" if _is_number(nudge.get("pct")) else "?"
        if nudge.get("passed"):
            copies.append(f"{pct}: pass")
        else:
            why = nudge.get("reason") or _required_summary(nudge.get("tool_result") or {})
            copies.append(f"{pct}: FAIL" + (f" ({why})" if why else ""))
    return [_num(check.get("round")), ", ".join(f"{name}={_num(value)}" for name, value in delta.items()) or "-",
            "survived" if check.get("passed") else "fragile", "; ".join(copies)]


def write_pdf(data: dict, path, chat_id: str | None = None, imported_at: str | None = None) -> None:
    """A readable report of one chat (build_export()'s dict): the plan, how
    the search ended, the best design against the baseline, and one row per
    candidate. Written from the stored values only - nothing is simulated or
    scored again. No chat transcript and no model reasoning."""
    run, messages, baseline = data["run"], data["messages"], data["baseline_coilset"]
    spec = run.get("spec") or {}
    report = _Report()

    report.text("Coil search report", size=18, bold=True)
    report.gap(0.5)
    report.text(f"Chat: {chat_id or '-'}     Baseline: {run.get('baseline_config_id') or '-'}")
    report.text(f"Created: {_when(run.get('created_at'))}     Search started: {_when(run.get('started_at'))}")
    report.text(f"Search ended: {_when(run.get('finished_at'))}")
    if imported_at:
        report.text(f"Imported from a file on {_when(imported_at)}. The results below come from that file and "
                    f"were not simulated on this machine.")
    status = _STATUS_WORDS.get(run.get("status"), str(run.get("status")))
    ended_by = _STOP_WORDS.get(run.get("stop_reason"))
    report.text(f"Status: {status}" + (f". Ended by: {ended_by}." if ended_by else ""), bold=True)

    report.heading("Plan")
    if not spec:
        report.text("No search plan was made in this chat.")
    else:
        report.text(f"Goal: {spec.get('objective_text') or '-'}")
        report.text("Distances (mm): " + ", ".join(_num(d) for d in spec.get("distances") or []))
        try:
            from agent.input_chat.models.spec import spec_from_dict
            for objective in spec_from_dict(spec).objectives:
                report.text(f"  - {objective.metric}: {objective.describe()}"
                            + ("" if objective.required else " (preference)"))
        except Exception:
            report.text("  (the objectives can't be read by this version)")
        if spec.get("favourite_metric"):
            report.text(f"Favourite objective: {spec['favourite_metric']}")
    if run.get("budget"):
        report.gap(0.5)
        report.table(
            ["Search segment", "Rounds used / limit", "Simulations used / limit", "Cache hits", "Minutes used / limit"],
            [[_num(b.get("segment")) + (f" (counts on from {_num(b['continues'])})" if b.get("continues") else ""),
              f"{_num(b.get('rounds_used'))} / {_num(b.get('max_rounds'))}",
              f"{_num(b.get('simulations_used'))} / {_num(b.get('max_simulations'))}", _num(b.get("cache_hits")),
              f"{(b.get('seconds_used') or 0) / 60:.1f} / {(b.get('time_limit_s') or 0) / 60:.0f}"]
             for b in run["budget"]],
            [1, 1.2, 1.4, 0.8, 1.3])

    report.heading("Result")
    best_result, best = run.get("best_tool_result"), run.get("best_coilset")
    won = run.get("status") == "goal_met" and best_result and best
    if not won:
        report.text("Target not achieved." if run.get("started_at") else "No search was run.", bold=True)
    if run.get("error"):
        report.text(run["error"])
    if best_result and best:
        report.gap(0.5)
        which = "Winning design" if won else "Closest design (did not meet the target)"
        report.text(which + (f", from round {run['best_round']}" if run.get("best_round") is not None else ""), bold=True)
        report.table(["Objective", "Requirement", "Baseline", "This design", "Change at each distance", "Result"],
                     [_objective_cells(o) for o in best_result.get("objectives") or []], [1.5, 2.2, 1, 1, 2.3, 0.7])
        if baseline:
            changed = [[name, _num(baseline.get(name)), _num(value)] for name, value in best.items()
                       if name != "config_name" and baseline.get(name) != value]
            report.text("Values changed from the baseline", bold=True)
            report.table(["Field", "Baseline", "This design"], changed or [["(none)", "", ""]], [2, 1, 1])
        else:
            report.text("The baseline design was not stored with this chat, so changed values can't be listed.")
    if run.get("fragility"):
        report.gap(0.5)
        report.text("Fragility checks (each passing design re-simulated with its dimensions nudged)", bold=True)
        report.table(["Round", "Design", "Result", "Each nudged copy"],
                     [_fragility_cells(check) for check in run["fragility"]], [0.6, 2.8, 1, 3.2])

    rounds = sorted((m for m in messages if m.get("phase") == "optimize"), key=lambda m: m.get("round_num") or 0)
    if rounds:
        report.heading("Every candidate, by round")
        rows = []
        for message in rounds:
            candidates = (message.get("payload") or {}).get("candidates") or []
            if not candidates:
                rows.append([str(message.get("round_num")), "-", "the AI proposed nothing", ""])
            rows.extend(_candidate_cells(message.get("round_num"), c) for c in candidates)
        report.table(["Round", "Values changed from the baseline", "Outcome", "Required objectives (worst distance)"],
                     rows, [0.6, 2.6, 1.6, 2.8])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a character the PDF font lacks is drawn as a box, not an error
        report.save(path, f"Chat {chat_id or '-'}. From stored results; nothing re-simulated.")
