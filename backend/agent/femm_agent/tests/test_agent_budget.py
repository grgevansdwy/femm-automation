# =============================================================================
# test_agent_budget.py
#
# The search's budget and stop rules, end to end through AgentRunner with a
# scripted fake LLM (no server), a counting fake job runner (no FEMM) and a
# clock the test moves by hand (no sleeping):
# - one scenario per stop reason, each checking status, stop_reason and the row,
# - never-go-wrong N3: no AI request and no solve starts after the time limit,
#   and the simulation limit turns proposals away in proposal order,
# - "continue": a new segment, fresh budget, same memory, same run,
# - the progress verdict (services/progress.py) as a pure function,
# - restore.py rebuilds a stored run without a single fresh solve.
#     python3 -m pytest agent/femm_agent/tests/test_agent_budget.py  (run from backend/)
# =============================================================================
import dataclasses
import time

import pytest

from agent.femm_agent.models.state import Budget, Tried
from agent.femm_agent.services import optimize, progress
from agent.femm_agent.services.restore import RestoreError, restore_run
from agent.femm_agent.services.runner import AgentRunner
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.repositories.ai_run_repository import create_ai_run, get_ai_run, list_messages
from agent.services.llm_client import LLMError
from agent.services.run_log import read_log
from agent.settings.settings import AgentSettings

_METRICS = {"L_loop": 1.9e-7, "L_tank": 4.6e-6, "L_rc": 1.6e-6, "M12": 6e-7, "M13": 1.3e-7, "M23": 6.9e-7,
            "loop_esr": 0.0168, "tank_esr": 0.427, "rc_esr": 0.163, "k12": 0.6, "k13": 0.2, "k23": 0.6}
_BASE_R = 72.688  # base_coil's tank_center_r


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Job:
    error = None

    def __init__(self, result, job_id, status="done"):
        self.result, self.id, self.status = result, job_id, status


class _Jobs:
    """Tank ESR scales with tank_center_r (a smaller tank is better). Every
    solve is remembered, so asking again for the same design is a cache hit.
    `solve_s`: how far each fresh solve moves the test's clock."""

    def __init__(self, clock=None, solve_s=0.0):
        self.clock, self.solve_s = clock, solve_s
        self.cache: dict = {}
        self.submits: list = []  # (tank_center_r, distance, clock time when it was queued)

    def result(self, coilset):
        return {**_METRICS, "tank_esr": _METRICS["tank_esr"] * coilset.tank_center_r / _BASE_R}

    def find_cached(self, coilset, distance):
        return self.cache.get((coilset.tank_center_r, distance))

    def touch(self, row):
        return row

    def submit(self, coilset, config_id, config_name, distance):
        self.submits.append((coilset.tank_center_r, distance, self.clock.t if self.clock else None))
        if self.clock is not None:
            self.clock.t += self.solve_s
        result = self.result(coilset)
        self.cache[(coilset.tank_center_r, distance)] = {"result": result}
        return _Job(result, f"job{len(self.submits)}")

    def cancel(self, job_id):
        return False

    def candidate_submits(self):
        return [s for s in self.submits if s[0] != _BASE_R]


def _propose(*radii):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{r}", "type": "function", "function": {"name": "propose_coilset",
                                                         "arguments": {"tank_center_r": r, "rationale": "try it"}}}
        for r in radii]}


def _text(content="Nothing more to try."):
    return {"role": "assistant", "content": content}


def _spec(objectives=None, cut_pct=10.0, distances=(10.0,), per_round=1, **settings):
    """rounds / time / simulations / plateau / stall settings by their AgentSettings names."""
    s = AgentSettings(**settings)
    return dataclasses.replace(
        AgentSpec.from_settings(s, baseline_config_id="base_coil"), distances=list(distances),
        candidates_per_round=per_round,
        objectives=objectives or [Objective(metric="tank_esr", kind="reduce", tolerance_pct=cut_pct)])


def _wait(state):
    deadline = time.monotonic() + 20
    while (get_ai_run(state.id) or {}).get("status") == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return get_ai_run(state.id)


def _search(monkeypatch, script, spec, jobs=None, clock=None, request_s=0.0):
    """Runs one search. script: one entry per AI request - a reply, an
    exception to raise, or a callable(kwargs) returning either; past its end
    the model answers with plain text. Each request moves `clock` by
    `request_s`. Returns (state, row, requests) with requests =
    [(messages, kwargs, clock time when it started)]."""
    requests = []

    def fake_call_llm(messages, tools, **kwargs):
        requests.append((messages, kwargs, clock.t if clock else None))
        if clock is not None:
            clock.t += request_s
        step = script[len(requests) - 1] if len(requests) <= len(script) else _text()
        if callable(step):
            step = step(kwargs)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(optimize, "call_llm", fake_call_llm)
    monkeypatch.setattr(optimize, "RETRY_PAUSE_S", 0.0)
    runner = AgentRunner(jobs or _Jobs(clock), **({"clock": clock} if clock else {}))
    state = runner.submit(spec)
    row = _wait(state)
    state.runner = runner  # for tests that continue it
    return state, row, requests


def _ending(state, row):
    assert row["status"] == state.status.value and row["stop_reason"] == state.stop_reason
    assert row["finished_at"] is not None and row["error"] == state.outcome_report
    return state.status.value, state.stop_reason, len(state.rounds)


# --- one scenario per stop reason ----------------------------------------------------------------

def test_nothing_left_a_match_only_goal_stops_at_the_first_pass(monkeypatch):
    spec = _spec(objectives=[Objective(metric="M23", kind="match", tolerance_pct=5.0)])
    state, row, _ = _search(monkeypatch, [_propose(71.0), _propose(70.0)], spec)
    assert _ending(state, row) == ("goal_met", "nothing_left", 1)
    assert "nothing in the goal can be pushed further" in state.outcome_report


def test_plateau_the_search_goes_on_after_a_pass_until_two_rounds_bring_nothing_better(monkeypatch):
    # 60 passes (17.5% against 10%); 50 is far better (31.2%); then two rounds within solver noise.
    state, row, requests = _search(monkeypatch, [_propose(60.0), _propose(50.0), _propose(50.2), _propose(49.9)], _spec())
    assert _ending(state, row) == ("goal_met", "plateau", 4)
    assert [r.verdict for r in state.rounds] == ["improving", "improving", "improving", "plateau"]
    assert [r.rounds_since_improvement for r in state.rounds] == [0, 0, 1, 2]
    assert state.best.coilset.tank_center_r == 49.9 and row["best_round"] == 4, "the best design wins, not the first pass"
    assert len(requests) == 4, "no request after the plateau"
    assert "2 rounds in a row without a better design" in state.outcome_report
    assert "points past the bar" in state.outcome_report
    assert [m["payload"]["verdict"] for m in list_messages(state.id, phase="optimize")][-1] == "plateau"


def test_plateau_length_comes_from_settings(monkeypatch):
    state, row, _ = _search(monkeypatch, [_propose(60.0)], _spec(plateau_rounds=3, max_tool_calls=10))
    # Text-only replies after the pass: the plateau (3 rounds) can't be reached before the second one ends it.
    assert _ending(state, row) == ("goal_met", "model_stopped", 3)


def test_stalled_the_model_is_told_once_and_the_search_stops_two_rounds_later(monkeypatch):
    spec = _spec(cut_pct=50.0, stall_rounds=2)
    script = [_propose(71.0)] + [_propose(71.0 + n / 10) for n in range(1, 9)]  # never closer than round 1
    state, row, requests = _search(monkeypatch, script, spec)

    assert _ending(state, row) == ("stalled", "stalled", 5), "2 rounds to stall, the notice, 2 more rounds"
    notices = [any(m["role"] == "user" and m["content"].startswith("No improvement in 2 rounds.") for m in messages)
               for messages, _, _ in requests]
    assert notices == [False, False, False, True, False], "sent once, with the request after it stalled"
    notice = requests[3][0][-1]["content"]
    assert "Fields not tried yet:" in notice and "rc_radius" in notice and "tank_center_r" not in notice
    for word in ("impossible", "one value", "one change", "several"):
        assert word not in notice

    report = state.outcome_report
    assert report.startswith("Target not achieved: the search stopped early")
    assert "4 were near-copies" in report and "Fields the search never changed:" in report
    assert "impossible" not in report and "continue" not in report
    assert [r.verdict for r in state.rounds] == ["improving", "improving", "stalled", "stalled", "stalled"]


def test_rounds_the_limit_counts_every_reply_text_only_included(monkeypatch):
    state, row, requests = _search(monkeypatch, [_propose(71.0), _text("Let me think."), _propose(70.0), _propose(69.0)],
                                   _spec(cut_pct=50.0, max_tool_calls=3))
    assert _ending(state, row) == ("budget_exhausted", "rounds", 3) and len(requests) == 3
    assert row["budget"] == [{"segment": 1, "max_rounds": 3, "time_limit_s": 5400.0, "max_simulations": 300,
                              "rounds_used": 3, "simulations_used": 2, "cache_hits": 0,
                              "seconds_used": row["budget"][0]["seconds_used"]}]


def test_model_stopped_two_text_only_replies_in_a_row(monkeypatch):
    state, row, requests = _search(monkeypatch, [_propose(71.0), _text(), _text()], _spec(cut_pct=50.0))
    assert _ending(state, row) == ("stopped_by_model", "model_stopped", 3)
    assert requests[2][0][-1]["content"].startswith("Continue - call propose_coilset"), "one nudge before it counts"
    assert state.outcome_report.startswith("Target not achieved: the AI stopped proposing")


def test_simulations_proposals_launch_in_order_while_the_count_allows(monkeypatch):
    jobs = _Jobs()
    # Two distances: every design costs 2 simulations. Limit 5: the third design of round 2 does not fit.
    spec = _spec(cut_pct=50.0, distances=(5.0, 10.0), per_round=3, max_simulations=5)
    state, row, requests = _search(monkeypatch, [_propose(71.0), _propose(71.0, 70.0, 69.0, 68.0)], spec, jobs=jobs)

    assert _ending(state, row) == ("budget_exhausted", "simulations", 2) and len(requests) == 2
    assert [s[0] for s in jobs.candidate_submits()] == [71.0, 71.0, 70.0, 70.0], "69 and 68 were never queued"
    round_2 = state.rounds[1].candidates
    assert [c.valid for c in round_2] == [True, True, False, False]
    assert round_2[2].error_code == "simulation_limit" and round_2[2].error.startswith("Not simulated")
    assert round_2[3].error_code == "over_candidate_limit"
    assert (state.budget.simulations_used, state.budget.cache_hits) == (4, 2), \
        "71 again in round 2 was a cache hit: free. The baseline's two solves are not counted either."
    assert len(jobs.submits) == 6
    assert "its limit of 5 simulations" in state.outcome_report
    assert [t.delta["tank_center_r"] for t in state.tried] == [71.0, 71.0, 70.0], "a proposal turned away is not remembered as tried"


# --- N3: every search ends within its budget -----------------------------------------------------

def test_time_no_ai_request_starts_after_the_limit_and_each_gets_only_the_time_left(monkeypatch):
    clock = _Clock()
    cut_short = lambda kwargs: LLMError("too_slow", "", elapsed_s=kwargs["time_limit_s"], tokens=500,  # noqa: E731
                                        time_limit_s=kwargs["time_limit_s"])
    # Each request takes 40 minutes of a 90-minute search: the third has 10 minutes left and is cut off by them.
    state, row, requests = _search(monkeypatch, [_propose(71.0), _propose(70.5), cut_short, _propose(60.0)],
                                   _spec(cut_pct=50.0), clock=clock, request_s=2400.0)

    assert _ending(state, row) == ("budget_exhausted", "time", 3)
    assert [started for _, _, started in requests] == [0.0, 2400.0, 4800.0], "nothing started at or after 5400 s"
    assert [kwargs["time_limit_s"] for _, kwargs, _ in requests] == [900.0, 900.0, 600.0]
    assert state.llm_failures == [] and state.search_attempt == 1, "cut off by the search's limit: not a failed try"
    assert state.rounds[2].no_reply and not state.rounds[2].candidates
    assert "90-minute time limit" in state.outcome_report


def test_time_no_solve_is_queued_once_the_limit_has_passed(monkeypatch):
    clock = _Clock()
    jobs = _Jobs(clock)
    # The reply to round 2 arrives after the limit: its proposals are turned away, nothing is solved.
    state, row, requests = _search(monkeypatch, [_propose(71.0), _propose(70.0, 69.0)],
                                   _spec(cut_pct=50.0, per_round=2), jobs=jobs, clock=clock, request_s=3000.0)

    assert _ending(state, row) == ("budget_exhausted", "time", 2)
    assert all(queued_at < 5400.0 for _, _, queued_at in jobs.submits)
    assert [s[0] for s in jobs.candidate_submits()] == [71.0]
    assert [c.error_code for c in state.rounds[1].candidates] == ["time_limit"] * 2
    assert state.budget.simulations_used == 1


def test_time_slow_solves_end_the_search_after_the_round_they_finish_in(monkeypatch):
    clock = _Clock()
    jobs = _Jobs(clock, solve_s=3000.0)
    state, row, requests = _search(monkeypatch, [_propose(71.0), _propose(70.0), _propose(69.0)],
                                   _spec(cut_pct=50.0), jobs=jobs, clock=clock)
    # Baseline 0-3000 s, round 1's solve 3000-6000 s: past 5400 s, so no second round.
    assert _ending(state, row) == ("budget_exhausted", "time", 1) and len(requests) == 1
    assert all(queued_at < 5400.0 for _, _, queued_at in jobs.submits)


def test_time_solves_still_queued_when_the_limit_passes_are_dropped_not_started(monkeypatch):
    clock = _Clock()

    class _QueueJobs(_Jobs):
        """One worker: the first solve of a round is running, the rest wait in the queue."""

        def __init__(self):
            super().__init__(clock)
            self.jobs, self.cancelled = [], []

        def submit(self, coilset, config_id, config_name, distance):
            job = super().submit(coilset, config_id, config_name, distance)
            if coilset.tank_center_r != _BASE_R:
                job.status = "running" if not self.jobs else "queued"
                self.jobs.append(job)
            return job

        def cancel(self, job_id):
            job = next(j for j in self.jobs if j.id == job_id)
            if job.status != "queued":
                return False
            job.status, job.error = "failed", "cancelled"
            self.cancelled.append(job_id)
            return True

    jobs = _QueueJobs()
    real_sleep = time.sleep

    def fake_sleep(seconds):  # the loop is waiting on the running solve: time passes, it finishes
        if jobs.jobs and jobs.jobs[0].status == "running":
            clock.t = 6000.0
            jobs.jobs[0].status = "done"
        real_sleep(0)

    monkeypatch.setattr("agent.femm_agent.services.scorer.time.sleep", fake_sleep)
    state, row, _ = _search(monkeypatch, [_propose(71.0, 70.0, 69.0)], _spec(cut_pct=50.0, per_round=3),
                            jobs=jobs, clock=clock)

    assert _ending(state, row) == ("budget_exhausted", "time", 1)
    assert len(jobs.cancelled) == 2, "the two solves that had not started were dropped"
    candidates = state.rounds[0].candidates
    assert [c.valid for c in candidates] == [True, False, False], "the one already running was waited out and kept"
    assert all(c.error_code == "time_limit" for c in candidates[1:])
    assert state.budget.simulations_used == 1, "a dropped solve never ran, so it is not charged"


def test_budget_arithmetic():
    clock = _Clock()
    budget = Budget(max_rounds=2, time_limit_s=100.0, max_simulations=3, clock=clock)
    assert budget.remaining_s() == 100.0 and budget.exhausted() is None, "the clock starts with start()"
    budget.start()
    clock.t = 99.0
    assert budget.exhausted() is None and budget.remaining_s() == pytest.approx(1.0)
    clock.t = 100.0
    assert budget.exhausted() == "time"
    clock.t = 0.0
    budget.simulations_used = 3
    assert budget.exhausted() == "simulations" and budget.simulations_left() == 0
    budget.rounds_used = 2
    assert budget.exhausted() == "rounds", "rounds are named first when several limits are reached"
    clock.t = 40.0
    budget.stop()
    clock.t = 500.0
    assert budget.elapsed_s() == 40.0 and budget.as_dict(1)["seconds_used"] == 40.0, "a finished segment stops the clock"


# --- "continue": a new segment of the same run ---------------------------------------------------

def test_continue_appends_rounds_to_the_same_run_with_memory_intact_and_a_fresh_budget(monkeypatch):
    spec = _spec(cut_pct=15.0, max_tool_calls=2)
    state, row, requests = _search(monkeypatch, [_propose(71.0), _propose(70.0), _propose(60.0)], spec)
    assert _ending(state, row) == ("budget_exhausted", "rounds", 2)
    assert 'reply "continue"' in state.outcome_report
    runner, first_budget = state.runner, state.budget

    with pytest.raises(ValueError):
        runner.continue_run("no-such-run")
    assert runner.continue_run(state.id) is state
    row = _wait(state)

    assert _ending(state, row) == ("goal_met", "rounds", 4), "same run, rounds 3 and 4 appended"
    assert [m["seq"] for m in list_messages(state.id, phase="optimize")] == [1, 2, 3, 4]
    assert [m["payload"]["segment"] for m in list_messages(state.id, phase="optimize")] == [1, 1, 2, 2]
    resumed = requests[2][0]
    assert [m["role"] for m in resumed] == ["system", "user", "user"]
    assert "'tank_center_r': 71.0" in resumed[2]["content"] and "'tank_center_r': 70.0" in resumed[2]["content"], \
        "everything tried before the continue is in the model's memory"
    assert state.budget is not first_budget and state.budget.rounds_used == 2
    assert [(b["segment"], b["rounds_used"]) for b in row["budget"]] == [(1, 2), (2, 2)]
    assert state.best.coilset.tank_center_r == 60.0 and row["best_round"] == 3
    assert row["baseline_coilset"]["tank_center_r"] == _BASE_R, "still measured against the original baseline"

    events = read_log(state.log.path)
    assert [e["segment"] for e in events if e["event"] == "run_start"] == [1, 2]
    assert [(e["segment"], e["stop_reason"]) for e in events if e["event"] == "run_end"] == [(1, "rounds"), (2, "rounds")]


def test_continue_after_a_plateau_gets_its_own_two_rounds(monkeypatch):
    state, row, requests = _search(monkeypatch, [_propose(60.0)], _spec(max_tool_calls=10))
    assert _ending(state, row)[:2] == ("goal_met", "plateau")
    before = len(state.rounds)
    state.runner.continue_run(state.id)
    row = _wait(state)
    assert _ending(state, row) == ("goal_met", "plateau", before + 2), "the counter restarts with the segment"


# --- the progress verdict, as a pure function ----------------------------------------------------

def _result(points, goal_met=None, preference=None):
    objectives = [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 10.0, "required": True, "points": points}]
    if preference is not None:
        objectives = [{"metric": "M23", "kind": "match", "tolerance_pct": 5.0, "required": True, "points": points},
                      {"metric": "L_rc", "kind": "reduce", "tolerance_pct": 0.0, "required": False, "points": preference}]
    return {"valid": True, "goal_met": points >= 0 if goal_met is None else goal_met, "objectives": objectives}


def _tried(per_round, **kwargs):
    """per_round: one list of results per round (round numbers from 1)."""
    return [Tried(n, {"tank_center_r": 70.0 - n - i / 10}, object(), result, **kwargs)
            for n, results in enumerate(per_round, start=1) for i, result in enumerate(results)]


def _assess(per_round, spec=None, **kwargs):
    spec = spec or _spec()
    return progress.assess(_tried(per_round), list(range(1, len(per_round) + 1)), spec,
                           ["tank_center_r", "rc_radius"], **kwargs)


def test_progress_before_a_pass_tracks_the_closest_design_beyond_noise():
    assert _assess([[_result(-10)], [_result(-8)]]).verdict == "improving"
    crawling = _assess([[_result(-10)], [_result(-9.5)], [_result(-9.2)]])
    assert (crawling.verdict, crawling.rounds_since_improvement, crawling.passed) == ("stalled", 2, False), \
        "0.8 points closer over two rounds is within the 1.0-point noise"
    assert crawling.reference == 10.0
    assert _assess([[_result(-10)], [_result(-12)], [_result(-8.9)]]).rounds_since_improvement == 0
    assert _assess([[], [], []]).verdict == "stalled", "rounds without a single valid design don't count as progress"
    assert _assess([[_result(-10)], [_result(-9.5)], [_result(-9.2)]],
                   spec=_spec(noise_points=0.2)).verdict == "improving", "the threshold is a setting"


def test_progress_after_a_pass_tracks_the_best_score_and_restarts_the_count():
    rounds = [[_result(-10)], [_result(-10)], [_result(-10)], [_result(2)]]
    first_pass = _assess(rounds)
    assert (first_pass.verdict, first_pass.rounds_since_improvement, first_pass.passed) == ("improving", 0, True)
    flat = _assess(rounds + [[_result(2.5)], [_result(2.9)]])
    assert (flat.verdict, flat.rounds_since_improvement, flat.reference) == ("plateau", 2, 2)
    assert _assess(rounds + [[_result(2.5)], [_result(3.5)]]).rounds_since_improvement == 0
    worse = _assess(rounds + [[_result(-3)], [_result(1)]])
    assert worse.verdict == "plateau", "a later round that doesn't beat the best passing design is no improvement"


def test_progress_for_a_match_only_goal_tracks_its_preferences():
    spec = _spec(objectives=[Objective("M23", "match", 5.0), Objective("L_rc", "reduce", 0.0, required=False)])
    assert progress.can_improve_after_pass(spec)
    assert not progress.can_improve_after_pass(_spec(objectives=[Objective("M23", "match", 5.0)]))
    rounds = [[_result(3, preference=1.0)], [_result(3, preference=1.5)], [_result(3, preference=5.0)]]
    assert _assess(rounds[:2], spec=spec).rounds_since_improvement == 1
    assert _assess(rounds, spec=spec).rounds_since_improvement == 0, "the preference moved by more than noise"


def test_progress_counts_repeats_near_copies_and_untried_fields():
    def at(n, r):
        return Tried(n, {"tank_center_r": r}, object(), _result(-10))
    tried = [at(1, 70.0), at(2, 70.0), at(2, 70.5), at(3, 60.0), Tried(3, {"tank_center_r": 60.0, "loop_r": 60}, None, {})]
    verdict = progress.assess(tried, [1, 2, 3], _spec(), ["tank_center_r", "rc_radius", "loop_r"])
    assert (verdict.repeats, verdict.near_copies, verdict.untried_fields) == (1, 1, ["rc_radius"])


def test_progress_ignores_rounds_without_a_reply_and_restarts_with_a_segment():
    tried = _tried([[_result(-10)], [], [_result(-10)], [_result(-10)]])
    assert progress.assess(tried, [1, 2, 3, 4], _spec()).rounds_since_improvement == 3
    assert progress.assess(tried, [1, 3, 4], _spec()).rounds_since_improvement == 2, "round 2 never got a reply"
    resumed = progress.assess(tried, [1, 2, 3, 4], _spec(), segment_start_round=3)
    assert (resumed.rounds_since_improvement, resumed.reference) == (1, 10.0), "a continue restarts the count, not the reference"


# --- restore: a stored run rebuilt from the database ---------------------------------------------

def test_restore_rebuilds_rounds_memory_and_best_design_without_a_fresh_solve(monkeypatch):
    jobs = _Jobs()
    state, row, _ = _search(monkeypatch, [_propose(71.0), _propose("not a number"), _propose(60.0), _propose(55.0)],
                            _spec(max_tool_calls=10), jobs=jobs)
    assert row["status"] == "goal_met"
    solved = len(jobs.submits)

    restored = restore_run(state.id, jobs)

    assert len(jobs.submits) == solved, "every solve was a cache hit"
    assert (restored.status, restored.stop_reason, restored.outcome_report) == (state.status, state.stop_reason, state.outcome_report)
    assert [(r.round_num, r.verdict, len(r.candidates)) for r in restored.rounds] == \
        [(r.round_num, r.verdict, len(r.candidates)) for r in state.rounds]
    assert [(t.round_num, t.delta, t.scored is None) for t in restored.tried] == \
        [(t.round_num, t.delta, t.scored is None) for t in state.tried]
    assert [t.result for t in restored.tried] == [t.result for t in state.tried], "re-scored to the same results"
    assert restored.best.coilset == state.best.coilset and restored.best.changed_fields == state.best.changed_fields
    assert dataclasses.asdict(restored.progress) == dataclasses.asdict(state.progress)
    assert restored.budget_history == row["budget"] and restored.rounds_persisted == len(restored.rounds)
    assert restored.spec.objectives == state.spec.objectives and restored.spec.settings == state.spec.settings
    assert optimize.search_memory(restored.tried, None, 10) == optimize.search_memory(state.tried, None, 10)


def test_restore_says_why_a_run_cannot_be_rebuilt():
    with pytest.raises(RestoreError, match="no longer exists"):
        restore_run("nope", _Jobs())
    with pytest.raises(RestoreError, match="no search plan"):
        restore_run(create_ai_run("base_coil")["id"], _Jobs())
