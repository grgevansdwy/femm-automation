"""Thin CLI entry point for the agent's optimize loop. Builds an AgentSpec
from the fixed test objective in agent/config.py (baseline, distances,
tolerances; no interview phase - see
agent/input_chat/services/interview.py for that, once it exists), constructs a real JobRunner
so solves are cached exactly like a manual run in the GUI, and polls
AgentRunner until the run finishes, printing each round as it completes.

Run:  cd backend && uv run python -m agent
Docs/data: backend/agent/docs/{hard_requirement_run,latest_run,free_variables}.md
"""

import sys
import time
from pathlib import Path

# frontend/job_runner.py is reused as-is for caching/concurrency (agent solves go
# through the same cache as manual runs) rather than duplicating it - this
# is the one place agent/ reaches into frontend/, and only for this CLI wrapper.
# agent/femm_agent/services/runner.py and agent/femm_agent/services/optimize.py never import frontend themselves - they
# only depend on the duck-typed SolveJobRunner shape (agent/femm_agent/services/runner.py).
_GUI_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
if str(_GUI_SRC) not in sys.path:
    sys.path.insert(0, str(_GUI_SRC))

from frontend.job_runner import JobRunner  # noqa: E402

from agent.femm_agent.services.runner import AgentRunner  # noqa: E402
from agent.femm_agent.services.scorer import objective_summary, tool_result  # noqa: E402
from agent.input_chat.models.spec import AgentSpec  # noqa: E402
from agent.femm_agent.models.state import AgentRunStatus, RoundStatus  # noqa: E402
from agent.input_chat.models.variables import allowed_fields, audit_changed_fields  # noqa: E402
from agent.settings.settings import load_settings  # noqa: E402


def main():
    spec = AgentSpec.from_settings(load_settings())
    print(f"Baseline: {spec.baseline_config_id}. Simulating at {spec.distances} mm...")

    job_runner = JobRunner()
    state = AgentRunner(job_runner).submit(spec)

    while not state.rounds and state.status == AgentRunStatus.RUNNING:
        time.sleep(0.1)  # wait for the baseline solve to land before printing anything about it
    if state.status == AgentRunStatus.FAILED:
        print(f"Failed before the first round:\n{state.outcome_report or state.error}")
        return

    printed = 0
    while state.status == AgentRunStatus.RUNNING or printed < len(state.rounds):
        while printed < len(state.rounds) and state.rounds[printed].status == RoundStatus.DONE:
            round_state = state.rounds[printed]
            for c in round_state.candidates:
                if c.valid:
                    r = c.tool_result
                    print(f"[{round_state.round_num}] {c.delta} -> goal_met={r['goal_met']}: {objective_summary(r)}")
                else:
                    print(f"[{round_state.round_num}] {c.delta} -> INVALID: {c.error}")
            if not round_state.candidates:
                print(f"[{round_state.round_num}] {round_state.reasoning_text!r}")
            printed += 1
        if state.status == AgentRunStatus.RUNNING and printed >= len(state.rounds):
            time.sleep(0.1)

    print(f"\nRun finished: {state.status.value} after {len(state.rounds)} round(s).")
    if state.outcome_report:
        print(state.outcome_report)

    print("\n=== Best candidate found ===")
    if state.best is None:
        print("No valid candidate was ever proposed.")
        return

    best = state.best
    audit = audit_changed_fields(state.baseline_coilset, best.coilset,
                                 allowed_fields(state.baseline_coilset, spec.variable_spec))
    print(f"goal_met={best.goal_met} used {len(state.rounds)}/{spec.max_tool_calls} tool calls")
    for o in tool_result(best, spec)["objectives"]:
        where = " (worst distance)" if o["change_pct_by_distance"] else ""
        print(f"  {'PASS' if o['passed'] else 'FAIL'}  {o['metric']:<20} {o['value']:<12g} {o['worst_change_pct']:+.2f}%"
              f"{where}  {o['requirement']}{'' if o['required'] else '  [preference]'}")
    print("Fields changed from baseline (old -> new):")
    for k, (old, new) in audit["changed_fields"].items():
        print(f"  {k}: {old} -> {new}{'   <-- OUTSIDE the allowed set' if k in audit['unexpected_fields'] else ''}")
    if audit["unexpected_fields"]:
        print(f"Structural check: FAILED - changed outside the allowed set: {sorted(audit['unexpected_fields'])}")
    else:
        print("Structural check: no out-of-scope fields changed.")


if __name__ == "__main__":
    main()
