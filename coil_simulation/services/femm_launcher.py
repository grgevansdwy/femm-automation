# =============================================================================
# femm_launcher.py
#
# "Open this design in FEMM": takes the same CoilSet the solver takes, draws
# it in real FEMM.exe under Wine, and leaves the FEMM window open on the
# finished model for a human to look at, mesh, solve or edit by hand.
#
# The geometry is NOT redrawn here. build_view_script() calls
# engine.build_geometry() - the exact same routine engine.build_script() uses
# for a real solve - and only appends the three things a GUI session needs and
# a headless solve doesn't: the loop-driven circuit currents, an mi_saveas()
# so the model is a real .fem file on disk, and an mi_zoomnatural() so it
# opens framed instead of at FEMM's default viewport. So what the user sees in
# FEMM is the model that produced the numbers next to the button, not a second
# drawing routine that could drift from it.
#
# Unlike tests/wine_femm_backend.py (which runs a whole solve script headlessly
# and appends quit()), this deliberately does NOT call quit() and does NOT
# analyze: FEMM stays open showing the model. That also means this can't wait
# for the process - open_in_femm() launches it and returns immediately
# (the FEMM window outlives the call), so callers get no solve results here.
# The solve path is unchanged: engine.simulate() through xfemm's femmcli.
# =============================================================================
import os
import re
import shutil
import subprocess
from pathlib import Path

FEMM_EXE = os.environ.get(
    "FEMM_EXE",
    os.path.expanduser("~/.wine/drive_c/femm42/bin/femm.exe"),
)

# Models are written here rather than to a TemporaryDirectory (which
# wine_femm_backend.py can use because it waits for its process): FEMM is still
# holding the .fem file open long after this call returns, and a user who
# meshes/solves/edits it wants the file to still be there afterwards. One
# subdirectory per label, overwritten on each click, so repeated clicks on the
# same card don't pile up.
MODELS_ROOT = Path(__file__).resolve().parents[1] / "data" / "femm_models"

_FEM_FILE = "model.fem"
_SCRIPT_FILE = "open_in_femm.lua"


def check_available():
    """None if FEMM can actually be launched here, else a short human-readable
    reason it can't - so a GUI can say "Wine isn't installed" instead of
    failing silently or dumping a traceback."""
    if shutil.which("wine") is None:
        return "Wine isn't installed (or isn't on PATH) - FEMM is a Windows program and needs it."
    if not os.path.exists(FEMM_EXE):
        return f"FEMM not found at {FEMM_EXE} - set the FEMM_EXE environment variable to its location."
    return None


def slugify(label):
    """A filesystem-safe directory name from a human label (a config name, a
    candidate step, ...). Non-alphanumerics collapse to underscores so a name
    like 'RevA1 with Ferrite (copy)' can't produce a path FEMM then can't
    open."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(label)).strip("_")
    return slug[:60] or "model"


def build_view_script(coilset, distance, fem_file=_FEM_FILE):
    """The Lua source that draws `coilset` at RC separation `distance` (mm) in
    FEMM and stops with the model on screen. Pure text - runs nothing, so it's
    testable without Wine or a display."""
    from engine import build_geometry, turn_currents

    script = build_geometry(coilset, distance)

    # The solver drives one coil per solve (three solves, see
    # engine.build_script); a GUI session shows one model, so it gets the
    # first of those - the loop-driven case. Without this every circuit sits
    # at 0 A and an analysis run by hand in FEMM would produce a blank field
    # plot, which reads as "the button drew nothing".
    loop_turn_current, _, _ = turn_currents(coilset)
    script.set_circuit_currents(loop_turn_current, coilset.num_loop_turns,
                                0, coilset.num_tank_turns, 0, coilset.num_rc_turns)

    script.mi_saveas(fem_file)
    # LuaScript.mi_zoom() is a deliberate no-op (femmcli has no viewport) -
    # here there is one, and without this the model opens off-screen at FEMM's
    # default zoom.
    script.raw("mi_zoomnatural()")
    return script.render()


def model_dir(label):
    return MODELS_ROOT / slugify(label)


def open_in_femm(coilset, distance, label="model"):
    """Draws `coilset` at `distance` mm in real FEMM under Wine and leaves the
    window open on it. Returns the path of the .fem file FEMM writes.

    Blocking only for as long as it takes to write the script and hand it to
    Wine (winepath can take a second on a cold wineserver) - the FEMM process
    itself is detached and outlives this call, so a GUI should still call this
    off its frame thread. Raises RuntimeError if FEMM can't be launched at
    all (see check_available())."""
    unavailable = check_available()
    if unavailable:
        raise RuntimeError(unavailable)

    workdir = model_dir(label)
    workdir.mkdir(parents=True, exist_ok=True)
    script_path = workdir / _SCRIPT_FILE
    script_path.write_text(build_view_script(coilset, distance))

    win_script_path = subprocess.run(
        ["winepath", "-w", str(script_path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Popen, not run(): FEMM stays open for the user, so waiting for it would
    # hang the caller until they close the window. start_new_session detaches
    # it from the GUI's process group, so quitting the GUI doesn't kill FEMM.
    subprocess.Popen(
        ["wine", FEMM_EXE, f"-lua-script={win_script_path}"],
        cwd=str(workdir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return workdir / _FEM_FILE
