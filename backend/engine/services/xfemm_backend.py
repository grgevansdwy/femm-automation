# =============================================================================
# xfemm_backend.py
#
# NOT CURRENTLY WIRED INTO engine/core.py, but the bug that blocked it is FIXED.
# engine/core.py and engine/services/femm_service.py were reverted back to the Wine/pyfemm
# implementation (legacy/wine_backend/, removed from the tree - in git history at commit 321b8d7) after mutual-inductance terms
# (M12/M13/M23, so k12/k13/k23) came out wrong on a production multi-coil
# config - mo_getcircuitproperties()'s flux linkage was rotated by an exact
# 90 degrees under femmcli relative to real FEMM under Wine.
#
# ROOT CAUSE FOUND: xfemm's cfemm/fpproc/fpproc.cpp::GetFluxLinkage(), for a
# circuit carrying zero prescribed current (i.e. every coil except the one
# being driven), computed FluxLinkage = GetVoltageDrop(circnum)/(2*PI*Frequency)
# - dividing by the real angular frequency instead of the imaginary j*omega.
# One-line fix in backend/engine/vendor/xfemm_flux_linkage_fix.patch, applied
# to the vendored femmcli binary (backend/engine/vendor/README.md has the
# full writeup and rebuild instructions). Re-validated against real FEMM under
# Wine on three structurally different production configs (base_coil.json,
# Gen3_backplane.json, seedcoil.json) - every self- and mutual-inductance/ESR
# term now matches to ~1e-8%.
#
# Wiring this back into engine/core.py is a deliberate decision, not a technical
# blocker anymore - see DESIGN.md.
#
# Everything below this notice is otherwise a working implementation:
# replaces "real FEMM.exe under Wine, driven live over pyfemm/COM" with
# "xfemm's femmcli CLI, driven by a generated Lua script, run once".
#
# femmcli only runs a whole, self-contained Lua script start-to-finish - there
# is no live call/response session like pyfemm's COM link, so this is not a
# drop-in femm-module replacement. LuaScript accumulates Lua source lines for
# the "write-only" mi_* calls (geometry/materials/circuits - these are reused
# as-is by femm_service.py via a femm_like parameter), plus the post-processing
# math femm_service.loop_inductance()/loop_ESR() used to do live in Python -
# that math has to be re-expressed as generated Lua instead, since results
# aren't available until the whole script finishes.
#
# See docs/archive/xfemm_spike_notes.md in git history for the accuracy/performance validation this is
# built on (single-coil cases only - that's where this bug wasn't visible),
# and git history (legacy/wine_backend/ at commit 321b8d7) for the restored implementation.
# =============================================================================
import os
import re
import subprocess
import tempfile

XFEMM_BIN_DIR = os.environ.get(
    "XFEMM_BIN_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "xfemm-4.0-linux64"),
)

_RESULT_FILE = "result.txt"

# Sums a circuit's voltage and flux linkage over its turns in one pass - the
# Lua equivalent of femm_service.loop_inductance()/loop_ESR(), which each
# looped over the same turns separately calling mo_getcircuitproperties twice
# per turn. Division by driving current and the final real-part extraction
# stay in Python (_parse_femm_complex/get_inductances below), exactly as the
# original functions did, since Lua's complex-number API isn't relied on here.
_PRELUDE = """
function sum_circuit(name, n)
    local voltage_sum = 0
    local flux_sum = 0
    local i
    for i = 0, n - 1 do
        local cur, v, fl = mo_getcircuitproperties(name .. i)
        voltage_sum = voltage_sum + v
        flux_sum = flux_sum + fl
    end
    return voltage_sum, flux_sum
end
"""


def _lua_literal(value):
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _lua_args(args):
    return ", ".join(_lua_literal(a) for a in args)


class LuaScript:
    """Accumulates one femmcli Lua script. Method names mirror the `femm`
    module's so femm_service.py's write-only geometry functions can take one
    of these in place of the real `femm` module (see `femm_like` parameters
    in femm_service.py)."""

    def __init__(self):
        self.lines = [_PRELUDE]
        self._results = {}

    def _call(self, name, *args):
        self.lines.append(f"{name}({_lua_args(args)})")

    # -- direct passthroughs used by femm_service.py / engine/core.py --
    def openfemm(self, *a):
        pass  # no live session to start under femmcli - no Lua equivalent needed

    def closefemm(self):
        pass  # no live session to release

    def newdocument(self, kind):
        self._call('newdocument', kind)

    def mi_probdef(self, *a): self._call('mi_probdef', *a)
    def mi_getmaterial(self, *a): self._call('mi_getmaterial', *a)
    def mi_addmaterial(self, *a): self._call('mi_addmaterial', *a)
    def mi_modifymaterial(self, *a): self._call('mi_modifymaterial', *a)
    def mi_addblocklabel(self, *a): self._call('mi_addblocklabel', *a)
    def mi_selectlabel(self, *a): self._call('mi_selectlabel', *a)
    def mi_setblockprop(self, *a): self._call('mi_setblockprop', *a)
    def mi_clearselected(self): self._call('mi_clearselected')
    def mi_drawrectangle(self, *a): self._call('mi_drawrectangle', *a)
    def mi_drawarc(self, *a): self._call('mi_drawarc', *a)
    def mi_selectrectangle(self, *a): self._call('mi_selectrectangle', *a)
    def mi_setgroup(self, *a): self._call('mi_setgroup', *a)
    def mi_selectgroup(self, *a): self._call('mi_selectgroup', *a)
    def mi_movetranslate(self, *a): self._call('mi_movetranslate', *a)
    def mi_addcircprop(self, *a): self._call('mi_addcircprop', *a)
    def mi_modifycircprop(self, *a): self._call('mi_modifycircprop', *a)
    def mi_saveas(self, *a): self._call('mi_saveas', *a)

    def mi_zoom(self, *a):
        pass  # viewport only, no solve effect, and no GUI exists to view it

    def raw(self, line):
        """Appends a literal line of Lua. The escape hatch for calls that
        only mean something outside femmcli - engine/services/femm_launcher.py's
        mi_zoomnatural(), which needs the viewport mi_zoom() above is right to
        drop. Not for geometry: anything the solver sees should go through a
        named method, so femm_service.py's `femm_like` duck-typing keeps
        working against the real `femm` module too."""
        self.lines.append(line)

    def mi_makeABC(self, *a):
        raise NotImplementedError(
            "mi_makeABC is broken under femmcli (mesh-time 'invalid block type "
            "index' error - see docs/archive/xfemm_spike_notes.md in git history). Use far_field_boundary() "
            "instead."
        )

    def far_field_boundary(self):
        """Replaces init_femm()'s mi_makeABC(4, 400, 0, 0) - broken under
        femmcli. A plain far-field Dirichlet A=0 box at the same ~400mm scale
        production already used, validated to ~10^-7% agreement against real
        FEMM's mi_makeABC in the spike (docs/archive/xfemm_spike_notes.md in git history)."""
        self.mi_drawrectangle(0, -400, 400, 400)
        self._call('mi_selectsegment', 200, 400)
        self._call('mi_selectsegment', 200, -400)
        self._call('mi_selectsegment', 400, 0)
        self._call('mi_setsegmentprop', 'A=0', 0, 1, 0, 0)
        self.mi_clearselected()

    def set_circuit_currents(self, loop_current, num_loop_turns,
                              tank_current, num_tank_turns, rc_current, num_rc_turns):
        """Lua port of femm_service.set_circuit_currents()."""
        for i in range(num_loop_turns):
            self.mi_modifycircprop(f'loop{i}', 1, loop_current)
        for i in range(num_tank_turns):
            self.mi_modifycircprop(f'tank{i}', 1, tank_current)
        for i in range(num_rc_turns):
            self.mi_modifycircprop(f'rc{i}', 1, rc_current)

    def solve_and_extract(self, loop_current, num_loop_turns,
                           tank_current, num_tank_turns, rc_current, num_rc_turns):
        """One mi_analyze()+mi_loadsolution(), then sums whichever circuits
        are driven (current > 0) - the Lua port of
        femm_service.get_inductances()'s per-solve body. Registers the
        populated result keys (e.g. 'L_loop', 'M_tank_loop', 'R_loop') for
        `render()` to write out."""
        # femmcli (unlike a live FEMM/COM session) requires an explicit save
        # before it will analyze: "A data file must be loaded, or the current
        # data must saved." Matches the original get_inductances(), which
        # also called mi_saveas() before every mi_analyze().
        self.mi_saveas('model.fem')
        self._call('mi_analyze', 1)
        self._call('mi_loadsolution')

        def sum_into(role, circuit, num_turns, driving_current, want_flux, want_voltage):
            v = f"{role}_{circuit}_v"
            f = f"{role}_{circuit}_f"
            self.lines.append(f"local {v}, {f} = sum_circuit('{circuit}', {num_turns})")
            if want_voltage:
                key = f"R_{role}"
                self.lines.append(f"{key} = {v} / {_lua_literal(float(driving_current))}")
                self._results[key] = key
            if want_flux:
                key = f"L_{role}" if role == circuit else f"M_{circuit}_{role}"
                self.lines.append(f"{key} = {f} / {_lua_literal(float(driving_current))}")
                self._results[key] = key

        if loop_current > 0:
            sum_into('loop', 'loop', num_loop_turns, loop_current, True, True)
            sum_into('loop', 'tank', num_tank_turns, loop_current, True, False)
            sum_into('loop', 'rc', num_rc_turns, loop_current, True, False)
        if tank_current > 0:
            sum_into('tank', 'loop', num_loop_turns, tank_current, True, False)
            sum_into('tank', 'tank', num_tank_turns, tank_current, True, True)
            sum_into('tank', 'rc', num_rc_turns, tank_current, True, False)
        if rc_current > 0:
            sum_into('rc', 'loop', num_loop_turns, rc_current, True, False)
            sum_into('rc', 'tank', num_tank_turns, rc_current, True, False)
            sum_into('rc', 'rc', num_rc_turns, rc_current, True, True)

    def render(self):
        """The accumulated Lua source, with the result-file epilogue appended
        when there is anything to write. A script that never called
        solve_and_extract() has no results and gets no epilogue - that's
        engine/services/femm_launcher.py's draw-only case, which is opened in the
        FEMM GUI rather than parsed, so an empty result.txt would be a stray
        file and nothing else."""
        lines = list(self.lines)
        if not self._results:
            return "\n".join(lines) + "\n"
        lines.append(f"local fh = openfile('{_RESULT_FILE}', 'w')")
        for key in self._results:
            lines.append(f"write(fh, '{key}=', {key}, '\\n')")
        lines.append("closefile(fh)")
        lines.append("write('DONE\\n')")
        return "\n".join(lines) + "\n"


def parse_femm_number(text):
    """Parses FEMM/femmcli's stringified numbers, e.g. '6.98e-08-I*6.09e-08'
    or a plain real '1.0' with no imaginary part (matches the format observed
    from both femmcli and real FEMM in docs/archive/xfemm_spike_notes.md in git history). Shared with
    engine/tests/wine_femm_backend.py, which parses the identical result.txt
    format written by the same generated Lua script running through real
    FEMM under Wine instead of femmcli."""
    text = text.strip()
    if "I*" not in text:
        return float(text)
    sign = -1.0 if "-I*" in text else 1.0
    real_part, imag_part = re.split(r"[+-]I\*", text)
    return complex(float(real_part), sign * float(imag_part))


SOLVE_TIMEOUT_S = 120


class SolverTimeout(RuntimeError):
    """One femmcli solve ran past SOLVE_TIMEOUT_S. Its own class so a caller
    that only has the job's repr(exc) text (frontend's JobRunner) can still
    tell a timeout from a crash by the name it starts with."""


def run_femmcli(script_text):
    """Writes `script_text` to a fresh per-call temp directory, runs it
    through femmcli, and returns the parsed key->float/complex result dict.
    Each call gets its own directory so concurrent calls never collide -
    replaces the shared 'tmp.fem' filename get_inductances() used to write."""
    with tempfile.TemporaryDirectory(prefix="xfemm_job_") as workdir:
        script_path = os.path.join(workdir, "model.lua")
        with open(script_path, "w") as f:
            f.write(script_text)

        femmcli = os.path.join(XFEMM_BIN_DIR, "femmcli")
        init_lua = os.path.join(XFEMM_BIN_DIR, "init.lua")
        try:
            proc = subprocess.run(
                [femmcli, "--lua-base-dir", XFEMM_BIN_DIR, "--lua-init", init_lua,
                 "--lua-script", script_path],
                cwd=workdir, capture_output=True, text=True, timeout=SOLVE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise SolverTimeout(f"femmcli did not finish within {SOLVE_TIMEOUT_S} s") from None
        result_path = os.path.join(workdir, _RESULT_FILE)
        if proc.returncode != 0 or not os.path.exists(result_path):
            raise RuntimeError(
                f"femmcli failed (exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )

        results = {}
        with open(result_path) as f:
            for line in f:
                if "=" not in line:
                    continue
                key, _, value = line.strip().partition("=")
                results[key] = parse_femm_number(value)
        return results
