#!/usr/bin/env python3
"""Report Claude Code usage for a project: tokens used, cost, and roughly how
full the current conversation's context window is.

Reads the local session transcript Claude Code already writes to
~/.claude/projects/<project-slug>/<session-id>.jsonl - no network calls, no
API key needed. Standard library only.

WHAT THIS CAN AND CANNOT TELL YOU (read before trusting the numbers):

  - Tokens used, per model, cumulative for one session: REAL. Read straight
    from the transcript's own "cost-state" entries, which Claude Code writes
    as it goes.
  - "How full is the context window right now": an ESTIMATE. Claude Code
    doesn't write "the context window is X% full" anywhere locally, as far
    as this script's author (Claude) could find by searching ~/.claude/.
    What IS real: the token counts (input + cache_read + cache_creation) on
    the most recent turn, which is what the model actually saw. This script
    divides that by --context-window (a number YOU supply) to estimate a
    percentage. Get the real number for your plan/model from Anthropic's
    docs, not from this script.
  - "Time until it resets": NOT AVAILABLE. Searched the transcript, and
    ~/.claude/state/, ~/.claude/telemetry/, ~/.claude/session-env/ - none of
    them contain a rate-limit or usage-window reset timestamp. Claude Code's
    Pro/Max usage limits (the ones with a reset countdown) are tracked on
    Anthropic's servers, not written to a local file this script can read.
    If you need that number, it's what Claude Code itself shows you when
    you're close to a limit - there's no separate API this script can call
    for it without your own Anthropic API credentials and their own,
    different rate-limit headers (a different thing from Claude Code's plan
    limits).

Usage:
    python3 scripts/claude_usage.py
    python3 scripts/claude_usage.py --context-window 200000
    python3 scripts/claude_usage.py --project-dir /path/to/other/project
    python3 scripts/claude_usage.py --session <session-id>
    python3 scripts/claude_usage.py --list-sessions
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Anthropic hasn't published one number that's true for every plan/model, so
# this is a placeholder, not a fact. Override with --context-window once you
# know yours.
DEFAULT_CONTEXT_WINDOW = 200_000


def project_slug(path: Path) -> str:
    """Claude Code's own encoding: the absolute cwd with '/' and '_' -> '-'
    (confirmed against this machine's own ~/.claude/projects/ - e.g.
    .../femm_automation encodes as ...-femm-automation, underscore included)."""
    resolved = str(path.resolve())
    return resolved.replace("/", "-").replace("_", "-")


def find_transcripts(project_dir: Path) -> list[Path]:
    if not project_dir.is_dir():
        return []
    return sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def pick_transcript(project_dir: Path, session_id: str | None) -> Path | None:
    files = find_transcripts(project_dir)
    if not files:
        return None
    if session_id:
        for f in files:
            if f.stem == session_id or f.stem.startswith(session_id):
                return f
        return None
    return files[-1]  # most recently modified = the live/current session


def iter_json_lines(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a partially-written last line while Claude Code is live


def collect_usage(path: Path) -> dict:
    """Walks the transcript once. Returns the latest cumulative cost-state
    (real, cumulative-for-the-session) and the latest assistant turn's raw
    usage block (real, one-turn - used for the context-window estimate)."""
    latest_cost_state = None
    latest_turn_usage = None
    latest_turn_model = None

    for entry in iter_json_lines(path):
        etype = entry.get("type")
        if etype == "cost-state":
            latest_cost_state = entry
        elif etype == "assistant":
            usage = entry.get("message", {}).get("usage")
            if usage:
                latest_turn_usage = usage
                latest_turn_model = entry.get("message", {}).get("model")

    return {
        "cost_state": latest_cost_state,
        "turn_usage": latest_turn_usage,
        "turn_model": latest_turn_model,
    }


def fmt_tokens(n: int) -> str:
    return f"{n:,}"


def fmt_duration_ms(ms: int) -> str:
    s = ms / 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}h {int(m)}m"
    if m >= 1:
        return f"{int(m)}m {int(s)}s"
    return f"{s:.1f}s"


def print_report(transcript: Path, data: dict, context_window: int) -> None:
    cost_state = data["cost_state"]
    turn_usage = data["turn_usage"]
    turn_model = data["turn_model"]

    print(f"Session file : {transcript}")
    print(f"Last updated : {datetime.fromtimestamp(transcript.stat().st_mtime, tz=timezone.utc).astimezone():%Y-%m-%d %H:%M:%S %Z}")
    print()

    # ---- cumulative tokens used, real data ---------------------------------
    print("=" * 62)
    print("TOKENS USED THIS SESSION (cumulative, from cost-state)")
    print("=" * 62)
    if cost_state is None:
        print("No cost-state entries found yet in this transcript.")
    else:
        model_usage = cost_state.get("modelUsage", {})
        total_in = total_out = total_cache_read = total_cache_write = 0
        for model, u in model_usage.items():
            inp = u.get("inputTokens", 0)
            out = u.get("outputTokens", 0)
            think = u.get("thinkingTokens", 0)
            cread = u.get("cacheReadInputTokens", 0)
            ccreate = u.get("cacheCreationInputTokens", 0)
            cost = u.get("costUSD", 0.0)
            total_in += inp
            total_out += out
            total_cache_read += cread
            total_cache_write += ccreate
            print(f"\n  {model}")
            print(f"    input (fresh, uncached)  : {fmt_tokens(inp)}")
            print(f"    output (incl. thinking)  : {fmt_tokens(out)}  (thinking: {fmt_tokens(think)})")
            print(f"    cache read               : {fmt_tokens(cread)}")
            print(f"    cache write              : {fmt_tokens(ccreate)}")
            print(f"    cost                     : ${cost:,.4f}")

        grand_total_tokens = total_in + total_out + total_cache_read + total_cache_write
        print(f"\n  TOTAL across all models    : {fmt_tokens(grand_total_tokens)} tokens")
        print(f"  TOTAL cost                 : ${cost_state.get('totalCostUSD', 0.0):,.4f}")
        print(f"  Session wall-clock time    : {fmt_duration_ms(cost_state.get('totalDuration', 0))}")
        print(f"  Time actually spent on API : {fmt_duration_ms(cost_state.get('totalAPIDuration', 0))}")

    # ---- context window estimate, from the latest turn only ---------------
    print()
    print("=" * 62)
    print("CURRENT CONTEXT WINDOW (estimate - see the script's docstring)")
    print("=" * 62)
    if turn_usage is None:
        print("No assistant turns with usage data found yet.")
    else:
        occupied = (
            turn_usage.get("input_tokens", 0)
            + turn_usage.get("cache_read_input_tokens", 0)
            + turn_usage.get("cache_creation_input_tokens", 0)
        )
        pct_used = occupied / context_window * 100 if context_window else 0.0
        pct_left = 100 - pct_used
        print(f"  Model (last turn)     : {turn_model or 'unknown'}")
        print(f"  Context in last turn  : {fmt_tokens(occupied)} tokens")
        print(f"  Assumed window size   : {fmt_tokens(context_window)} tokens  (--context-window to change)")
        print(f"  Estimated used        : {pct_used:.1f}%")
        print(f"  Estimated remaining   : {pct_left:.1f}%")
        bar_width = 40
        filled = max(0, min(bar_width, int(bar_width * pct_used / 100)))
        print(f"  [{'#' * filled}{'-' * (bar_width - filled)}]")
        if occupied > context_window:
            print(f"\n  NOTE: last turn alone ({fmt_tokens(occupied)}) is bigger than --context-window")
            print(f"  ({fmt_tokens(context_window)}). Either that request used a larger context")
            print(f"  window than assumed, or {fmt_tokens(context_window)} is the wrong number for")
            print(f"  this model/plan - pass the real one with --context-window.")

    # ---- reset timer: honestly not available -------------------------------
    print()
    print("=" * 62)
    print("TIME UNTIL USAGE RESETS")
    print("=" * 62)
    print("  Not available locally - Claude Code's plan usage limits are tracked")
    print("  server-side, not written to any file this script can read. See the")
    print("  script's docstring for exactly what was checked.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-dir", type=Path, default=Path.cwd(),
                        help="Project directory Claude Code was run in (default: current directory)")
    parser.add_argument("--session", help="Specific session id (or a prefix of one). Default: most recently updated session.")
    parser.add_argument("--context-window", type=int, default=DEFAULT_CONTEXT_WINDOW,
                        help=f"Context window size in tokens to estimate against (default: {DEFAULT_CONTEXT_WINDOW:,}, a placeholder - see docstring)")
    parser.add_argument("--list-sessions", action="store_true", help="List available sessions for this project and exit")
    args = parser.parse_args()

    slug = project_slug(args.project_dir)
    project_transcripts_dir = CLAUDE_PROJECTS_DIR / slug

    if not project_transcripts_dir.is_dir():
        print(f"No Claude Code session data found for {args.project_dir} "
              f"(looked in {project_transcripts_dir})", file=sys.stderr)
        sys.exit(1)

    if args.list_sessions:
        files = find_transcripts(project_transcripts_dir)
        if not files:
            print("No sessions found.")
            return
        for f in files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"{f.stem}  (last updated {mtime}, {f.stat().st_size / 1024:.0f} KB)")
        return

    transcript = pick_transcript(project_transcripts_dir, args.session)
    if transcript is None:
        print("No matching session transcript found.", file=sys.stderr)
        sys.exit(1)

    data = collect_usage(transcript)
    print_report(transcript, data, args.context_window)


if __name__ == "__main__":
    main()
