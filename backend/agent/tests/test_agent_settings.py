# =============================================================================
# test_agent_settings.py
#
# Checks agent/settings/: what's stored (only changes from the defaults), that
# invalid settings never reach the database, the {{placeholder}} syntax, and
# that an edited prompt actually changes what the model is sent. No LLM, no FEMM.
# =============================================================================
import dataclasses
import json

import pytest

from agent import config
from agent.femm_agent.services.prompts import system_prompt
from agent.input_chat.models.objective import Objective
from agent.input_chat.models.spec import AgentSpec
from agent.repositories import agent_settings_repository as repository
from agent.settings.prompt_defs import PROMPT_DEFS, default_text
from agent.settings.settings import AgentSettings, load_settings, save_settings, settings_from_dict
from agent.settings.templating import check_placeholders, placeholders_in, render
from engine.repositories.coilset_repository import get_coilset


def test_defaults_come_from_config():
    s = AgentSettings()
    assert (s.api_base_url, s.model, s.max_tool_calls) == (config.API_BASE_URL, config.MODEL, config.MAX_TOOL_CALLS)
    assert s.validate() == []
    assert load_settings() == s, "nothing saved -> exactly the defaults"


def test_only_changes_from_the_default_are_stored_and_reset_removes_them():
    s = load_settings()
    s.candidates_per_round = 2
    s.prompts["followup_system"] = "Answer briefly. {{run_data}}"
    s.prompts["search_system"] = default_text("search_system")  # same as default -> not a change
    save_settings(s)
    assert repository.load_overrides() == {"candidates_per_round": 2, "prompt:followup_system": "Answer briefly. {{run_data}}"}
    again = load_settings()
    assert again.candidates_per_round == 2 and again.prompt("followup_system") == "Answer briefly. {{run_data}}"
    assert again.prompt("search_system") == default_text("search_system")

    save_settings(AgentSettings())  # "reset all"
    assert repository.load_overrides() == {}
    assert load_settings() == AgentSettings()


def test_invalid_settings_are_rejected_and_nothing_is_saved():
    s = load_settings()
    s.candidates_per_round = 0
    problems = s.validate()
    for fragment in ("Candidates per round",):
        assert any(fragment in p for p in problems), (fragment, problems)
    with pytest.raises(ValueError):
        save_settings(s)
    assert repository.load_overrides() == {}


def test_the_objective_is_not_a_setting_only_the_built_in_cli_values_exist():
    """Baseline, distances, tolerances and the primary M are chosen per search (in the chat), never
    stored as settings; a value saved by an older version is ignored."""
    for name in ("baseline_config_id", "distances", "m_tolerance_pct", "min_esr_improvement_pct", "primary_m"):
        assert not hasattr(AgentSettings(), name), name
    repository.replace_overrides({"distances": [1.0], "primary_m": "M12", "baseline_config_id": "x"})
    assert load_settings() == AgentSettings()
    assert AgentSpec.from_settings(AgentSettings()).distances == config.DISTANCES


def test_prompt_validation_flags_unknown_and_missing_placeholders():
    s = load_settings()
    s.prompts["search_system"] = "Do the thing. {{objectives}} {{no_such_thing}}"
    problems = s.validate()
    assert any("no_such_thing" in p and "not a placeholder" in p for p in problems)
    assert any("free_fields" in p and "required" in p for p in problems), "a required placeholder was deleted"
    s.prompts["search_system"] = "   "
    assert any("can't be empty" in p for p in s.validate())


def test_every_default_prompt_uses_only_its_own_placeholders():
    for definition in PROMPT_DEFS.values():
        assert check_placeholders(definition.default, definition.placeholders, definition.required) == [], definition.key


def test_render_replaces_in_one_pass_and_leaves_unknown_names():
    assert render("a {{x}} b {{ y }} c {{z}}", {"x": "{{y}}", "y": 2}) == "a {{y}} b 2 c {{z}}"
    assert render('json {"k": {{v}}}', {"v": 1}) == 'json {"k": 1}', "literal braces need no escaping"
    assert placeholders_in("{{a}} {{b}} {{a}}") == ["a", "b"]


def test_blocks_keep_or_drop_their_text_by_whether_the_value_is_set():
    t = "a\n{{#x}}\nhas {{x}}\n{{/x}}\n{{^x}}\nno x\n{{/x}}\nb"
    assert render(t, {"x": 3}) == "a\nhas 3\nb", "tag-only lines leave no blank lines behind"
    assert render(t, {"x": ""}) == "a\nno x\nb"
    assert render(t, {}) == "a\nno x\nb"
    assert render("go {{#x}}yes{{/x}}{{^x}}no{{/x}}.", {"x": 1}) == "go yes."
    assert render("go {{#x}}yes{{/x}}{{^x}}no{{/x}}.", {"x": ""}) == "go no."
    assert "{{#x}}" in render(t, {"x": 1}, keep_blocks=True), "the preview keeps the tags so every branch reads"
    assert placeholders_in("{{#x}}{{y}}{{/x}} {{z}}") == ["x", "y", "z"]


def test_unpaired_block_tags_are_flagged():
    known = {"x": "a switch"}
    assert check_placeholders("{{#x}}open", known) == ["{{#x}} has no matching {{/x}}"]
    assert check_placeholders("closed{{/x}}", known) == ["{{/x}} has no matching {{#x}}"]
    assert check_placeholders("{{#x}}fine{{/x}}", known) == []


def test_settings_snapshot_survives_json_and_ignores_unknown_keys():
    s = load_settings()
    s.model = "some-model"
    s.prompts["search_system"] = "Remember {{keep_best}} things. {{objectives}} {{free_fields}}"
    restored = settings_from_dict({**json.loads(json.dumps(dataclasses.asdict(s))), "from_a_future_version": 1})
    assert restored == s
    assert settings_from_dict(None) == AgentSettings()


def test_saved_values_that_no_longer_fit_the_code_are_ignored():
    repository.replace_overrides({"removed_setting": 1, "temperature": 0.3, "top_p": 0.9,
                                  "prompt:removed_prompt": "x", "max_tool_calls": 7})
    assert load_settings().max_tool_calls == 7


def test_a_saved_prompt_written_for_placeholders_that_no_longer_exist_falls_back_to_the_default():
    """The old search prompt was split across batch_guidance / physics_reference / ...; one edited
    before they were merged must not reach the model with raw {{tags}} in it."""
    stale = "Old edit. {{objectives}} {{free_fields}} {{batch_guidance}} {{physics_reference}}"
    repository.replace_overrides({"prompt:search_system": stale, "prompt:batch_multi": "old",
                                  "prompt:followup_system": "Kept. {{run_data}}"})
    settings = load_settings()
    assert settings.prompt("search_system") == default_text("search_system")
    assert settings.prompt("followup_system") == "Kept. {{run_data}}"
    assert settings_from_dict({"prompts": {"search_system": stale}}).prompts == {}


_METRICS = {5.0: {m: 1.0 for m in ("L_loop", "L_tank", "L_rc", "M12", "M13", "M23", "loop_esr", "tank_esr", "rc_esr", "k12", "k13", "k23")}}


def _search_prompt(settings, per_round, free_fields=None):
    baseline = get_coilset("base_coil")
    spec = AgentSpec(baseline_config_id="base_coil", distances=[5.0], objectives=[Objective("L_rc", "increase", 5.0)],
                     candidates_per_round=per_round, keep_best_candidates=4, settings=settings)
    return system_prompt(baseline, free_fields or {"loop_r": {}}, spec, _METRICS)


def test_an_edited_search_prompt_and_setting_change_what_the_model_is_sent():
    settings = AgentSettings(candidates_per_round=2, prompts={
        "search_system": "{{#n_candidates}}PROBE {{n_candidates}} TIMES.{{/n_candidates}} MEM {{keep_best}} {{objectives}} {{free_fields}}"})
    prompt = _search_prompt(settings, 2)
    assert prompt.startswith("PROBE 2 TIMES. MEM 4 ") and "{{" not in prompt


def test_the_default_search_prompt_is_one_text_whose_optional_parts_follow_the_run():
    several = _search_prompt(AgentSettings(), 3)
    assert "propose up to 3 candidates" in several and "Propose one candidate per turn." not in several
    single = _search_prompt(AgentSettings(), 1)
    assert "Propose one candidate per turn." in single and "up to 3" not in single
    for prompt in (several, single):
        assert "Physics reference" in prompt and "Geometry validity" in prompt and "{{" not in prompt
        assert "4 best" in prompt and "loop " in prompt, "keep_best and the coil radii are filled in"
        assert "Ferrite (" not in prompt and "Metal shield/backplane (" not in prompt, "notes only appear when those fields are free"

    opened = _search_prompt(AgentSettings(), 1, {"loop_r": {}, "tank_ferrite_present": {}, "tank_mu": {}, "backplane": {}})
    assert "Ferrite (tank_ferrite_present, tank_mu)" in opened and "Metal shield/backplane (backplane" in opened


# ---- the settings reach every phase (fake LLM, no server) -------------------

def test_interview_uses_the_conversations_settings(monkeypatch):
    from agent.input_chat.services import interview
    seen = {}

    def fake_call_llm(messages, tools, **kwargs):
        seen.update(system=messages[0]["content"], max_items=tools[0]["function"]["parameters"]["properties"]["distances"]["maxItems"],
                    settings=kwargs["settings"])
        return {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c", "type": "function", "function": {
            "name": "finalize_spec", "arguments": {
                "objective_text": "x", "distances": [5.0, 7.0],
                "objectives": [{"metric": "tank_esr", "kind": "reduce", "tolerance_pct": 10}]}}}]}

    monkeypatch.setattr(interview, "call_llm", fake_call_llm)
    settings = AgentSettings(max_distances=2, candidates_per_round=3, max_tool_calls=9,
                             prompts={"interview_system": "CUSTOM {{sim_lines}} {{derived_lines}} {{variable_lines}}"})
    state = interview.InterviewState(baseline_config_id="base_coil", settings=settings)
    interview.run_interview_turn(state, "cut tank esr")

    assert seen["system"].startswith("CUSTOM ") and seen["settings"] is settings and seen["max_items"] == 2
    assert state.finalized, state.error
    assert (state.spec.settings, state.spec.candidates_per_round, state.spec.max_tool_calls) == (settings, 3, 9)

    state = interview.InterviewState(baseline_config_id="base_coil", settings=dataclasses.replace(settings, max_distances=1))
    interview.run_interview_turn(state, "cut tank esr")
    assert not state.finalized and "at most 1" in state.transcript[-1]["content"]


def test_followup_uses_the_runs_settings(monkeypatch):
    from agent.femm_agent.services import followup
    from agent.femm_agent.models.state import AgentRunState, AgentRunStatus
    from agent.input_chat.services.interview import InterviewState
    seen = {}

    def fake_call_llm(messages, tools, **kwargs):
        seen.update(system=messages[0]["content"], settings=kwargs["settings"])
        return {"role": "assistant", "content": "fine"}

    monkeypatch.setattr(followup, "call_llm", fake_call_llm)
    settings = AgentSettings(prompts={"followup_system": "ONLY DATA: {{run_data}}"})
    spec = AgentSpec(baseline_config_id="base_coil", distances=[5.0], objectives=[Objective("tank_esr", "reduce", 10.0)], settings=settings)
    run_state = AgentRunState(id="r", spec=spec, baseline_coilset=get_coilset("base_coil"), status=AgentRunStatus.RUNNING)
    followup.run_followup_turn(InterviewState(baseline_config_id="base_coil"), "how is it going?", run_state)

    assert seen["system"].startswith("ONLY DATA: Search status: still running") and seen["settings"] is settings


def test_retry_limits_come_from_the_runs_settings(monkeypatch):
    from agent.femm_agent.tests import test_agent_retry as retry
    settings = AgentSettings(round_llm_attempts=1, search_attempts=1)
    spec = dataclasses.replace(retry._spec(rounds=3), settings=settings)
    state, sent = retry._run(monkeypatch, [retry._timeout(), retry._timeout(), retry._timeout(), retry._timeout()], spec)
    assert len(sent) == 1, "one try per round, one search attempt: a single failed request ends the run"
    assert state.status.value == "failed" and "1 tries in a row" in state.outcome_report


def test_the_default_followup_prompt_follows_whether_the_search_is_running():
    from agent.femm_agent.models.state import AgentRunState, AgentRunStatus
    from agent.femm_agent.services.followup import followup_system_prompt
    spec = AgentSpec(baseline_config_id="base_coil", distances=[5.0], objectives=[Objective("tank_esr", "reduce", 10.0)])
    running = AgentRunState(id="r", spec=spec, baseline_coilset=get_coilset("base_coil"), status=AgentRunStatus.RUNNING)
    finished = dataclasses.replace(running, status=AgentRunStatus.GOAL_MET)
    text = followup_system_prompt(running)
    assert "still running" in text and "inputs of a running search are locked" in text and "{{" not in text
    text = followup_system_prompt(finished)
    assert "has finished" in text and "can't be changed" in text and "still running" not in text
