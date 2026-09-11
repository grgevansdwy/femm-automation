"""Exercises AgentPanelState directly - the same calls the AI Agent mode's
chat/progress panels make, without needing to click a real window. Same
"real backend, no mocks" convention as test_runs_panel.py: a real interview
turn against the real LLM server, a real handoff into AgentRunner, real
FEMM solves via the shared JobRunner.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import gui._sim_path  # noqa: F401 - adds coil_simulation to sys.path

from gui.job_runner import JobRunner
from gui.panels.agent_panel import AgentPanelState


def _poll_until(predicate, timeout=300, interval=0.1):
    deadline = time.time() + timeout
    while not predicate():
        if time.time() > deadline:
            raise TimeoutError("condition not met before timeout")
        time.sleep(interval)


def test_interview_hands_off_to_a_real_optimize_run_only_after_confirmation():
    state = AgentPanelState(JobRunner())

    # Picking a config opens the chat immediately - no separate Start
    # click, and no opening LLM call (the model would otherwise reply with
    # a long list of clarifying questions before the user has said anything).
    state.select_config("base_coil")
    assert state.interview_state is not None
    assert not state.interview_state.transcript, "no opening turn should fire on its own"
    assert not state.interview_runner.busy

    state.chat_input = (
        "Match tank-to-RC mutual inductance within 5% of baseline, and reduce ESR on all "
        "three coils by at least 10% each. Evaluate at 5 and 10mm. Keep ferrite and shields "
        "exactly as they already are on this baseline - don't change them."
    )
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy)

    if not state.interview_state.finalized:
        # A second reply in case the model asked one more clarifying question.
        state.chat_input = "That's everything - please propose the plan now."
        state.send_chat()
        _poll_until(lambda: not state.interview_runner.busy)

    assert state.interview_state.finalized, state.interview_state.transcript
    assert state.interview_state.spec is not None

    # A finalized plan alone must NOT start the search - poll() is what
    # main.py calls every frame, and it must stay a no-op until the human
    # explicitly confirms.
    for _ in range(5):
        state.poll()
    assert state.run_state is None, "the search must not start before explicit confirmation"
    assert not state.plan_confirmed

    state.confirm_plan()
    state.poll()
    assert state.run_state is not None
    assert state.run_state.id == state.interview_runner.ai_run_id, "both phases must share one ai_run id"

    # 300s was too tight - a real diagnostic run (see docs/gui_agent_plan.md)
    # converged correctly in 281s with individual rounds ranging 23-67s,
    # confirming this is genuine LLM latency variance, not a stall. 600s
    # gives real headroom without masking an actual hang.
    _poll_until(lambda: state.run_state.status.value != "running", timeout=600)
    assert len(state.run_state.rounds) >= 1

    # The whole session (interview + optimize) must be persisted under that
    # one ai_run id, replayable from the history repository agent_history.py
    # reads. AgentRunner._persist() runs in the same thread's `finally`
    # right after status flips in memory, but isn't instantaneous (writes
    # one row per round plus the final status) - poll for it to actually
    # land rather than assuming the DB write races won.
    from repositories.ai_run_repository import get_ai_run, list_messages

    _poll_until(lambda: (get_ai_run(state.run_state.id) or {}).get("status") == state.run_state.status.value, timeout=30)
    record = get_ai_run(state.run_state.id)
    assert record is not None
    assert record["spec"]["baseline_config_id"] == "base_coil"

    messages = list_messages(state.run_state.id)
    phases = {m["phase"] for m in messages}
    assert "interview" in phases
    assert "optimize" in phases
