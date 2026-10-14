# =============================================================================
# wine_femm_backend.py
#
# Runs a generated Lua script (from engine/services/xfemm_backend.LuaScript.render())
# through real FEMM.exe under Wine, headlessly - the same "-lua-script=" CLI
# mode documented in DESIGN.md, and the same mechanism used to
# find and fix the mutual-inductance bug (DESIGN.md). This is NOT a
# production backend - it lives in tests/, not services/, because it exists
# only so compare_wine_vs_xfemm.py can run the exact same script through both
# engines and diff the results.
#
# Real FEMM's Lua interpreter understands the same mi_*/mo_* functions and
# openfile/write/closefile calls xfemm's does (xfemm is a port of FEMM's
# solver core, and init.lua's wrappers were built to mirror FEMM's own
# function names) - so the identical rendered script that runs through
# femmcli also runs unmodified through real FEMM, and both write the same
# result.txt format.
# =============================================================================
import os
import subprocess
import tempfile

from engine.services.xfemm_backend import parse_femm_number

FEMM_EXE = os.environ.get(
    "FEMM_EXE",
    os.path.expanduser("~/.wine/drive_c/femm42/bin/femm.exe"),
)

_RESULT_FILE = "result.txt"

# Real FEMM under Wine is far slower than femmcli and mesh size varies a lot
# across configs - generous timeout, not tuned.
_TIMEOUT_SECONDS = 300


def run_wine_femm(script_text):
    """Writes `script_text` to a fresh per-call temp directory, runs it
    through real FEMM.exe under Wine, and returns the parsed key->float/
    complex result dict - same shape as xfemm_backend.run_femmcli()'s return
    value, so callers can diff them directly."""
    with tempfile.TemporaryDirectory(prefix="wine_femm_job_") as workdir:
        script_path = os.path.join(workdir, "model.lua")
        with open(script_path, "w") as f:
            # Unlike femmcli (which exits on its own once the script ends),
            # real FEMM under Wine keeps its window open until a script
            # explicitly calls quit() - without this the process never
            # exits and run_wine_femm() would hang until _TIMEOUT_SECONDS.
            f.write(script_text + "\nquit()\n")

        win_script_path = subprocess.run(
            ["winepath", "-w", script_path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        proc = subprocess.run(
            ["wine", FEMM_EXE, f"-lua-script={win_script_path}"],
            cwd=workdir, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
        )
        result_path = os.path.join(workdir, _RESULT_FILE)
        if not os.path.exists(result_path):
            raise RuntimeError(
                f"wine femm.exe produced no {_RESULT_FILE} (exit "
                f"{proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
            )

        results = {}
        with open(result_path) as f:
            for line in f:
                if "=" not in line:
                    continue
                key, _, value = line.strip().partition("=")
                results[key] = parse_femm_number(value)
        return results
