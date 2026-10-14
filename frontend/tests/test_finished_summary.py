"""The chat reply that closes a search is plain words from the best design's numbers:
no scores or points, what was achieved, what changed, and an invitation to ask."""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from engine.repositories.coilset_repository import get_coilset
from frontend.screen_agent.finished_summary import summary_text


def _objective(metric, kind, worst, passed, requirement, required=True, tolerance=0.0, target=0.0):
    return {"metric": metric, "kind": kind, "required": required, "passed": passed, "worst_change_pct": worst,
            "requirement": requirement, "tolerance_pct": tolerance, "target_pct": target, "target_value": None,
            "points": 99.0, "margin": 9.9}


def _result(goal_met, *objectives):
    return {"valid": True, "goal_met": goal_met, "objectives": list(objectives)}


def test_a_search_that_met_its_goal_says_so_in_plain_words():
    base = get_coilset("base_coil")
    best = dataclasses.replace(base, num_tank_turns=base.num_tank_turns + 1)
    result = _result(True,
                     _objective("tank_esr", "reduce", -27.3, True, ">= 10% lower", tolerance=10.0),
                     _objective("M12", "match", 1.5, True, "within +/-5% of baseline", tolerance=5.0),
                     _objective("M23", "match", -4.1, True, "within +/-5% of baseline", tolerance=5.0))
    text = summary_text("goal_met", "plateau", result, base, best)
    assert text.startswith("Search finished. Best coil design found!")
    assert "tank_esr is 27.3% lower" in text and "M12, M23 stay within 4.1% of baseline" in text
    assert "Number of Tank Turns" in text and f"{base.num_tank_turns:g} to {base.num_tank_turns + 1:g}" in text
    assert "stopped finding better designs" in text
    assert "Let me know if you have any questions about the run." in text and '"continue"' in text
    assert "point" not in text.lower() and "score" not in text.lower() and "fragility" not in text.lower()


def test_a_search_that_missed_says_which_requirement_and_does_not_claim_a_best_design():
    result = _result(False,
                     _objective("tank_esr", "reduce", -4.0, False, ">= 10% lower", tolerance=10.0),
                     _objective("M23", "match", 1.0, True, "within +/-5% of baseline", tolerance=5.0))
    text = summary_text("budget_exhausted", "rounds", result, None, None)
    assert "Best coil design found" not in text and "No design met every requirement" in text
    assert "tank_esr is 4% lower (needed >= 10% lower)" in text and "M23 stay within 1% of baseline" in text
    assert "used all its rounds" in text


def test_a_read_only_chat_does_not_offer_to_continue_and_no_design_is_said_plainly():
    text = summary_text("failed", None, None, None, None, error="The AI server did not answer.", can_continue=False)
    assert "ended before finding a design. The AI server did not answer." in text
    assert "continue" not in text and text.endswith("Let me know if you have any questions about the run.")
    assert "No valid coil design was found" in summary_text("goal_met", None, None, None, None)


def test_a_long_list_of_changes_is_cut_short():
    base = get_coilset("base_coil")
    best = dataclasses.replace(base, **{n: getattr(base, n) + 1 for n in (
        "num_tank_turns", "num_rc_turns", "num_loop_turns", "tank_center_r", "rc_radius", "loop_r", "loop_offset", "num_tank_layers")})
    text = summary_text("goal_met", "plateau", _result(True, _objective("tank_esr", "reduce", -20.0, True, ">= 10% lower")),
                        base, best)
    assert ", and 2 more." in text
