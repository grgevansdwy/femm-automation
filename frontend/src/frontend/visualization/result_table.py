"""The table of the 12 solved quantities (with units) - drawn for a manual run's
result (screen_manual/runs_panel.py) and for the baseline's own solve in the AI
Agent mode (screen_agent/agent_panel.py), so it lives here rather than in either.
"""

from imgui_bundle import imgui

RESULT_FIELDS = (
    ("L_loop", "H"), ("L_tank", "H"), ("L_rc", "H"),
    ("M12", "H"), ("M13", "H"), ("M23", "H"),
    ("loop_esr", "Ohm"), ("tank_esr", "Ohm"), ("rc_esr", "Ohm"),
    ("k12", ""), ("k13", ""), ("k23", ""),
)


def draw_result_table(result: dict, table_id: str = "result_table") -> None:
    """The 12 solved quantities with units."""
    if imgui.begin_table(table_id, 3, imgui.TableFlags_.borders.value):
        imgui.table_setup_column("Field")
        imgui.table_setup_column("Value")
        imgui.table_setup_column("Unit")
        imgui.table_headers_row()
        for name, unit in RESULT_FIELDS:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(name)
            imgui.table_next_column()
            imgui.text(f"{result[name]:.6g}")
            imgui.table_next_column()
            imgui.text(unit)
        imgui.end_table()
