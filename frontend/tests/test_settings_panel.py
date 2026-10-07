"""Exercises SettingsPanelState's methods directly - the same calls the Settings
screen's inputs and buttons make - against the real (temporary, per-test) database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import frontend._sim_path  # noqa: F401 - adds backend to sys.path

from agent.repositories import agent_settings_repository
from agent.settings.prompt_defs import PROMPT_DEFS
from agent.settings.settings import AgentSettings, load_settings
from frontend.screen_settings.settings_panel import SettingsPanelState


def test_opens_on_the_defaults_with_nothing_to_save():
    state = SettingsPanelState()
    assert state.draft == AgentSettings() and not state.dirty and state.problems() == []


def test_editing_a_number_marks_it_changed_and_save_stores_only_that():
    state = SettingsPanelState()
    state.set_field("candidates_per_round", 2)
    assert state.dirty and state.is_modified("candidates_per_round") and not state.is_modified("max_tool_calls")

    assert state.save() is True
    assert agent_settings_repository.load_overrides() == {"candidates_per_round": 2}
    assert not state.dirty and load_settings().candidates_per_round == 2 and "Saved" in state.message


def test_sampling_is_editable_under_llm_connection():
    from frontend.screen_settings import settings_panel
    fields = settings_panel._SECTION_FIELDS["LLM connection"]
    assert {"temperature", "top_p", "seed"} <= set(fields)
    for name in fields:  # every row has a label, a help line and a known input type
        assert name in settings_panel.FIELD_LABELS and name in settings_panel.FIELD_HELP
    assert "seed" in settings_panel._INT_FIELDS and {"temperature", "top_p"} <= set(settings_panel._FLOAT_FIELDS)

    state = SettingsPanelState()
    state.set_field("temperature", 0.3)
    state.set_field("seed", 7)
    assert state.save() is True
    assert agent_settings_repository.load_overrides() == {"temperature": 0.3, "seed": 7}

    state.set_field("top_p", 0.0)
    assert state.save() is False and any("Sampling top-p" in p for p in state.problems())


def test_invalid_input_blocks_save_and_names_the_field():
    state = SettingsPanelState()
    state.set_field("candidates_per_round", 0)
    assert state.save() is False
    assert any("Candidates per round" in p for p in state.problems())
    assert agent_settings_repository.load_overrides() == {}

    state.reset_field("candidates_per_round")  # fixing it clears the problem
    assert state.problems() == [] and not state.dirty


def test_a_prompt_edit_is_saved_and_reset_goes_back_to_the_default():
    state = SettingsPanelState()
    key = "followup_system"
    assert not state.is_prompt_modified(key)
    state.set_prompt(key, "One at a time. {{run_data}}")
    assert state.is_prompt_modified(key) and state.save()
    assert load_settings().prompt(key) == "One at a time. {{run_data}}"

    state = SettingsPanelState()  # reopened: still there
    assert state.prompt_text(key) == "One at a time. {{run_data}}"
    state.reset_prompt(key)
    assert not state.is_prompt_modified(key) and state.save()
    assert agent_settings_repository.load_overrides() == {}


def test_typing_the_default_text_back_is_not_an_edit():
    state = SettingsPanelState()
    state.set_prompt("followup_system", "changed")
    state.set_prompt("followup_system", PROMPT_DEFS["followup_system"].default)
    assert not state.dirty


def test_prompt_placeholder_problems_block_save():
    state = SettingsPanelState()
    state.set_prompt("search_system", "Search! {{objectives}} {{oops}}")
    problems = state.prompt_problems("search_system")
    assert any("oops" in p for p in problems) and any("free_fields" in p for p in problems)
    assert state.save() is False


def test_preview_shows_each_placeholder_as_what_it_stands_for():
    state = SettingsPanelState()
    preview = state.prompt_preview("search_system")
    assert "<how many best candidates are re-shown each turn>" in preview and "{{keep_best}}" not in preview
    assert "{{#n_candidates}}" in preview and "{{^n_candidates}}" in preview, "block tags stay so every branch can be read"


def test_discard_and_reset_all():
    state = SettingsPanelState()
    state.set_field("model", "other")
    state.set_prompt("followup_system", "x {{run_data}}")
    state.discard()
    assert not state.dirty and state.draft.model == AgentSettings().model

    state.set_field("model", "other")
    assert state.save()
    state.reset_all()
    assert state.dirty and state.draft == AgentSettings(), "reset is a draft change until saved"
    assert state.save() and agent_settings_repository.load_overrides() == {}
