"""Agent Settings mode: edit the agent's prompts and numbers (agent/settings/),
saved in the database and used by every conversation and search started after.

Edits go to a draft copy of the settings; nothing is stored until Save, and
Discard drops the draft. A setting at its default is stored as nothing at all
(so "Reset" really returns it to whatever the code's default is). The logic -
parsing, which fields differ from the default, validation, saving - is on
SettingsPanelState and unit-tested; the draw functions only show it.
"""

import copy
import dataclasses

from imgui_bundle import imgui

from agent.input_chat.models import variables
from agent.settings.prompt_defs import PROMPT_DEFS
from agent.settings.settings import (FIELD_HELP, FIELD_LABELS, AgentSettings, load_settings, load_user_default,
                                     save_settings, save_user_default)
from agent.settings.templating import check_placeholders, render

from ..services import field_layout, toast

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_MODIFIED_COLOR = imgui.ImVec4(0.9, 0.75, 0.2, 1.0)
_MUTED_COLOR = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)

SECTIONS = ("Field Config", "System Prompt", "LLM & Budget")
# The one prompt and the few numbers this screen offers. Every other AgentSettings
# value (the interview and follow-up prompts, sampling, per-request limits, stop
# rules, retries) stays at its default in agent/config.py and settings/prompts/.
SHOWN_PROMPT = "search_system"
SHOWN_FIELDS = ("api_base_url", "model", "max_tool_calls", "search_time_limit_s", "max_simulations",
                "candidates_per_round")
_INT_FIELDS = ("max_tool_calls", "candidates_per_round", "max_simulations")
_FLOAT_FIELDS = ("search_time_limit_s",)


class SettingsPanelState:
    def __init__(self) -> None:
        self.section = SECTIONS[0]
        self.leaving = False    # Back was pressed with unsaved changes: the save/discard question is open
        self.leave_ok = False   # the screen may be closed now; main.py takes this with take_leave()
        self.reload()

    # ---- loading / saving
    def reload(self) -> None:
        """Forgets the draft and starts again from what is saved."""
        self.saved = load_settings()
        self.draft = copy.deepcopy(self.saved)
        self.user_default = load_user_default()
        # (field, "min" | "max") -> what is typed in a limit box that isn't a number
        # yet ("", "1.", "-"). Kept apart from the draft so half-typed text is never
        # stored as a limit; anything here blocks Save (problems()).
        self._limit_typing: dict = {}

    @property
    def dirty(self) -> bool:
        return self.draft != self.saved

    def problems(self) -> list[str]:
        typing = [f"Field Config, {name}: {'minimum' if side == 'min' else 'maximum'} must be a number or left empty"
                  for (name, side) in self._limit_typing]
        return self.draft.validate() + typing

    def save(self) -> bool:
        problems = self.problems()
        if problems:
            return False
        save_settings(self.draft)
        self.saved = copy.deepcopy(self.draft)
        toast.success("Settings saved.")
        return True

    def save_as_default(self) -> bool:
        """Remembers the draft as the person's own default (what "Reset to my default"
        returns to) and saves it as the current settings too, so what is on screen,
        what new searches use and the default all agree."""
        if not self.save():
            return False
        save_user_default(self.saved)
        self.user_default = copy.deepcopy(self.saved)
        toast.success("Saved as your default.")
        return True

    def reset_to_user_default(self) -> None:
        """Puts every setting back to the person's own default in the draft (still needs Save)."""
        if self.user_default is None:
            return
        self.draft = copy.deepcopy(self.user_default)
        self._limit_typing = {}

    def discard(self) -> None:
        self.reload()
        toast.success("Changes discarded.")

    # ---- leaving the screen
    def request_leave(self) -> bool:
        """Back was pressed. True = nothing is unsaved, go. Otherwise the question
        "save or discard?" opens (leaving) and the screen stays until it is answered."""
        if not self.dirty and not self._limit_typing:
            self.leave_ok = True
            return True
        self.leaving = True
        return False

    def save_and_leave(self) -> None:
        if self.save():
            self.leaving, self.leave_ok = False, True

    def discard_and_leave(self) -> None:
        self.discard()
        self.leaving, self.leave_ok = False, True

    def keep_editing(self) -> None:
        self.leaving = False

    def take_leave(self) -> bool:
        """True once, after the question was answered with Save or Discard."""
        ok, self.leave_ok = self.leave_ok, False
        return ok

    # ---- settings this screen has no input for
    def hidden_changes(self) -> list[str]:
        """Names of the settings that differ from their default but are not shown
        here (saved by an earlier version, or written straight to the database).
        They are still in force, so the screen says so rather than hiding them."""
        defaults = AgentSettings()
        names = [FIELD_LABELS[name] for name in FIELD_LABELS
                 if name not in SHOWN_FIELDS and getattr(self.draft, name) != getattr(defaults, name)]
        return names + [f"{PROMPT_DEFS[key].title} prompt" if key in PROMPT_DEFS else f"prompt '{key}'"
                        for key in self.draft.prompts if key != SHOWN_PROMPT]

    def reset_hidden(self) -> None:
        """Puts every setting not shown here back to its default in the draft (still needs Save)."""
        defaults = AgentSettings()
        for name in FIELD_LABELS:
            if name not in SHOWN_FIELDS:
                setattr(self.draft, name, getattr(defaults, name))
        self.draft.prompts = {key: text for key, text in self.draft.prompts.items() if key == SHOWN_PROMPT}

    # ---- field config: which design fields a search may change, and their limits
    def search_rule(self, name: str) -> variables.FieldRule:
        return variables.search_field_table(self.draft)[name]

    def is_search_field_modified(self, name: str) -> bool:
        return self.search_rule(name) != variables.DEFAULT_SEARCH_FIELDS[name] or any(
            key[0] == name for key in self._limit_typing)

    def _set_search_rule(self, name: str, **changes) -> None:
        table = variables.search_field_table(self.draft)
        table[name] = dataclasses.replace(table[name], **changes)
        self.draft.search_fields = variables.search_field_overrides(table)

    def set_search_enabled(self, name: str, enabled: bool) -> None:
        self._set_search_rule(name, enabled=bool(enabled))

    def limit_text(self, name: str, side: str) -> str:
        """What a limit box shows: the half-typed text if there is some, else the
        stored limit, else empty (= no limit on that side)."""
        if (name, side) in self._limit_typing:
            return self._limit_typing[(name, side)]
        value = getattr(self.search_rule(name), side)
        return "" if value is None else f"{value:g}"

    def set_limit_text(self, name: str, side: str, text: str) -> None:
        """A limit box was edited. Empty clears the limit; a number sets it;
        anything else is kept as typed and blocks Save until it is one."""
        text = text.strip()
        self._limit_typing.pop((name, side), None)
        if not text:
            self._set_search_rule(name, **{side: None})
            return
        try:
            value = float(text)
        except ValueError:
            value = float("nan")
        if value != value or value in (float("inf"), float("-inf")):
            self._limit_typing[(name, side)] = text
            return
        self._set_search_rule(name, **{side: int(value) if value == int(value) else value})

    def reset_search_field(self, name: str) -> None:
        for side in ("min", "max"):
            self._limit_typing.pop((name, side), None)
        default = variables.DEFAULT_SEARCH_FIELDS[name]
        self._set_search_rule(name, enabled=default.enabled, min=default.min, max=default.max)

    # ---- numbers and text
    def is_modified(self, name: str) -> bool:
        return getattr(self.draft, name) != getattr(AgentSettings(), name)

    def set_field(self, name: str, value) -> None:
        setattr(self.draft, name, value)

    def reset_field(self, name: str) -> None:
        setattr(self.draft, name, getattr(AgentSettings(), name))

    # ---- prompts
    def prompt_text(self, key: str) -> str:
        return self.draft.prompt(key)

    def is_prompt_modified(self, key: str) -> bool:
        return self.draft.prompt(key) != PROMPT_DEFS[key].default

    def set_prompt(self, key: str, text: str) -> None:
        """Stores an edit; text identical to the default is stored as no edit."""
        if text == PROMPT_DEFS[key].default:
            self.draft.prompts.pop(key, None)
        else:
            self.draft.prompts[key] = text

    def reset_prompt(self, key: str) -> None:
        self.draft.prompts.pop(key, None)

    def prompt_problems(self, key: str) -> list[str]:
        definition = PROMPT_DEFS[key]
        return check_placeholders(self.draft.prompt(key), definition.placeholders, definition.required)

    def prompt_preview(self, key: str) -> str:
        """The prompt with each {{placeholder}} shown as <what the app fills in>.
        The {{#name}}/{{^name}} tags stay visible so every branch can be read."""
        definition = PROMPT_DEFS[key]
        return render(self.draft.prompt(key), {name: f"<{what}>" for name, what in definition.placeholders.items()},
                      keep_blocks=True)


# ---------------------------------------------------------------- drawing

def _field_row(state: SettingsPanelState, name: str) -> None:
    modified = state.is_modified(name)
    imgui.text(FIELD_LABELS[name])
    if modified:
        imgui.same_line()
        imgui.text_colored(_MODIFIED_COLOR, "(changed)")

    imgui.set_next_item_width(360)
    draft = state.draft
    if name in _INT_FIELDS:
        changed, value = imgui.input_int(f"##{name}", getattr(draft, name))
        if changed:
            state.set_field(name, value)
    elif name in _FLOAT_FIELDS:
        changed, value = imgui.input_double(f"##{name}", getattr(draft, name))
        if changed:
            state.set_field(name, value)
    else:
        changed, value = imgui.input_text(f"##{name}", getattr(draft, name))
        if changed:
            state.set_field(name, value)

    if modified:
        imgui.same_line()
        if imgui.small_button(f"Reset##{name}"):
            state.reset_field(name)
    imgui.push_style_color(imgui.Col_.text.value, _MUTED_COLOR)
    imgui.push_text_wrap_pos(0.0)
    imgui.text(FIELD_HELP[name])
    imgui.pop_text_wrap_pos()
    imgui.pop_style_color()
    imgui.spacing()
    imgui.spacing()


def _draw_fields(state: SettingsPanelState) -> None:
    for name in SHOWN_FIELDS:
        _field_row(state, name)


def search_field_groups() -> list[tuple[str, list[str]]]:
    """The Field Config page's sections: the config form's own grouping
    (field_layout), minus fields that aren't numeric design fields."""
    groups = [(group, names) for group, names in field_layout.FIELD_GROUPS.items()]
    groups.append(("Advanced", list(field_layout.ADVANCED_FIELDS)))
    return [(group, [n for n in names if n in variables.DEFAULT_SEARCH_FIELDS]) for group, names in groups
            if any(n in variables.DEFAULT_SEARCH_FIELDS for n in names)]


def _draw_limit_box(state: SettingsPanelState, name: str, side: str) -> None:
    imgui.set_next_item_width(-1)
    changed, text = imgui.input_text_with_hint(f"##{side}_{name}", "no limit", state.limit_text(name, side))
    if changed:
        state.set_limit_text(name, side, text)


def _draw_search_fields(state: SettingsPanelState) -> None:
    labels = field_layout.field_labels()
    flags = imgui.TableFlags_.borders.value | imgui.TableFlags_.row_bg.value
    for group, names in search_field_groups():
        open_count = sum(state.search_rule(n).enabled for n in names)
        header_flags = imgui.TreeNodeFlags_.default_open.value if group in ("Loop", "Tank", "RC") else 0
        if not imgui.collapsing_header(f"{group}  ({open_count} of {len(names)} open)###search_fields_{group}",
                                       flags=header_flags):
            continue
        if not imgui.begin_table(f"search_fields_table_{group}", 5, flags):
            continue
        imgui.table_setup_column("Search", imgui.TableColumnFlags_.width_fixed.value, 54.0)
        imgui.table_setup_column("Field", imgui.TableColumnFlags_.width_stretch.value)
        imgui.table_setup_column("Minimum", imgui.TableColumnFlags_.width_fixed.value, 110.0)
        imgui.table_setup_column("Maximum", imgui.TableColumnFlags_.width_fixed.value, 110.0)
        imgui.table_setup_column("", imgui.TableColumnFlags_.width_fixed.value, 60.0)
        imgui.table_headers_row()
        for name in names:
            rule = state.search_rule(name)
            imgui.table_next_row()
            imgui.table_next_column()
            changed, enabled = imgui.checkbox(f"##enabled_{name}", rule.enabled)
            if changed:
                state.set_search_enabled(name, enabled)
            imgui.table_next_column()
            imgui.text(labels.get(name, name))
            if imgui.is_item_hovered():
                imgui.set_tooltip(name)
            if name in variables.FLAG_FIELDS:
                imgui.table_next_column()
                imgui.text_disabled("on / off")
                imgui.table_next_column()
            else:
                imgui.table_next_column()
                _draw_limit_box(state, name, "min")
                imgui.table_next_column()
                _draw_limit_box(state, name, "max")
            imgui.table_next_column()
            if state.is_search_field_modified(name) and imgui.small_button(f"Reset##search_{name}"):
                state.reset_search_field(name)
        imgui.end_table()


def _draw_placeholder_help(definition) -> None:
    """The values the app fills in, folded away so the prompt itself gets the room."""
    if not definition.placeholders:
        imgui.text_disabled("No placeholders - this text is used as written.")
        return
    if imgui.tree_node_ex(f"Values the app fills in ({len(definition.placeholders)})##placeholders_{definition.key}", 0):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled("Write them exactly, in double braces, wherever the value should appear.")
        for name, what in definition.placeholders.items():
            marker = " (required)" if name in definition.required else ""
            imgui.bullet_text(f"{{{{{name}}}}}  -  {what}{marker}")
        if definition.switches:
            imgui.spacing()
            imgui.text_disabled("Some parts only apply sometimes. Text between {{#name}} and {{/name}} is kept only "
                                "when that value is set; between {{^name}} and {{/name}} only when it is not. "
                                "Works for: " + ", ".join(definition.switches) + ".")
        imgui.pop_text_wrap_pos()
        imgui.tree_pop()


def _draw_prompt_editor(state: SettingsPanelState) -> None:
    key = SHOWN_PROMPT
    definition = PROMPT_DEFS[key]
    imgui.text(definition.title)
    if state.is_prompt_modified(key):
        imgui.same_line()
        imgui.text_colored(_MODIFIED_COLOR, "(changed)")
        imgui.same_line()
        if imgui.small_button("Reset to default##prompt"):
            state.reset_prompt(key)
    if definition.help:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(definition.help)
        imgui.pop_text_wrap_pos()
    _draw_placeholder_help(definition)

    avail = imgui.get_content_region_avail()
    changed, text = imgui.input_text_multiline(f"##prompt_text_{key}", state.prompt_text(key),
                                               imgui.ImVec2(-1, max(avail.y - 90, 160)))
    if changed:
        state.set_prompt(key, text)
    for problem in state.prompt_problems(key):
        imgui.text_colored(_ERROR_COLOR, problem)
    if imgui.tree_node_ex(f"Preview with placeholders shown##preview_{key}", 0):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(state.prompt_preview(key))
        imgui.pop_text_wrap_pos()
        imgui.tree_pop()


def _draw_leave_question(state: SettingsPanelState) -> None:
    """The popup that opens when Back is pressed with unsaved changes."""
    title = "Unsaved changes"
    if state.leaving and not imgui.is_popup_open(title):
        imgui.open_popup(title)
    if not imgui.begin_popup_modal(title, None, imgui.WindowFlags_.always_auto_resize.value)[0]:
        return
    imgui.text("You have changes that are not saved.")
    problems = state.problems()
    if problems:
        imgui.text_colored(_ERROR_COLOR, f"Can't save yet - {len(problems)} problem(s). Fix them, or discard.")
    imgui.spacing()
    imgui.begin_disabled(bool(problems))
    if imgui.button("Save", imgui.ImVec2(110, 0)):
        state.save_and_leave()
        imgui.close_current_popup()
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("Discard", imgui.ImVec2(110, 0)):
        state.discard_and_leave()
        imgui.close_current_popup()
    imgui.same_line()
    if imgui.button("Keep editing", imgui.ImVec2(110, 0)):
        state.keep_editing()
        imgui.close_current_popup()
    imgui.end_popup()


def draw(state: SettingsPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_settings", size, 0)
    _draw_leave_question(state)

    problems = state.problems()
    right_edge = imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x
    # One row: the title, the buttons that act on the settings, and Back on the right.
    imgui.align_text_to_frame_padding()
    imgui.text("Settings")
    imgui.same_line(0.0, 28.0)
    imgui.begin_disabled(not state.dirty or bool(problems))
    if imgui.button("Save", imgui.ImVec2(100, 0)):
        state.save()
    imgui.end_disabled()
    imgui.same_line()
    imgui.begin_disabled(bool(problems))
    if imgui.button("Save as default"):
        state.save_as_default()
    imgui.end_disabled()
    if imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled.value):
        imgui.set_tooltip("Saves these settings and remembers them as your own default.\n"
                          "\"Reset to my default\" then comes back to them.")
    imgui.same_line()
    imgui.begin_disabled(not state.dirty)
    if imgui.button("Discard changes"):
        state.discard()
    imgui.end_disabled()
    imgui.same_line()
    imgui.begin_disabled(state.user_default is None or state.draft == state.user_default)
    if imgui.button("Reset to my default"):
        state.reset_to_user_default()
    imgui.end_disabled()
    if state.user_default is None and imgui.is_item_hovered(imgui.HoveredFlags_.allow_when_disabled.value):
        imgui.set_tooltip("No default of your own yet. Use \"Save as default\" first.")
    if state.dirty:
        imgui.same_line()
        imgui.align_text_to_frame_padding()
        imgui.text_colored(_MODIFIED_COLOR, "Unsaved changes")
    back_w = 100.0
    imgui.same_line(max(right_edge - back_w, 0.0))
    if imgui.button("Back", imgui.ImVec2(back_w, 0)):
        state.request_leave()
    if problems:
        imgui.text_colored(_ERROR_COLOR, f"Can't save yet - {len(problems)} problem(s):")
        for problem in problems[:6]:
            imgui.bullet_text(problem)
    hidden = state.hidden_changes()
    if hidden:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(_MODIFIED_COLOR, "Also in force, changed earlier and not shown on this screen: "
                           + ", ".join(hidden) + ".")
        imgui.pop_text_wrap_pos()
        if imgui.small_button("Reset those to defaults"):
            state.reset_hidden()
    imgui.separator()

    avail = imgui.get_content_region_avail()
    imgui.begin_child("settings_sections", imgui.ImVec2(190, avail.y), imgui.ChildFlags_.borders.value)
    for section in SECTIONS:
        if imgui.selectable(f"{section}##section", state.section == section)[0]:
            state.section = section
    imgui.end_child()
    imgui.same_line()

    imgui.begin_child("settings_body", imgui.ImVec2(0, avail.y), 0)
    if state.section == "System Prompt":
        _draw_prompt_editor(state)
    elif state.section == "Field Config":
        _draw_search_fields(state)
    else:
        _draw_fields(state)
    imgui.end_child()
    imgui.end_child()
