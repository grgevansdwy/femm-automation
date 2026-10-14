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
from frontend.services import toast


def test_opens_on_the_defaults_with_nothing_to_save():
    state = SettingsPanelState()
    assert state.draft == AgentSettings() and not state.dirty and state.problems() == []


def test_editing_a_number_marks_it_changed_and_save_stores_only_that():
    state = SettingsPanelState()
    state.set_field("candidates_per_round", 2)
    assert state.dirty and state.is_modified("candidates_per_round") and not state.is_modified("max_tool_calls")

    assert state.save() is True
    assert agent_settings_repository.load_overrides() == {"candidates_per_round": 2}
    assert not state.dirty and load_settings().candidates_per_round == 2 and toast.active()[-1] == ("Settings saved.", False)


def test_the_screen_offers_one_prompt_and_its_numbers():
    from frontend.screen_settings import settings_panel
    assert settings_panel.SECTIONS == ("Field Config", "System Prompt", "LLM & Budget")
    assert settings_panel.SHOWN_PROMPT == "search_system"
    assert settings_panel.SHOWN_FIELDS == ("api_base_url", "model", "max_tool_calls", "search_time_limit_s",
                                           "max_simulations", "candidates_per_round")
    for name in settings_panel.SHOWN_FIELDS:  # every row has a label, a help line and an input of the right type
        assert name in settings_panel.FIELD_LABELS and name in settings_panel.FIELD_HELP
        default = getattr(AgentSettings(), name)
        assert (name in settings_panel._INT_FIELDS) == isinstance(default, int), name
        assert (name in settings_panel._FLOAT_FIELDS) == isinstance(default, float), name


def test_a_changed_setting_the_screen_does_not_show_is_named_and_can_be_reset():
    state = SettingsPanelState()
    assert state.hidden_changes() == []
    state.set_field("candidates_per_round", 2)  # shown: not a hidden change
    state.set_prompt("search_system", "Search! {{objectives}} {{free_fields}}")
    assert state.hidden_changes() == []

    state.set_field("temperature", 0.3)
    state.set_prompt("followup_system", "One at a time. {{run_data}}")
    assert state.save() is True
    state = SettingsPanelState()  # reopened: still in force, and the screen says so
    assert state.hidden_changes() == ["Sampling temperature", "Follow up chat prompt"]

    state.reset_hidden()
    assert state.hidden_changes() == [] and state.save() is True
    assert set(agent_settings_repository.load_overrides()) == {"candidates_per_round", "prompt:search_system"}


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


def test_discard():
    state = SettingsPanelState()
    state.set_field("model", "other")
    state.set_prompt("followup_system", "x {{run_data}}")
    state.discard()
    assert not state.dirty and state.draft.model == AgentSettings().model
    assert toast.active()[-1] == ("Changes discarded.", False)


def test_search_fields_page_lists_every_numeric_field_once_in_the_forms_groups():
    from agent.input_chat.models.variables import DEFAULT_SEARCH_FIELDS
    from frontend.screen_settings import settings_panel

    assert "Field Config" in settings_panel.SECTIONS
    groups = settings_panel.search_field_groups()
    listed = [name for _, names in groups for name in names]
    assert sorted(listed) == sorted(DEFAULT_SEARCH_FIELDS) and len(listed) == len(set(listed))
    assert [g for g, _ in groups] == ["Loop", "Tank", "RC", "Ferrite", "Shield & backplane", "Advanced"]


def test_unticking_a_field_and_setting_limits_is_saved_as_only_those_rows():
    state = SettingsPanelState()
    assert state.search_rule("tank_trace_width").enabled and not state.search_rule("tank_mu").enabled
    assert state.limit_text("num_tank_layers", "max") == "8" and state.limit_text("tank_center_r", "max") == "120"

    state.set_search_enabled("tank_trace_width", False)
    state.set_limit_text("tank_center_r", "max", "80")
    state.set_limit_text("tank_center_r", "min", " 20.5 ")
    assert state.dirty and state.is_search_field_modified("tank_center_r") and not state.is_search_field_modified("loop_r")

    assert state.save() is True
    assert agent_settings_repository.load_overrides() == {"search_fields": {
        "tank_trace_width": {"enabled": False, "min": None, "max": 10},
        "tank_center_r": {"enabled": True, "min": 20.5, "max": 80},
    }}
    assert not SettingsPanelState().search_rule("tank_trace_width").enabled, "a fresh panel loads what was saved"


def test_clearing_the_layer_limit_and_resetting_it():
    state = SettingsPanelState()
    state.set_limit_text("num_rc_layers", "max", "")
    assert state.search_rule("num_rc_layers").max is None and state.is_search_field_modified("num_rc_layers")
    assert state.save() is True
    assert load_settings().search_fields == {"num_rc_layers": {"enabled": True, "min": None, "max": None}}

    state.reset_search_field("num_rc_layers")
    assert state.search_rule("num_rc_layers").max == 8 and state.save() is True
    assert agent_settings_repository.load_overrides() == {}, "a row back at its default is stored as nothing"


def test_half_typed_or_inconsistent_limits_block_save():
    state = SettingsPanelState()
    state.set_limit_text("tank_center_r", "max", "8o")
    assert state.limit_text("tank_center_r", "max") == "8o", "what was typed stays in the box"
    assert state.save() is False and any("tank_center_r: maximum must be a number" in p for p in state.problems())
    assert state.search_rule("tank_center_r").max == 120, "text that isn't a number is never stored as a limit"

    state.set_limit_text("tank_center_r", "max", "50")
    state.set_limit_text("tank_center_r", "min", "60")
    assert state.save() is False and any("minimum 60 is above maximum 50" in p for p in state.problems())
    state.reset_search_field("tank_center_r")
    assert state.problems() == [] and not state.dirty

    state.set_limit_text("loop_r", "min", "x")
    state.discard()
    assert state.problems() == [] and state.limit_text("loop_r", "min") == "25"


def test_back_with_nothing_unsaved_just_leaves_and_with_changes_asks_first():
    state = SettingsPanelState()
    assert state.section == "Field Config", "the screen opens on Field Config"
    assert state.request_leave() is True and not state.leaving
    assert state.take_leave(), "Back with nothing unsaved leaves at once"

    state.set_field("candidates_per_round", 2)
    assert state.request_leave() is False and state.leaving
    state.keep_editing()
    assert not state.leaving and state.dirty and not state.take_leave()

    state.request_leave()
    state.discard_and_leave()
    assert not state.dirty and state.take_leave() and not state.take_leave(), "leaving is taken once"
    assert load_settings().candidates_per_round == AgentSettings().candidates_per_round

    state.set_field("candidates_per_round", 3)
    state.request_leave()
    state.save_and_leave()
    assert state.take_leave() and load_settings().candidates_per_round == 3


def test_back_does_not_leave_while_a_limit_box_holds_text_that_is_not_a_number():
    state = SettingsPanelState()
    state.set_limit_text("loop_r", "min", "1.2x")
    assert state.request_leave() is False and state.leaving
    state.save_and_leave()
    assert not state.take_leave() and state.leaving, "it cannot be saved, so the question stays open"
    state.discard_and_leave()
    assert state.take_leave() and state.limit_text("loop_r", "min") == "25", "back to the shipped limit"


def test_save_as_default_remembers_the_settings_and_reset_to_my_default_returns_to_them():
    state = SettingsPanelState()
    assert state.user_default is None

    state.reset_to_user_default()  # nothing remembered yet: leaves the draft alone
    assert state.draft == AgentSettings()

    state.set_field("candidates_per_round", 2)
    state.set_limit_text("loop_r", "min", "0.20334")
    assert state.save_as_default() is True
    assert toast.active()[-1] == ("Saved as your default.", False)
    assert load_settings().candidates_per_round == 2, "it is also saved as the current settings"
    assert SettingsPanelState().user_default.candidates_per_round == 2, "and survives reopening the screen"
    assert SettingsPanelState().search_rule("loop_r").min == 0.20334

    state.set_field("candidates_per_round", 5)
    assert state.save() is True and load_settings().candidates_per_round == 5
    state.reset_to_user_default()
    assert state.draft.candidates_per_round == 2 and state.draft.search_fields == state.user_default.search_fields


def test_save_as_default_does_nothing_while_the_settings_have_a_problem():
    state = SettingsPanelState()
    state.set_field("candidates_per_round", 0)
    assert state.problems() and state.save_as_default() is False
    assert state.user_default is None and agent_settings_repository.load_user_default() is None
