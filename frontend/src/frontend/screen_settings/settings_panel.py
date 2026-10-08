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
from agent.settings.settings import FIELD_HELP, FIELD_LABELS, AgentSettings, load_settings, save_settings
from agent.settings.templating import check_placeholders, render

from ..services import field_layout

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_STATUS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_MODIFIED_COLOR = imgui.ImVec4(0.9, 0.75, 0.2, 1.0)
_MUTED_COLOR = imgui.ImVec4(0.6, 0.6, 0.6, 1.0)

SECTIONS = ("System prompts", "LLM connection", "Search budget", "Search fields")
_SECTION_FIELDS = {
    "LLM connection": ("api_base_url", "model", "llm_time_limit_s", "llm_max_tokens",
                       "temperature", "top_p", "seed"),
    "Search budget": ("max_tool_calls", "search_time_limit_s", "max_simulations", "plateau_rounds", "stall_rounds",
                      "noise_points", "candidates_per_round", "keep_best_candidates", "round_llm_attempts",
                      "search_attempts", "max_distances"),
}
_INT_FIELDS = ("seed", "llm_max_tokens", "max_tool_calls", "candidates_per_round", "keep_best_candidates",
               "round_llm_attempts", "search_attempts", "max_distances", "max_simulations", "plateau_rounds",
               "stall_rounds")
_FLOAT_FIELDS = ("llm_time_limit_s", "temperature", "top_p", "search_time_limit_s", "noise_points")


class SettingsPanelState:
    def __init__(self) -> None:
        self.section = SECTIONS[0]
        self.selected_prompt = "search_system"
        self.message = ""
        self.reload()

    # ---- loading / saving
    def reload(self) -> None:
        """Forgets the draft and starts again from what is saved."""
        self.saved = load_settings()
        self.draft = copy.deepcopy(self.saved)
        # (field, "min" | "max") -> what is typed in a limit box that isn't a number
        # yet ("", "1.", "-"). Kept apart from the draft so half-typed text is never
        # stored as a limit; anything here blocks Save (problems()).
        self._limit_typing: dict = {}

    @property
    def dirty(self) -> bool:
        return self.draft != self.saved

    def problems(self) -> list[str]:
        typing = [f"Search fields, {name}: {'minimum' if side == 'min' else 'maximum'} must be a number or left empty"
                  for (name, side) in self._limit_typing]
        return self.draft.validate() + typing

    def save(self) -> bool:
        problems = self.problems()
        if problems:
            self.message = ""
            return False
        save_settings(self.draft)
        self.saved = copy.deepcopy(self.draft)
        self.message = "Saved. New conversations and searches use these settings; one already running keeps its own."
        return True

    def discard(self) -> None:
        self.reload()
        self.message = "Changes discarded."

    def reset_all(self) -> None:
        """Puts every setting and prompt back to its default in the draft (still needs Save)."""
        self.draft = AgentSettings()
        self._limit_typing = {}
        self.message = ""

    # ---- search fields: which design fields a search may change, and their limits
    def search_rule(self, name: str) -> variables.FieldRule:
        return variables.search_field_table(self.draft)[name]

    def is_search_field_modified(self, name: str) -> bool:
        return self.search_rule(name) != variables.DEFAULT_SEARCH_FIELDS[name] or any(
            key[0] == name for key in self._limit_typing)

    def _set_search_rule(self, name: str, **changes) -> None:
        table = variables.search_field_table(self.draft)
        table[name] = dataclasses.replace(table[name], **changes)
        self.draft.search_fields = variables.search_field_overrides(table)
        self.message = ""

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
            self.message = ""
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
        self.message = ""

    def reset_field(self, name: str) -> None:
        setattr(self.draft, name, getattr(AgentSettings(), name))
        self.message = ""

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
        self.message = ""

    def reset_prompt(self, key: str) -> None:
        self.draft.prompts.pop(key, None)
        self.message = ""

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
    for name in _SECTION_FIELDS[state.section]:
        _field_row(state, name)


def search_field_groups() -> list[tuple[str, list[str]]]:
    """The Search fields page's sections: the config form's own grouping
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
    imgui.push_text_wrap_pos(0.0)
    imgui.text_disabled("Which design fields a search may change, and how far. A ticked field is offered to the AI; "
                        "an unticked one always stays at its baseline value. Minimum and maximum are plain numbers "
                        "in the field's own unit - leave one empty for no limit on that side. A proposed value "
                        "outside a limit is rejected before it is simulated. The AI can suggest a change to this "
                        "table in the chat, but only you can make one.")
    imgui.pop_text_wrap_pos()
    imgui.spacing()
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


def _draw_prompt_list(state: SettingsPanelState, width: float) -> None:
    imgui.begin_child("prompt_list", imgui.ImVec2(width, 0), imgui.ChildFlags_.borders.value)
    for key, definition in PROMPT_DEFS.items():
        label = definition.title + ("  *" if state.is_prompt_modified(key) else "")
        if imgui.selectable(f"{label}##prompt_{key}", state.selected_prompt == key)[0]:
            state.selected_prompt = key
    imgui.end_child()


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
    key = state.selected_prompt
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


def draw(state: SettingsPanelState, size: imgui.ImVec2) -> None:
    imgui.begin_child("agent_settings", size, 0)

    problems = state.problems()
    imgui.begin_disabled(not state.dirty or bool(problems))
    if imgui.button("Save", imgui.ImVec2(100, 0)):
        state.save()
    imgui.end_disabled()
    imgui.same_line()
    imgui.begin_disabled(not state.dirty)
    if imgui.button("Discard changes"):
        state.discard()
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("Reset everything to defaults"):
        state.reset_all()
    if state.dirty:
        imgui.same_line()
        imgui.text_colored(_MODIFIED_COLOR, "Unsaved changes")
    elif state.message:
        imgui.same_line()
        imgui.text_colored(_STATUS_COLOR, state.message)
    imgui.text_disabled("Applies to new conversations and searches; one already running keeps the settings it started with.")
    if problems:
        imgui.text_colored(_ERROR_COLOR, f"Can't save yet - {len(problems)} problem(s):")
        for problem in problems[:6]:
            imgui.bullet_text(problem)
    imgui.separator()

    avail = imgui.get_content_region_avail()
    imgui.begin_child("settings_sections", imgui.ImVec2(190, avail.y), imgui.ChildFlags_.borders.value)
    for section in SECTIONS:
        if imgui.selectable(f"{section}##section", state.section == section)[0]:
            state.section = section
    imgui.end_child()
    imgui.same_line()

    imgui.begin_child("settings_body", imgui.ImVec2(0, avail.y), 0)
    if state.section == SECTIONS[0]:
        _draw_prompt_list(state, 200)
        imgui.same_line()
        imgui.begin_child("prompt_editor", imgui.ImVec2(0, 0), 0)
        _draw_prompt_editor(state)
        imgui.end_child()
    elif state.section == "Search fields":
        _draw_search_fields(state)
    else:
        _draw_fields(state)
    imgui.end_child()
    imgui.end_child()
