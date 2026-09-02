"""Config Library tab: list + create/edit/clone/delete CoilSet configs.

Mirrors webapp/src/webapp/routers/configs.py's behavior (same repository
calls, same check_valid_coilset validation, "New" starts blank like the
web form does) but as a single in-memory form buffer instead of an HTTP
round trip per action.
"""

import dataclasses

from imgui_bundle import imgui

from .. import field_layout

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_STATUS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)


class ConfigsPanelState:
    def __init__(self):
        self.configs: list[dict] = []
        self.selected_id: str | None = None
        self.buffer: dict = {}
        self.error: str = ""
        self.status: str = ""
        self.refresh()

    def refresh(self) -> None:
        from repositories.coilset_repository import list_configs

        self.configs = list_configs()

    def _selected_record(self) -> dict | None:
        return next((c for c in self.configs if c["id"] == self.selected_id), None)

    def is_selected_builtin(self) -> bool:
        record = self._selected_record()
        return bool(record and record.get("builtin"))

    def _blank_buffer(self) -> dict:
        types = field_layout.field_types()
        return {name: ("" if t is str else (0 if t is int else 0.0)) for name, t in types.items()}

    def load(self, config_id: str) -> None:
        from repositories.coilset_repository import get_coilset

        coilset = get_coilset(config_id)
        self.buffer = dataclasses.asdict(coilset)
        self.selected_id = config_id
        self.error = ""
        self.status = ""

    def new(self) -> None:
        self.buffer = self._blank_buffer()
        self.buffer["config_name"] = "New Config"
        self.selected_id = None
        self.error = ""
        self.status = ""

    def _build_coilset(self):
        from models.coilset import CoilSet

        return CoilSet(**self.buffer)

    @staticmethod
    def _validate(coilset) -> str | None:
        from services.femm_service import check_valid_coilset

        reason, is_valid = check_valid_coilset(
            coilset.loop_r, coilset.num_loop_turns, coilset.num_parallel_loop_traces,
            coilset.loop_trace_spacing, coilset.loop_turn_spacing, coilset.loop_trace_width,
            coilset.tank_center_r, coilset.num_tank_turns, coilset.num_parallel_tank_traces,
            coilset.tank_trace_spacing, coilset.tank_turn_spacing, coilset.tank_trace_width,
            coilset.rc_radius, coilset.num_rc_turns, coilset.num_parallel_rc_traces,
            coilset.rc_trace_spacing, coilset.rc_turn_spacing, coilset.rc_trace_width,
        )
        return None if is_valid else reason

    def save(self) -> None:
        from repositories.coilset_repository import save_config

        try:
            coilset = self._build_coilset()
        except (TypeError, ValueError) as exc:
            self.error = str(exc)
            return
        error = self._validate(coilset)
        if error:
            self.error = error
            return
        record = save_config(coilset, name=coilset.config_name.strip(), config_id=self.selected_id)
        self.error = ""
        self.status = f"Saved {record['name']!r}"
        self.refresh()
        self.load(record["id"])

    def clone(self) -> None:
        if not self.selected_id:
            return
        from repositories.coilset_repository import get_coilset, save_config

        coilset = get_coilset(self.selected_id)
        record = save_config(
            coilset, name=f"{coilset.config_name.strip()} (copy)", cloned_from=self.selected_id,
        )
        self.refresh()
        self.load(record["id"])
        self.status = f"Cloned to {record['name']!r}"

    def delete(self) -> None:
        if not self.selected_id:
            return
        from repositories.coilset_repository import delete_config

        try:
            delete_config(self.selected_id)
        except ValueError as exc:
            self.error = str(exc)
            return
        self.refresh()
        self.selected_id = None
        self.buffer = {}
        self.status = "Deleted"
        self.error = ""


def _draw_field(state: ConfigsPanelState, name: str, label: str, field_type: type) -> None:
    imgui.set_next_item_width(180)
    if name in field_layout.PRESENCE_FIELDS:
        changed, value = imgui.checkbox(f"{label}##{name}", bool(state.buffer[name]))
        if changed:
            state.buffer[name] = int(value)
    elif field_type is int:
        changed, value = imgui.input_int(f"{label}##{name}", int(state.buffer[name]))
        if changed:
            state.buffer[name] = value
    elif field_type is float:
        changed, value = imgui.input_float(f"{label}##{name}", float(state.buffer[name]), format="%.6g")
        if changed:
            state.buffer[name] = value
    else:
        changed, value = imgui.input_text(f"{label}##{name}", str(state.buffer[name]))
        if changed:
            state.buffer[name] = value


def _draw_form(state: ConfigsPanelState) -> None:
    labels = field_layout.field_labels()
    types = field_layout.field_types()
    is_builtin = state.is_selected_builtin()

    if imgui.button("Save"):
        state.save()
    imgui.same_line()

    imgui.begin_disabled(state.selected_id is None)
    if imgui.button("Clone"):
        state.clone()
    imgui.end_disabled()
    imgui.same_line()

    imgui.begin_disabled(state.selected_id is None or is_builtin)
    if imgui.button("Delete"):
        state.delete()
    imgui.end_disabled()

    if is_builtin:
        imgui.same_line()
        imgui.text_disabled("(built-in - editable, not deletable)")

    if state.error:
        imgui.text_colored(_ERROR_COLOR, state.error)
    elif state.status:
        imgui.text_colored(_STATUS_COLOR, state.status)

    imgui.separator()

    for group_name, field_names in field_layout.FIELD_GROUPS.items():
        if imgui.collapsing_header(f"{group_name}##group_{group_name}"):
            for name in field_names:
                _draw_field(state, name, labels.get(name, name), types[name])

    if imgui.collapsing_header("Advanced##group_advanced"):
        for name in field_layout.ADVANCED_FIELDS:
            _draw_field(state, name, labels.get(name, name), types[name])


def draw(state: ConfigsPanelState) -> None:
    imgui.begin_child("configs_list_pane", imgui.ImVec2(260, 0), imgui.ChildFlags_.borders.value)
    if imgui.button("+ New", imgui.ImVec2(-1, 0)):
        state.new()
    imgui.separator()
    for cfg in state.configs:
        tag = "Built-in" if cfg.get("builtin") else "Saved"
        label = f"{cfg['name']} [{tag}]##{cfg['id']}"
        selected = cfg["id"] == state.selected_id
        clicked, _ = imgui.selectable(label, selected)
        if clicked:
            state.load(cfg["id"])
    imgui.end_child()

    imgui.same_line()

    imgui.begin_child("configs_form_pane", imgui.ImVec2(0, 0), imgui.ChildFlags_.borders.value)
    if state.selected_id is None and not state.buffer:
        imgui.text_wrapped("Select a config on the left, or click + New to create one.")
    else:
        _draw_form(state)
    imgui.end_child()
