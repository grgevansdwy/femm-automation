"""Tests the script femm_launcher builds and the guards around launching it -
never actually starts Wine or FEMM (open_in_femm() is the only function that
does, and it's a thin winepath+Popen wrapper around build_view_script()).

The point of every assertion here is the same one the module exists for: the
model the GUI's "Open in FEMM" button opens has to be the model the solver
solves, not a second drawing of it.
"""

import pytest

from engine import core
from engine.repositories.coilset_repository import get_coilset
from engine.services import femm_launcher


@pytest.fixture
def coilset():
    return get_coilset("base_coil")


def test_the_drawn_geometry_is_byte_identical_to_the_solvers(coilset):
    """The whole reason build_geometry() was split out of build_script():
    every geometry line the viewer emits is the solve script's own prefix,
    in the same order, not a re-implementation."""
    view_lines = femm_launcher.build_view_script(coilset, 5.0).splitlines()
    solve_lines = core.build_script(coilset, 5.0).render().splitlines()

    geometry_length = solve_lines.index("mi_modifycircprop('loop0', 1, 10.0)")
    assert view_lines[:geometry_length] == solve_lines[:geometry_length]


def test_the_view_script_drives_the_loop_and_nothing_else(coilset):
    """A GUI session shows one model, so it gets the solver's first solve -
    the loop-driven case. Every other circuit sits at 0 A."""
    script = femm_launcher.build_view_script(coilset, 5.0)

    loop_current, _, _ = core.turn_currents(coilset)
    assert f"mi_modifycircprop('loop0', 1, {loop_current!r})" in script
    assert "mi_modifycircprop('tank0', 1, 0)" in script
    assert "mi_modifycircprop('rc0', 1, 0)" in script


def test_the_view_script_saves_and_frames_the_model_but_never_solves(coilset):
    script = femm_launcher.build_view_script(coilset, 5.0)

    assert script.rstrip().endswith("mi_saveas('model.fem')\nmi_zoomnatural()")
    # No solve, no result file, and no quit() - FEMM is left open on the model
    # for a human, unlike tests/wine_femm_backend.py's headless run.
    assert "mi_analyze" not in script
    assert "result.txt" not in script
    assert "quit()" not in script


def test_distance_moves_the_rc_the_same_way_a_solve_does(coilset):
    near = femm_launcher.build_view_script(coilset, 2.0)
    far = femm_launcher.build_view_script(coilset, 40.0)

    assert near != far
    # The RC block label sits at the separation the caller asked for, exactly
    # as engine.core.build_geometry() places it for a solve.
    assert near.count("mi_addblocklabel") == far.count("mi_addblocklabel")


def test_each_label_gets_its_own_model_directory(coilset):
    assert femm_launcher.model_dir("Gen3") != femm_launcher.model_dir("Gen3 proposed step 3")
    # The same label always resolves to the same directory, so repeated clicks
    # overwrite one model instead of piling up.
    assert femm_launcher.model_dir("Gen3") == femm_launcher.model_dir("Gen3")


def test_slugify_keeps_a_real_config_name_usable_as_a_path():
    assert femm_launcher.slugify("RevA1 with Ferrite (copy)") == "RevA1_with_Ferrite_copy"
    assert "/" not in femm_launcher.slugify("a/b")
    assert femm_launcher.slugify("///") == "model"  # never an empty path segment


def test_a_missing_femm_install_is_reported_not_launched(monkeypatch, coilset):
    monkeypatch.setattr(femm_launcher, "FEMM_EXE", "/nonexistent/femm.exe")

    reason = femm_launcher.check_available()
    assert reason is not None and "/nonexistent/femm.exe" in reason

    with pytest.raises(RuntimeError, match="/nonexistent/femm.exe"):
        femm_launcher.open_in_femm(coilset, 5.0, label="never launched")
