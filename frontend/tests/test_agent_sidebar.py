"""The AI Agent screen's sidebar of saved chats, exercised through
AgentSidebarState and AgentPanelState - the same calls the screen makes,
without a window. No LLM server and no FEMM: the interview and search AIs
are scripted and every solve is instant (the helpers test_agent_panel.py
uses for the run lifecycle), on the isolated database from conftest.py.
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from frontend.screen_agent import agent_sidebar
from frontend.screen_agent.agent_panel import AgentPanelState, _chat_ending
from test_agent_panel import _InstantSolver, _finished, _planned_state, _poll_until, _proposal, _start, _wait_for_abort


def _stored_run(status, baseline_config_id="base_coil", said="cut tank ESR", spec=None, rounds=1):
    """A run written straight to the database, as an earlier app session left it."""
    from agent.input_chat.models.objective import Objective
    from agent.input_chat.models.spec import AgentSpec
    from agent.repositories import ai_run_repository as ar
    from engine.repositories.coilset_repository import get_coilset

    run_id = ar.create_ai_run(baseline_config_id)["id"]
    ar.append_message(run_id, phase="interview", seq=0, role="user", content=said, payload={"role": "user", "content": said})
    if status == "interviewing":
        return run_id
    if spec is None:
        spec = dataclasses.asdict(dataclasses.replace(
            AgentSpec(baseline_config_id=baseline_config_id), distances=[5.0], objective_text=f"goal of {said}",
            objectives=[Objective(metric="tank_esr", kind="reduce", tolerance_pct=50.0)]))
    ar.finalize_spec(run_id, spec)
    if status == "plan_ready":
        return run_id
    try:
        baseline = dataclasses.asdict(get_coilset(baseline_config_id))
    except KeyError:
        baseline = None  # a run from before the baseline was copied into it, whose config is gone
    ar.transition(run_id, "running", started_at=ar._now_iso(), baseline_coilset=baseline)
    for n in range(1, rounds + 1):
        ar.append_message(run_id, phase="optimize", seq=n, role="assistant", round_num=n, content=f"round {n}",
                          payload={"candidates": []})
    if status == "interrupted":
        ar.reap_running()  # what the app does at its next start
    elif status != "running":
        ar.transition(run_id, status, finished_at=ar._now_iso(), error="Target not achieved.")
    return run_id


def test_the_list_shows_every_status_names_each_chat_and_search_narrows_it():
    from agent.repositories import ai_run_repository as ar
    from engine.repositories.coilset_repository import get_config, restore_config

    assert set(agent_sidebar.STATUS_LABELS) == set(ar.STATUSES), "every status has a label"
    assert len({label for label, _ in agent_sidebar.STATUS_LABELS.values()}) == len(ar.STATUSES)

    restore_config({**get_config("base_coil"), "id": "cfg_other", "name": "Receiver v3"})
    ids = {status: _stored_run(status, said=f"chat about {status}") for status in ar.STATUSES}
    ar.create_ai_run("base_coil")  # a blank row, as versions before the run lifecycle left behind
    other = _stored_run("interviewing", baseline_config_id="cfg_other", said="  shrink the\nreceiver  ")

    sidebar = agent_sidebar.AgentSidebarState()
    rows = sidebar.visible_rows()
    assert {r["id"] for r in rows} == set(ids.values()) | {other}, "every status is listed; only the blank row is not"
    assert [r["created_at"] for r in rows] == sorted((r["created_at"] for r in rows), reverse=True), "newest first"
    titles = {r["id"]: agent_sidebar.chat_title(r) for r in rows}
    assert titles[ids["interviewing"]] == "chat about interviewing", "no plan yet: the first message"
    assert titles[ids["goal_met"]] == "goal of chat about goal_met", "with a plan: its goal text"
    assert titles[other] == "shrink the receiver", "one line"

    names = {"base_coil": "base_coil", "cfg_other": "Receiver v3"}
    for query, expected in [("GOAL_MET", {ids["goal_met"]}), ("receiver v3", {other}), ("shrink", {other}),
                            (ids["stalled"][:8], {ids["stalled"]}), ("no such chat", set())]:
        sidebar.search_text = query
        assert {r["id"] for r in sidebar.visible_rows(names)} == expected, query


def test_new_saves_nothing_and_an_empty_chat_is_gone_once_another_is_opened():
    from agent.repositories.ai_run_repository import list_ai_runs

    saved = _stored_run("interviewing")
    state = AgentPanelState(_InstantSolver())
    state.new_chat()
    assert state.phase == "none", "no baseline picked yet"
    state.select_config("base_coil")
    state.new_chat()
    assert state.phase == "interview" and state.selected_config_id == "base_coil", "New keeps the baseline"
    assert [r["id"] for r in list_ai_runs()] == [saved], "no row for a chat nothing was said in"

    empty = state.chat
    state.open_chat(saved)
    assert state.chat is not empty and all(chat is not empty for chat in state._all_chats())
    assert state.chat.ai_run_id == saved and [m["content"] for m in state.interview_state.transcript] == ["cut tank ESR"]


def test_a_finished_chat_reopens_without_a_solve_and_can_be_asked_and_continued(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    first, calls = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n), max_rounds=2)
    _start(first)
    row = _finished(first)
    run_id = row["id"]
    assert (row["status"], row["stop_reason"]) == ("budget_exhausted", "rounds")

    # A later app session: nothing of that run is in memory.
    solver = _InstantSolver()
    state = AgentPanelState(solver)
    state.select_config("base_coil")
    before = solver.submitted
    state.open_chat(run_id)
    state.poll()
    assert solver.submitted == before, "opening a finished chat simulates nothing"

    chat = state.chat
    assert state.interview_state.transcript == first.interview_state.transcript
    assert state.search_markers == first.search_markers, "the search line sits where Start Search was clicked"
    assert state.phase == "finished" and state.run_state is None and not chat.live and state.baseline_locked
    assert _chat_ending(chat) == ("Search finished: target not achieved within the budget after 2 round(s) (round limit).",
                                  row["error"])
    assert [m["round_num"] for m in agent_sidebar.stored_rounds(chat.messages)] == [1, 2]
    assert agent_sidebar.has_stored_summary(chat.record, chat.messages), "its best design and summary are there to draw"

    # A question: the run is rebuilt in the background, then the follow-up AI answers.
    monkeypatch.setattr("agent.femm_agent.services.followup.call_llm",
                        lambda messages, tools, **kwargs: {"role": "assistant", "content": "Two rounds."})
    state.chat_input = "How many rounds?"
    state.send_chat()
    _poll_until(lambda: not chat.restoring and not state.interview_runner.busy
                and state.interview_state.transcript[-1].get("content") == "Two rounds.", timeout=10)
    assert state.run_state is not None and len(state.run_state.rounds) == 2 and not chat.live
    assert chat.read_only_reason is None and get_ai_run(run_id)["status"] == "budget_exhausted"
    assert [m["content"] for m in list_messages(run_id, phase="interview")][-2:] == ["How many rounds?", "Two rounds."]

    # "continue": rounds 3 and 4 are appended to the same run.
    state.chat_input = "continue"
    state.send_chat()
    _poll_until(lambda: chat.live, timeout=10)
    row = _finished(state)
    assert len(state.run_state.rounds) == 4 and [b["segment"] for b in row["budget"]] == [1, 2]
    assert [m["seq"] for m in list_messages(run_id, phase="optimize")] == [1, 2, 3, 4]
    assert "'tank_center_r': 71.0" in calls[2][2]["content"], "the model still knows what was tried before"
    assert len(state.search_markers) == 2

    # And a session after that finds both search lines again.
    again = AgentPanelState(_InstantSolver())
    again.open_chat(run_id)
    assert again.search_markers == state.search_markers
    assert [m["round_num"] for m in agent_sidebar.stored_rounds(again.chat.messages)] == [1, 2, 3, 4]


def test_a_plan_that_was_never_started_reopens_with_its_start_search(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run

    first, _ = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n))
    run_id = first.interview_runner.ai_run_id

    state = AgentPanelState(_InstantSolver())
    state.open_chat(run_id)
    assert state.phase == "interview" and state.interview_state.finalized and not state.plan_confirmed
    assert state.interview_state.spec.objective_text == "cut tank ESR in half" and not state.baseline_locked
    assert get_ai_run(run_id)["status"] == "plan_ready", "opening it changed nothing"

    _start(state)
    row = _finished(state)
    assert row["id"] == run_id and row["status"] == "budget_exhausted" and state.chat.live


def test_cancelled_and_interrupted_chats_open_with_their_finished_rounds():
    state = AgentPanelState(_InstantSolver())
    for status, ending in [("cancelled", "Search finished: stopped by you after 2 round(s)."),
                           ("interrupted", "Search finished: interrupted after 2 round(s).")]:
        run_id = _stored_run(status, rounds=2)
        state.open_chat(run_id)
        assert state.phase == "finished" and state.chat.read_only_reason is None, status
        assert _chat_ending(state.chat)[0] == ending
        assert [m["content"] for m in agent_sidebar.stored_rounds(state.chat.messages)] == ["round 1", "round 2"]
        assert state.search_markers == [1], "after the one message that came before Start Search"


def test_a_chat_that_cannot_be_rebuilt_opens_read_only_and_says_why():
    solver = _InstantSolver()
    state = AgentPanelState(solver)
    no_baseline = _stored_run("cancelled", baseline_config_id="deleted_coil")
    bad_plan = _stored_run("plan_ready", spec={"objectives": "not a list"})

    state.open_chat(no_baseline)
    assert "deleted_coil" in state.chat.read_only_reason and "no longer exists" in state.chat.read_only_reason
    assert [m["content"] for m in state.interview_state.transcript] == ["cut tank ESR"], "it can still be read"
    state.chat_input = "continue"
    state.send_chat()
    assert state.chat_input == "continue" and not state.chat.restoring and not state.interview_runner.busy
    assert not state.continue_search() and state.run_state is None

    state.open_chat(bad_plan)
    assert "stored plan" in state.chat.read_only_reason and not state.interview_state.finalized
    state.confirm_plan()
    state.poll()
    assert state.run_state is None and solver.submitted == 0


def test_one_search_at_a_time_and_a_running_search_survives_looking_elsewhere(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run

    state, calls = _planned_state(monkeypatch, _wait_for_abort)
    first_id, first_chat = state.interview_runner.ai_run_id, state.chat

    state.new_chat()  # a second plan, made while the first one waits
    state.chat_input = "Cut tank ESR in half at 5 mm, again."
    state.send_chat()
    _poll_until(lambda: not state.interview_runner.busy, timeout=10)
    state.poll()
    second_id = state.interview_runner.ai_run_id
    assert state.interview_state.finalized and second_id != first_id

    state.open_chat(first_id)
    assert state.chat is first_chat
    run_state = _start(state)
    _poll_until(lambda: run_state.rounds, timeout=10)

    # Looking at the other chat does not touch the search...
    state.open_chat(second_id)
    state.poll()
    assert not run_state.cancelled and get_ai_run(first_id)["status"] == "running"
    assert state.running_chat is first_chat and state.phase == "interview"
    # ...and that chat cannot start a second one.
    assert "Another search is running" in state.search_busy_elsewhere
    state.confirm_plan()
    state.poll()
    assert state.run_state is None and not state.plan_confirmed and "Another search is running" in state.error
    assert not state.delete_chat(first_id) and get_ai_run(first_id) is not None, "a running search is never deleted"

    state.open_chat(first_id)
    assert state.chat is first_chat and state.run_state is run_state and state.phase == "running"
    state.stop_search()
    assert _finished(state)["status"] == "cancelled"

    # Once it has ended, the other chat's Start Search works.
    state.open_chat(second_id)
    assert state.search_busy_elsewhere is None
    second_run = _start(state)
    assert second_run.id == second_id
    state.stop_search()
    _finished(state)


def test_delete_removes_the_chat_and_leaves_a_new_one_open(monkeypatch):
    from agent.repositories.ai_run_repository import get_ai_run, list_messages

    keep = _stored_run("goal_met")
    state, _ = _planned_state(monkeypatch, lambda n, kwargs: _proposal(n))
    _start(state)
    run_id = _finished(state)["id"]
    state.sidebar.refresh()
    assert {r["id"] for r in state.sidebar.visible_rows()} == {keep, run_id}

    assert state.delete_chat(run_id)
    assert get_ai_run(run_id) is None and list_messages(run_id) == []
    assert state.chat.ai_run_id is None and state.phase == "interview" and state.selected_config_id == "base_coil"
    assert run_id not in state._sessions and run_id not in state.agent_runner.runs
    assert [r["id"] for r in state.sidebar.visible_rows()] == [keep], "the list is re-read at once"

    assert state.delete_chat(keep) and not state.delete_chat(keep), "a chat that is not open; then nothing left to delete"
    assert state.sidebar.visible_rows() == []
