"""Benchmark for the search loop: a fixed set of goals, each run several times
against the real LLM server, so a change to the prompt or the loop can be judged
by numbers instead of by watching one run.

Run:     cd backend && uv run python -m agent.benchmark            # every goal x REPEATS
         cd backend && uv run python -m agent.benchmark --report   # rewrite docs/summarize_result.md
Options: --label NAME (default "baseline") keeps separate result sets, e.g. one per milestone;
         --goals g1,g3 and --repeats N narrow a run.

Skips the interview: each goal is an AgentSpec built here, so only the search is
measured. Searches run one at a time (the LLM server is shared and keeps one model
loaded). A finished search is never re-run - delete its result file to redo it.

Nothing here touches the real database: the first run copies it to
backend/data/benchmark/ (git-ignored) and every solve and ai_run goes there.
Each search's AI requests are also written, in full, to logs/<ai_run_id>.jsonl next to the
results (agent/services/run_log.py); the result file's "run_id" says which log is its.
"""

import argparse
import dataclasses
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_GUI_SRC = _BACKEND.parent / "frontend" / "src"
if str(_GUI_SRC) not in sys.path:
    sys.path.insert(0, str(_GUI_SRC))

from frontend.services import job_runner as job_runner_module  # noqa: E402

from engine.repositories import db  # noqa: E402
from agent.femm_agent.models.state import AgentRunStatus  # noqa: E402
from agent.femm_agent.services.runner import AgentRunner  # noqa: E402
from agent.femm_agent.services.scorer import blended_score, points_past_bar, tool_result  # noqa: E402
from agent.input_chat.models.objective import Objective  # noqa: E402
from agent.input_chat.models.spec import AgentSpec  # noqa: E402
from agent.services import run_log  # noqa: E402
from agent.settings.settings import load_settings  # noqa: E402

BENCH_DIR = _BACKEND / "data" / "benchmark"
REPORT_PATH = _BACKEND.parent / "docs" / "summarize_result.md"
NOTES_MARKER = "<!-- hand-written from here down; kept when the tables above are regenerated -->"

REPEATS = 5
# The decided budget, set here rather than read from Settings so every
# result set compares like for like. The search enforces both itself
# (state.Budget); the baseline set, measured before it could, had the time
# limit enforced by this script and reports it as status "time_limit".
MAX_ROUNDS = 25
TIME_LIMIT_S = 90 * 60


def _hold(*metrics, tol=5.0):
    return [Objective(metric=m, kind="match", tolerance_pct=tol) for m in metrics]


def _cut(*metrics, by):
    return [Objective(metric=m, kind="reduce", tolerance_pct=by) for m in metrics]


@dataclasses.dataclass(frozen=True)
class Goal:
    key: str
    difficulty: str
    baseline: str
    distances: tuple
    objectives: tuple
    text: str


GOALS = [
    Goal("g1", "easy", "Gen2_coil", (5.0,),
         tuple(_cut("tank_esr", by=10)),
         "Cut tank ESR by at least 10%."),
    Goal("g2", "easy", "base_coil", (5.0, 10.0),
         tuple(_cut("tank_esr", by=10) + _hold("M23")),
         "Cut tank ESR by at least 10%, hold M23 within 5%."),
    Goal("g3", "medium", "Gen3_2mm_61_RC_ferrite", (5.0, 10.0, 15.0),
         tuple(_cut("rc_esr", by=10) + _hold("M12", "M13", "M23")),
         "Cut RC ESR by at least 10%, hold all three M within 5% (ferrite fitted, kept fixed)."),
    Goal("g4", "hard", "Gen2_coil", (5.0, 10.0, 15.0),
         tuple(_cut("loop_esr", "tank_esr", "rc_esr", by=20) + _hold("M12", "M13", "M23")),
         "Cut ESR of all three coils by at least 20%, hold all three M within 5%."),
    Goal("g5", "hard", "Gen2_coil", (10.0, 20.0, 30.0, 40.0),
         tuple(_hold("M12", "M13", "M23") + _cut("tank_esr", by=20) + _cut("loop_esr", "rc_esr", by=0)
               + _cut("loop_outer_diameter", "tank_outer_diameter", "rc_outer_diameter", by=0)
               + _cut("L_loop", "L_tank", "L_rc", by=0)),
         "The original brief: hold all three M within 5%, cut tank ESR by at least 20%, and let no other "
         "ESR, outer diameter or self-inductance grow."),
]


def _use_benchmark_db() -> None:
    """Points every repository (and the solve cache) at a copy of the real
    database, made once - so the benchmark starts with the same configs and
    cached solves, and leaves the real History untouched."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    target = BENCH_DIR / "femm_automation.db"
    if not target.exists():
        source = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
        copy = sqlite3.connect(target)
        with copy:
            source.backup(copy)
        source.close()
        copy.close()
    db.DB_PATH = target
    job_runner_module.RESULTS_ROOT = BENCH_DIR


def _seconds(start, end) -> float:
    return (end - start).total_seconds() if start is not None and end is not None else 0.0


def run_one(goal: Goal, repeat: int, results_dir: Path) -> dict:
    settings = dataclasses.replace(load_settings(), max_tool_calls=MAX_ROUNDS, search_time_limit_s=float(TIME_LIMIT_S))
    spec = dataclasses.replace(
        AgentSpec.from_settings(settings, baseline_config_id=goal.baseline),
        distances=list(goal.distances), objectives=list(goal.objectives), objective_text=goal.text)

    run_log.LOG_DIR = results_dir / "logs"
    runner = AgentRunner(job_runner_module.JobRunner())
    started = time.monotonic()
    state = runner.submit(spec)
    seen_rounds = 0
    while state.status == AgentRunStatus.RUNNING:  # every search ends by its own budget or stop rules
        if len(state.rounds) != seen_rounds:
            seen_rounds = len(state.rounds)
            print(f"    round {seen_rounds} ({(time.monotonic() - started) / 60:.0f} min)", flush=True)
        time.sleep(1.0)

    candidates = [c for r in state.rounds for c in r.candidates]
    valid = [c for c in candidates if c.valid]
    first_pass = next((r.round_num for r in state.rounds
                       if any(c.valid and c.tool_result["goal_met"] for c in r.candidates)), None)
    return {
        "goal": goal.key, "repeat": repeat, "run_id": state.id,
        "status": state.status.value, "stop_reason": state.stop_reason,
        "budget": state.budget_history[-1] if state.budget_history else None,
        "goal_met": bool(state.best is not None and state.best.goal_met),
        "minutes": round((time.monotonic() - started) / 60, 1),
        "rounds": len(state.rounds),
        "first_pass_round": first_pass,
        "candidates": len(candidates),
        "rejected": len(candidates) - len(valid),
        "simulations": state.budget.simulations_used,  # fresh solves; cache hits and the baseline are free
        "llm_seconds": round(sum(_seconds(r.proposing_started_at, r.evaluating_started_at) for r in state.rounds)),
        "femm_seconds": round(sum(_seconds(r.evaluating_started_at, r.done_at) for r in state.rounds)),
        "reply_tokens": sum(r.llm_stats.get("tokens") or 0 for r in state.rounds),
        "llm_failures": len(state.llm_failures),
        "mean_fields_changed": round(statistics.mean(len(c.delta) for c in valid), 1) if valid else None,
        "best": tool_result(state.best, spec) if state.best is not None else None,
        "best_delta": {k: v[1] for k, v in state.best.changed_fields.items()} if state.best is not None else None,
        "outcome_report": state.outcome_report,
        "settings": {k: v for k, v in dataclasses.asdict(settings).items() if k != "prompts"},
        "rounds_detail": [
            {"round": r.round_num,
             "llm_s": round(_seconds(r.proposing_started_at, r.evaluating_started_at)),
             "femm_s": round(_seconds(r.evaluating_started_at, r.done_at)),
             "candidates": [{"delta": c.delta, "valid": c.valid, "error": c.error, "rationale": c.rationale,
                             "result": c.tool_result} for c in r.candidates]}
            for r in state.rounds],
    }


def run(label: str, goal_keys, repeats: int) -> None:
    _use_benchmark_db()
    results_dir = BENCH_DIR / label
    results_dir.mkdir(parents=True, exist_ok=True)
    goals = [g for g in GOALS if not goal_keys or g.key in goal_keys]
    for repeat in range(1, repeats + 1):  # repeat-major, so an interrupted benchmark still covers every goal
        for goal in goals:
            path = results_dir / f"{goal.key}_{repeat}.json"
            if path.exists():
                continue
            print(f"[{time.strftime('%H:%M')}] {goal.key} ({goal.difficulty}) repeat {repeat}/{repeats}: {goal.text}",
                  flush=True)
            try:
                result = run_one(goal, repeat, results_dir)
            except Exception as exc:
                result = {"goal": goal.key, "repeat": repeat, "status": "benchmark_error", "goal_met": False,
                          "error": repr(exc)}
            path.write_text(json.dumps(result, indent=1, default=str))
            print(f"    -> {result['status']}, {result.get('rounds', 0)} rounds, "
                  f"{result.get('minutes', 0)} min", flush=True)
    print("Benchmark finished.", flush=True)


# --- report -----------------------------------------------------------------

def _median(values, digits=1):
    values = [v for v in values if v is not None]
    return "-" if not values else f"{statistics.median(values):.{digits}f}"


def _sampling_sentence(results: list[dict]) -> str:
    """How the model's sampling was set for this result set, from the settings each search recorded."""
    sent = {(s.get("temperature"), s.get("top_p"), s.get("seed"))
            for s in (r.get("settings") or {} for r in results if r.get("status") != "benchmark_error")}
    if not sent or sent == {(None, None, None)}:
        return ("Sampling was left to the server's defaults (temperature 1.0, top_p 0.95, random seed when "
                "read on 2026-10-07), so repeats differ.")
    described = "; ".join(
        "left to the server's defaults" if t is None else
        f"temperature {t:g}, top_p {p:g}, {'a random seed' if seed == -1 else f'seed {seed}'}"
        for t, p, seed in sorted(sent, key=str))
    return f"Sampling sent with every request: {described}."


def _load(label: str) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((BENCH_DIR / label).glob("g*_*.json"))]


def report(label: str) -> str:
    results = _load(label)
    lines = [
        "---",
        "owns: backend/agent/benchmark.py (its results; regenerate with `uv run python -m agent.benchmark --report`)",
        f"last-verified: {time.strftime('%Y-%m-%d')}",
        "---",
        "",
        "# Search benchmark results",
        "",
        f"Result set: `{label}`. {len(results)} searches finished out of {len(GOALS) * REPEATS} planned "
        f"({len(GOALS)} goals x {REPEATS} repeats). Generated {time.strftime('%Y-%m-%d %H:%M')} from "
        f"`backend/data/benchmark/{label}/`.",
        "",
        "Each search is the search loop alone (no interview) against the real LLM server, limited to "
        f"{MAX_ROUNDS} rounds and {TIME_LIMIT_S // 60} minutes. {_sampling_sentence(results)}",
        "",
        "## Goals",
        "",
        "| Goal | Difficulty | Baseline | Distances (mm) | Objective |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {g.key} | {g.difficulty} | `{g.baseline}` | {', '.join(f'{d:g}' for d in g.distances)} | {g.text} |"
              for g in GOALS]

    lines += ["", "## Summary per goal", "",
              "Medians are over the finished repeats. \"Weakest points past the bar\" and \"Score\" are for searches "
              "that met the goal: how far the winning design's weakest reduce objective got past its target, and "
              "the 50/50 blended score (half the weakest objective, half their average).", "",
              "| Goal | Met goal | Rounds | Candidates | Rejected | Simulations | Minutes | AI share of time | "
              "Weakest points past the bar | Score |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for g in GOALS:
        rs = [r for r in results if r["goal"] == g.key and r["status"] != "benchmark_error"]
        if not rs:
            lines.append(f"| {g.key} | no results yet | | | | | | | | |")
            continue
        met = [r for r in rs if r["goal_met"]]
        total_candidates = sum(r["candidates"] for r in rs)
        rejected = sum(r["rejected"] for r in rs)
        llm, femm = sum(r["llm_seconds"] for r in rs), sum(r["femm_seconds"] for r in rs)
        lines.append(
            f"| {g.key} | {len(met)} of {len(rs)} | {_median([r['rounds'] for r in rs], 0)} | "
            f"{_median([r['candidates'] for r in rs], 0)} | "
            f"{rejected} of {total_candidates} | {_median([r['simulations'] for r in rs], 0)} | "
            f"{_median([r['minutes'] for r in rs])} | {(llm / (llm + femm) if llm + femm else 0):.0%} | "
            f"{_median([min(points_past_bar(r['best'])) for r in met if points_past_bar(r['best'])], 2)} | "
            f"{_median([blended_score(r['best']) for r in met], 2)} |")

    lines += ["", "## Every search", "",
              "| Goal | Repeat | Ended as | Rounds | First pass in round | Candidates | Rejected | Minutes | "
              "AI failures | Fields changed per candidate | Weakest points past the bar |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r["status"] == "benchmark_error":
            lines.append(f"| {r['goal']} | {r['repeat']} | benchmark error: {r.get('error', '')[:80]} | | | | | | | | |")
            continue
        points = points_past_bar(r["best"]) if r["best"] else []
        lines.append(
            f"| {r['goal']} | {r['repeat']} | {r['status']}{' (' + r['stop_reason'] + ')' if r.get('stop_reason') else ''} | {r['rounds']} | {r['first_pass_round'] or '-'} | "
            f"{r['candidates']} | {r['rejected']} | {r['minutes']} | {r['llm_failures']} | "
            f"{r['mean_fields_changed'] if r['mean_fields_changed'] is not None else '-'} | "
            f"{f'{min(points):+.2f}' if points else '-'} |")

    lines += ["", "## Why proposals were rejected", ""]
    reasons: dict[str, int] = {}
    for r in results:
        for rd in r.get("rounds_detail", []):
            for c in rd["candidates"]:
                if not c["valid"]:
                    key = (c["error"] or "unknown")[:90]
                    reasons[key] = reasons.get(key, 0) + 1
    if reasons:
        lines += ["| Count | Reason (first 90 characters) |", "|---|---|"]
        lines += [f"| {n} | `{reason}` |" for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1])]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--goals", default="", help="comma-separated goal keys (default: all)")
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--report", action="store_true", help="only rewrite docs/summarize_result.md")
    args = parser.parse_args()
    if not args.report:
        run(args.label, {k for k in args.goals.split(",") if k}, args.repeats)
    # Everything from NOTES_MARKER down is written by hand and kept across regenerations.
    old = REPORT_PATH.read_text() if REPORT_PATH.exists() else ""
    notes = old[old.index(NOTES_MARKER):] if NOTES_MARKER in old else f"{NOTES_MARKER}\n\n## Findings\n\nNot written yet.\n"
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)  # docs/ is not in the repo
    REPORT_PATH.write_text(report(args.label) + "\n" + notes)
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
