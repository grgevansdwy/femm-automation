"""Command-line run report: what the stored agent runs did, from the database
(ai_runs / ai_run_messages) and the per-run log files (agent/services/run_log.py).
A developer tool - nothing in the app calls it.

Run:     cd backend && uv run python -m agent.report                    # every run
Options: --run ID            one run in detail (a unique prefix of its id is enough)
         --since YYYY-MM-DD  only runs created on or after that day
         --db PATH, --logs DIR   read another database / log folder, e.g. the benchmark's:
                                 --db data/benchmark/femm_automation.db --logs data/benchmark/<label>/logs
         --request ROUND     with --run: print the exact request body round ROUND sent, rebuilt
                             from the log, ready to re-send:
                             ... --run ID --request 3 > body.json
                             curl <server>/v1/chat/completions -H 'Content-Type: application/json' -d @body.json

The database is opened read-only. A run with no log file (anything older than the
log) still gets a row; the columns only the log knows show "-".
"""

import argparse
import dataclasses
import json
import sqlite3
import statistics
import sys
from datetime import datetime
from pathlib import Path

from engine.repositories import db
from agent.femm_agent.services.scorer import blended_score
from agent.repositories.ai_run_repository import _message_row_to_record, _run_row_to_record
from agent.services import run_log
from agent.services.llm_client import build_payload, encode_payload, tools_sha256
from agent.settings.settings import AgentSettings


# --- reading ------------------------------------------------------------------

def _read_db(db_path, since: str | None = None, run_id: str | None = None) -> list[tuple[dict, list[dict]]]:
    """(run record, its optimize-phase messages) for every matching run, oldest
    first. Read-only: this never creates a table or a file."""
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        query, params = "SELECT * FROM ai_runs WHERE 1=1", []
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        if run_id:
            query += " AND id LIKE ?"
            params.append(run_id + "%")
        try:
            runs = [_run_row_to_record(r) for r in conn.execute(query + " ORDER BY created_at", params)]
        except sqlite3.OperationalError:  # a database with no agent tables yet
            return []
        out = []
        for run in runs:
            rows = conn.execute("SELECT * FROM ai_run_messages WHERE ai_run_id = ? AND phase = 'optimize' ORDER BY seq",
                                (run["id"],)).fetchall()
            out.append((run, [_message_row_to_record(r) for r in rows]))
        return out
    finally:
        conn.close()


def _read_log(log_dir, run_id: str) -> list[dict]:
    path = Path(log_dir) / f"{run_id}.jsonl"
    return run_log.read_log(path) if path.exists() else []


# --- one run's numbers ----------------------------------------------------------

def _minutes(start: str | None, end: str | None) -> float | None:
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def summarize_run(run: dict, rounds: list[dict], events: list[dict]) -> dict:
    """One row of the report. `rounds` are the run's optimize-phase messages,
    `events` its log (may be empty)."""
    candidates = [c for r in rounds for c in (r["payload"] or {}).get("candidates", [])]
    valid = [c for c in candidates if c.get("valid")]
    llm_s = sum((r["payload"] or {}).get("llm_seconds") or 0 for r in rounds)
    femm_s = sum((r["payload"] or {}).get("femm_seconds") or 0 for r in rounds)
    first_pass = next((r["round_num"] for r in rounds
                       if any(c.get("valid") and (c.get("tool_result") or {}).get("goal_met")
                              for c in (r["payload"] or {}).get("candidates", []))), None)
    deltas = [json.dumps(c.get("delta"), sort_keys=True) for c in candidates]
    best = run.get("best_tool_result")

    calls = [e for e in events if e.get("event") == "llm_call"]
    # One run_end per search segment (the first search, then each "continue").
    ends = [e for e in events if e.get("event") == "run_end"]
    end = ends[-1] if ends else None
    prompt_tokens = [e["prompt_tokens"] for e in calls if e.get("prompt_tokens") is not None]
    minutes = _minutes(run.get("started_at") or run.get("created_at"), run.get("finished_at"))
    if minutes is None and end is not None:
        minutes = end.get("seconds", 0) / 60
    return {
        "id": run["id"], "created_at": run["created_at"], "status": run["status"],
        "stop_reason": run.get("stop_reason") or (end.get("stop_reason") if end else None),
        "segments": len(run.get("budget") or []) or len(ends) or None,
        "rounds": len(rounds), "candidates": len(candidates), "rejected": len(candidates) - len(valid),
        "simulations": sum(e.get("simulations") or 0 for e in ends) if ends else None,
        "cache_hits": sum(e.get("cache_hits") or 0 for e in ends) if ends else None,
        "minutes": minutes,
        "ai_share": llm_s / (llm_s + femm_s) if llm_s + femm_s else None,
        "prompt_tokens": sum(prompt_tokens) if prompt_tokens else None,
        "reply_tokens": (sum(e.get("reply_tokens") or 0 for e in calls) if calls else
                         sum(((r["payload"] or {}).get("llm_stats") or {}).get("tokens") or 0 for r in rounds) or None),
        "first_pass_round": first_pass,
        "best_score": blended_score(best, (run.get("spec") or {}).get("favourite_metric")) if best and best.get("goal_met") else None,
        "repeated": len(deltas) - len(set(deltas)),
        "llm_calls": len(calls) or None,
        "llm_errors": sum(1 for e in calls if "error" in e) if calls else None,
        "warnings": sum(1 for e in events if e.get("event") == "warning") if events else None,
        "has_log": bool(events),
    }


# --- tables ---------------------------------------------------------------------

def _fmt(value, digits=0, pct=False) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:.0%}"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()  # noqa: E731
    return "\n".join([line(headers), line(["-" * w for w in widths])] + [line(r) for r in rows])


def runs_table(summaries: list[dict]) -> str:
    headers = ["Run", "Created", "Status", "Stop reason", "Rounds", "Cand.", "Rejected", "Sims", "Cache hits",
               "Min", "AI share", "Prompt tok", "Reply tok", "First pass", "Best score", "Repeats", "Log"]
    rows = [[s["id"][:8], (s["created_at"] or "")[:16], s["status"], _fmt(s["stop_reason"]), _fmt(s["rounds"]),
             _fmt(s["candidates"]),
             f"{s['rejected']} ({s['rejected'] / s['candidates']:.0%})" if s["candidates"] else "-",
             _fmt(s["simulations"]), _fmt(s["cache_hits"]), _fmt(s["minutes"], 1), _fmt(s["ai_share"], pct=True),
             _fmt(s["prompt_tokens"]), _fmt(s["reply_tokens"]), _fmt(s["first_pass_round"]),
             _fmt(s["best_score"], 2), _fmt(s["repeated"]), "yes" if s["has_log"] else "no"]
            for s in summaries]
    return _table(headers, rows)


def _median(values, digits=1) -> str:
    values = [v for v in values if v is not None]
    return "-" if not values else f"{statistics.median(values):.{digits}f}"


def totals_table(summaries: list[dict]) -> str:
    """Cross-run figures, one row per status and one for everything. Medians
    are over the runs that have the number."""
    headers = ["Status", "Runs", "Rounds (median)", "Candidates", "Rejected share", "Sims (median)",
               "Minutes (median)", "AI share (median)", "First pass round (median)", "Best score (median)",
               "Repeated candidates"]
    groups = [("all", summaries)] + [(status, [s for s in summaries if s["status"] == status])
                                     for status in sorted({s["status"] for s in summaries})]
    rows = []
    for name, group in groups:
        candidates = sum(s["candidates"] for s in group)
        rejected = sum(s["rejected"] for s in group)
        rows.append([name, str(len(group)), _median([s["rounds"] for s in group], 0), str(candidates),
                     f"{rejected / candidates:.0%}" if candidates else "-",
                     _median([s["simulations"] for s in group], 0), _median([s["minutes"] for s in group]),
                     _median([s["ai_share"] for s in group], 2),
                     _median([s["first_pass_round"] for s in group], 0),
                     _median([s["best_score"] for s in group], 2), str(sum(s["repeated"] for s in group))])
    return _table(headers, rows)


def run_detail(summary: dict, rounds: list[dict], events: list[dict]) -> str:
    """One run, round by round, then its AI requests and warnings from the log."""
    lines = [f"Run {summary['id']}  ({summary['status']}, created {summary['created_at']})", ""]
    lines.append(_table(
        ["Round", "Attempt", "Candidates", "Rejected", "Passed", "AI s", "FEMM s", "Reply tok"],
        [[str(r["round_num"]), str((r["payload"] or {}).get("search_attempt", 1)),
          str(len(cs := (r["payload"] or {}).get("candidates", []))),
          str(sum(1 for c in cs if not c.get("valid"))),
          str(sum(1 for c in cs if c.get("valid") and (c.get("tool_result") or {}).get("goal_met"))),
          _fmt((r["payload"] or {}).get("llm_seconds"), 0), _fmt((r["payload"] or {}).get("femm_seconds"), 0),
          _fmt(((r["payload"] or {}).get("llm_stats") or {}).get("tokens"))] for r in rounds]))
    calls = [e for e in events if e.get("event") == "llm_call"]
    if calls:
        lines += ["", "AI requests (from the log):", _table(
            ["Phase", "Round", "Try", "Sent chars", "Prompt tok", "Reply tok", "Seconds", "First token s", "Result"],
            [[str(e.get("phase", "-")), _fmt(e.get("round")), _fmt(e.get("ai_try")), _fmt(e.get("chars_in")),
              _fmt(e.get("prompt_tokens")), _fmt(e.get("reply_tokens")), _fmt(e.get("elapsed_s")),
              _fmt(e.get("first_token_s")), e["error"]["kind"] if "error" in e else "ok"] for e in calls])]
    else:
        lines += ["", "No log file for this run, so its AI requests are not available."]
    warnings = [e for e in events if e.get("event") == "warning"]
    if warnings:
        lines += ["", "Warnings:"] + [f"  {e.get('ts', '')[:19]}  {e.get('where')}: {e.get('message')}" for e in warnings]
    return "\n".join(lines)


# --- replay -----------------------------------------------------------------------

def rebuild_request(events: list[dict], round_num: int, ai_try: int | None = None) -> bytes:
    """The exact request body a search round sent, rebuilt from the run's log
    alone: the logged messages, model, sampling and token cap, plus the tool
    schema from the run_start line. `ai_try` picks one try of the round (default:
    the last). Raises ValueError when the log can't answer - no such round, or
    the tool schema in the log isn't the one that request carried."""
    calls = [e for e in events if e.get("event") == "llm_call" and e.get("phase") == "search"
             and e.get("round") == round_num and (ai_try is None or e.get("ai_try") == ai_try)]
    if not calls:
        raise ValueError(f"the log has no search request for round {round_num}"
                         + (f", try {ai_try}" if ai_try is not None else ""))
    call = calls[-1]
    tools = None
    if call.get("tools_sha256"):
        # The run_start line in force when this request was made (the last one before it).
        starts = [e for e in events[:events.index(call)] if e.get("event") == "run_start"]
        tools = starts[-1].get("tools") if starts else None
        if tools_sha256(tools) != call["tools_sha256"]:
            raise ValueError("the tool schema in the log's run_start line is not the one this request sent")
    try:
        sampling = call["sampling"]
        settings = dataclasses.replace(AgentSettings(), model=call["model"], temperature=sampling["temperature"],
                                       top_p=sampling["top_p"], seed=sampling["seed"])
        return encode_payload(build_payload(call["messages"], tools, settings, call["max_tokens"]))
    except KeyError as exc:
        raise ValueError(f"that request's log line is incomplete: no {exc.args[0]!r}") from exc


# --- entry point --------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run", help="one run in detail (its id, or a unique prefix)")
    parser.add_argument("--since", help="only runs created on or after this day (YYYY-MM-DD)")
    parser.add_argument("--db", help="database file to read (default: the app's)")
    parser.add_argument("--logs", help="folder of run logs (default: backend/data/agent_logs)")
    parser.add_argument("--request", type=int, metavar="ROUND",
                        help="with --run: print the request body that round sent, rebuilt from the log")
    args = parser.parse_args(argv)
    db_path = args.db or db.DB_PATH
    log_dir = args.logs or run_log.LOG_DIR

    found = _read_db(db_path, since=args.since, run_id=args.run)
    if args.run:
        if len(found) != 1:
            print(f"--run {args.run}: {len(found)} runs match in {db_path}", file=sys.stderr)
            return 1
        run, rounds = found[0]
        events = _read_log(log_dir, run["id"])
        if args.request is not None:
            try:
                sys.stdout.write(rebuild_request(events, args.request).decode() + "\n")
            except ValueError as exc:
                print(f"Can't rebuild that request: {exc}", file=sys.stderr)
                return 1
            return 0
        print(run_detail(summarize_run(run, rounds, events), rounds, events))
        return 0
    if args.request is not None:
        print("--request needs --run", file=sys.stderr)
        return 1

    summaries = [summarize_run(run, rounds, _read_log(log_dir, run["id"])) for run, rounds in found]
    if not summaries:
        print(f"No agent runs in {db_path}" + (f" since {args.since}" if args.since else "") + ".")
        return 0
    print(runs_table(summaries))
    print()
    print(totals_table(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
