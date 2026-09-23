"""Config list + create/edit/clone/delete CoilSet form - the dashboard's
left and center columns (draw_list()/draw_form(), drawn side by side by
main.py, not tabs).

Mirrors webapp/src/webapp/routers/configs.py's behavior (same repository
calls, same check_valid_coilset validation, "New" starts blank like the
web form does) but as a single in-memory form buffer instead of an HTTP
round trip per action - except deletion: every config is deletable here,
including built-ins (see ConfigsPanelState.delete).
"""

import dataclasses
from pathlib import Path

from imgui_bundle import imgui, portable_file_dialogs as pfd

from ..files_backup import config_io
from ..services import field_layout

_ERROR_COLOR = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_STATUS_COLOR = imgui.ImVec4(0.35, 0.8, 0.45, 1.0)
_DELETE_POPUP_ID = "confirm_delete_config"


class ConfigsPanelState:
    def __init__(self):
        self.configs: list[dict] = []
        self.selected_id: str | None = None
        self.buffer: dict = {}
        self.error: str = ""
        self.status: str = ""
        self._export_dialog = None  # a pfd.save_file() handle while one is open, else None
        self._import_dialog = None  # a pfd.open_file() handle while one is open, else None
        self.refresh()

    def refresh(self) -> None:
        from engine.repositories.coilset_repository import list_configs

        self.configs = list_configs()

    def _blank_buffer(self) -> dict:
        types = field_layout.field_types()
        return {name: ("" if t is str else (0 if t is int else 0.0)) for name, t in types.items()}

    def load(self, config_id: str) -> None:
        from engine.repositories.coilset_repository import get_coilset

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
        from engine.models.coilset import CoilSet

        return CoilSet(**self.buffer)

    @staticmethod
    def _validate(coilset) -> str | None:
        from engine.services.femm_service import check_valid_coilset

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
        from engine.repositories.coilset_repository import save_config

        try:
            coilset = self._build_coilset()
        except (TypeError, ValueError) as exc:
            self.error = str(exc)
            return
        name = coilset.config_name.strip()
        if config_io.name_conflict(name, exclude_id=self.selected_id):
            self.error = f"A config named {name!r} already exists - choose a different name."
            return
        error = self._validate(coilset)
        if error:
            self.error = error
            return
        record = save_config(coilset, name=name, config_id=self.selected_id)
        self.refresh()
        self.load(record["id"])  # load() resets error/status - set them after, not before
        self.status = f"Saved {record['name']!r}"
        self.error = ""

    def clone(self) -> None:
        if not self.selected_id:
            return
        from engine.repositories.coilset_repository import get_coilset, save_config

        coilset = get_coilset(self.selected_id)
        # Cloning never fails on a name collision - it just stacks another
        # " (copy)" until the name is free ("X (copy)", "X (copy) (copy)",
        # ...), so cloning the same config repeatedly always works.
        name = f"{coilset.config_name.strip()} (copy)"
        while config_io.name_conflict(name):
            name = f"{name} (copy)"
        # The CoilSet's own config_name is renamed too, not just the record's
        # name - otherwise the list says "X (copy)" while the form field
        # below it still says "X", which reads like a bug.
        coilset.config_name = name
        record = save_config(coilset, name=name, cloned_from=self.selected_id)
        self.refresh()
        self.load(record["id"])
        self.status = f"Cloned to {record['name']!r}"
        self.error = ""

    def export(self, path) -> None:
        if not self.selected_id:
            self.error = "Select a config to export first."
            return
        from engine.repositories.coilset_repository import get_config

        Path(path).write_text(config_io.export_record(get_config(self.selected_id)))
        self.status = f"Exported to {path}"
        self.error = ""

    def import_from(self, path) -> None:
        from engine.repositories.coilset_repository import save_config

        try:
            text = Path(path).read_text()
        except OSError as exc:
            self.error, self.status = f"Couldn't read {path}: {exc}", ""
            return
        try:
            coilset, name = config_io.parse_import(text)
        except ValueError as exc:
            self.error, self.status = str(exc), ""
            return
        # Imports run through the same geometry validation Save does - no
        # special-cased leniency for imported data.
        error = self._validate(coilset)
        if error:
            self.error, self.status = f"Imported config failed validation: {error}", ""
            return
        deduped = config_io.dedupe_name(name)
        # The CoilSet's own config_name field is renamed to match too, not
        # just the record's name - same reasoning as clone() below: otherwise
        # the list/left panel says "X (imported)" while the form's
        # config_name field still shows whatever name was baked into the
        # imported file, which reads like a bug (and did - see docs/GUI.md).
        coilset.config_name = deduped
        record = save_config(coilset, name=deduped, config_id=None)  # always a fresh id, never overwrites
        self.refresh()
        self.load(record["id"])  # load() resets error/status - set them after, not before
        self.status = f"Imported {record['name']!r}" + (f" (renamed from {name!r})" if deduped != name else "")
        self.error = ""

    def delete(self) -> None:
        """Deletes any config, including built-ins - unlike
        coilset_repository.delete_config()'s default, which refuses
        built-ins. That guard is right for the webapp's shared
        reference-design library; this app's user wants every config,
        template or not, deletable, so this passes force=True instead of
        going through the guard."""
        if not self.selected_id:
            return
        from engine.repositories.coilset_repository import delete_config

        delete_config(self.selected_id, force=True)
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


def _draw_delete_confirmation(state: ConfigsPanelState) -> None:
    """Delete used to fire immediately with no confirmation - by explicit
    request every config (including built-ins) is deletable, but that
    combination already caused two real accidental deletions this session
    (Gen3, base_coil). This popup is the fix: Delete now only opens it."""
    # begin_popup_modal() returns a (bool, Optional[bool]) tuple, not a plain
    # bool - a non-empty tuple is always Python-truthy, so `if
    # imgui.begin_popup_modal(...):` alone would run end_popup() every
    # frame regardless of whether the popup is actually open. Unpack it.
    is_open, _ = imgui.begin_popup_modal(_DELETE_POPUP_ID, flags=imgui.WindowFlags_.always_auto_resize.value)
    if is_open:
        record = next((c for c in state.configs if c["id"] == state.selected_id), None)
        display_name = record["name"] if record else state.buffer.get("config_name", state.selected_id)
        imgui.text(f"Delete {display_name!r}?")
        imgui.text_disabled("This can't be undone.")
        imgui.separator()
        if imgui.button("Cancel"):
            imgui.close_current_popup()
        imgui.same_line()
        imgui.push_style_color(imgui.Col_.button.value, _ERROR_COLOR)
        if imgui.button("Delete permanently"):
            state.delete()
            imgui.close_current_popup()
        imgui.pop_style_color()
        imgui.end_popup()


def _poll_export_import_dialogs(state: ConfigsPanelState) -> None:
    if state._export_dialog is not None and state._export_dialog.ready():
        result = state._export_dialog.result()  # save_file() -> a single path string, "" if cancelled
        if result:
            state.export(result)
        state._export_dialog = None

    if state._import_dialog is not None and state._import_dialog.ready():
        results = state._import_dialog.result()  # open_file() -> a list of paths, [] if cancelled
        if results:
            state.import_from(results[0])
        state._import_dialog = None


def _draw_form(state: ConfigsPanelState) -> None:
    labels = field_layout.field_labels()
    types = field_layout.field_types()

    if imgui.button("Save"):
        state.save()
    imgui.same_line()

    imgui.begin_disabled(state.selected_id is None)
    if imgui.button("Clone"):
        state.clone()
    imgui.end_disabled()
    imgui.same_line()

    imgui.begin_disabled(state.selected_id is None)
    if imgui.button("Export"):
        default_name = str(state.buffer.get("config_name") or "config").strip() + ".json"
        state._export_dialog = pfd.save_file("Export config", default_path=default_name, filters=["JSON files", "*.json"])
    imgui.end_disabled()
    imgui.same_line()

    imgui.begin_disabled(state.selected_id is None)
    if imgui.button("Delete"):
        imgui.open_popup(_DELETE_POPUP_ID)
    imgui.end_disabled()
    _draw_delete_confirmation(state)

    if state.error:
        imgui.text_colored(_ERROR_COLOR, state.error)
    elif state.status:
        imgui.text_colored(_STATUS_COLOR, state.status)

    if not state.buffer:
        # A Delete/Clone click just above may have emptied the buffer this
        # same frame (e.g. deleting the config currently being edited) -
        # nothing left to render.
        return

    imgui.separator()

    # config_name is pulled out of FIELD_GROUPS["General"] for display
    # (it stays in FIELD_GROUPS itself so field_layout's drift-guard still
    # accounts for it) - it's the one field worth always having visible,
    # not tucked inside a collapsed section.
    _draw_field(state, "config_name", labels.get("config_name", "config_name"), types["config_name"])

    # Sections start expanded - collapsed-by-default meant every config you
    # opened showed nothing but a stack of headers.
    open_by_default = imgui.TreeNodeFlags_.default_open.value
    for group_name, field_names in field_layout.FIELD_GROUPS.items():
        if group_name == "General":
            continue
        if imgui.collapsing_header(f"{group_name}##group_{group_name}", flags=open_by_default):
            for name in field_names:
                _draw_field(state, name, labels.get(name, name), types[name])

    if imgui.collapsing_header("Advanced##group_advanced", flags=open_by_default):
        for name in field_layout.ADVANCED_FIELDS:
            _draw_field(state, name, labels.get(name, name), types[name])


def draw_list(state: ConfigsPanelState, size: imgui.ImVec2 = imgui.ImVec2(260, 0)) -> None:
    # Polled here unconditionally (not from _draw_form) since Import must
    # work with nothing selected - _draw_form's own early-return on an
    # empty buffer would otherwise sometimes skip polling its result away.
    _poll_export_import_dialogs(state)

    imgui.begin_child("configs_list_pane", size, imgui.ChildFlags_.borders.value)
    # Measured from the real content region *inside* the child (i.e. after
    # its own padding) and the real item spacing - deriving this from the
    # outer size.x instead clipped the second button off the panel edge.
    avail_w = imgui.get_content_region_avail().x
    button_width = (avail_w - imgui.get_style().item_spacing.x) / 2.0
    if imgui.button("+ New", imgui.ImVec2(button_width, 0)):
        state.new()
    imgui.same_line()
    if imgui.button("Import", imgui.ImVec2(button_width, 0)):
        state._import_dialog = pfd.open_file("Import config", filters=["JSON files", "*.json"])
    imgui.separator()
    for cfg in state.configs:
        label = f"{cfg['name']}##{cfg['id']}"
        selected = cfg["id"] == state.selected_id
        clicked, _ = imgui.selectable(label, selected)
        if clicked:
            state.load(cfg["id"])
    imgui.end_child()


def draw_form(state: ConfigsPanelState, size: imgui.ImVec2 = imgui.ImVec2(0, 0)) -> None:
    imgui.begin_child("configs_form_pane", size, imgui.ChildFlags_.borders.value)
    if state.selected_id is None and not state.buffer:
        imgui.text_wrapped("Select a config on the left, or click + New to create one.")
    else:
        _draw_form(state)
    imgui.end_child()
