"""agent_candidate_card._metric_rows() - the "Result vs. goal" table's rows,
for both tool_result shapes it can be handed: the objective-list shape
every run writes now, and the older fixed M-gate + 3x ESR shape still
sitting in the history database. Pure data in, rows out - no window needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from gui.panels.agent_candidate_card import _metric_rows, _step_outcome


def test_rows_come_from_the_objective_list_without_a_spec():
    tool_result = {
        "valid": True, "goal_met": False,
        "objectives": [
            {"metric": "L_loop", "kind": "match", "target_pct": -10.0, "tolerance_pct": 1.0, "required": True,
             "requirement": "within +/-1% of -10% vs baseline",
             "change_pct_by_distance": {"3.0": -9.6, "12.0": -8.7}, "worst_change_pct": -8.7,
             "passed": False, "margin": -0.3},
            {"metric": "rc_esr", "kind": "reduce", "target_pct": 0.0, "tolerance_pct": 0.0, "required": False,
             "requirement": "no worse than baseline",
             "change_pct_by_distance": {"3.0": 1.0, "12.0": 1.2}, "worst_change_pct": 1.2,
             "passed": False, "margin": -1.2},
        ],
    }
    rows = _metric_rows(tool_result, {})
    assert [r["metric"] for r in rows] == ["L_loop", "rc_esr"]
    assert rows[0]["change_text"] == "-8.7% (worst distance)"
    assert rows[1]["requirement"] == "no worse than baseline (preference)"

    outcome, _ = _step_outcome({"valid": True, "tool_result": tool_result}, {})
    assert outcome == "Missed: L_loop", "an unmet preference isn't a miss"


def test_legacy_runs_still_render_from_their_stored_spec():
    tool_result = {
        "valid": True, "primary_pass": True, "goal_met": False,
        "metric_error_pct_by_distance": {"M23": {"3.0": 1.0, "5.0": -2.0}},
        "loop_esr_change_pct": -12.0, "tank_esr_change_pct": -8.0, "rc_esr_change_pct": -15.0,
    }
    spec = {"primary_metrics": {"M23": 5.0}, "min_esr_improvement_pct": 10.0}
    rows = _metric_rows(tool_result, spec)
    assert [(r["metric"], r["passed"]) for r in rows] == [
        ("M23", True), ("loop_esr", True), ("tank_esr", False), ("rc_esr", True),
    ]
