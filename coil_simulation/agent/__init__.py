"""LLM-driven coilset search prototype - standalone, no gui/ dependency.

Run:  uv run --package coil_simulation python coil_simulation/experiments/agent_prototype.py
(the old script path is kept as a thin shim into this package - see that
file's docstring)

Every submodule here imports coil_simulation's own code bare (e.g.
`from engine import simulate`, `from repositories.coilset_repository import
get_coilset`), the same way models/repositories/services do - not as
`coil_simulation.engine`. That only resolves if coil_simulation/ itself is
on sys.path, which this does once, here, before any submodule's own
top-level imports run (Python always fully executes a package's __init__.py
before any of its submodules) - the same trick gui/src/gui/_sim_path.py
uses for the same reason. This also makes `agent` itself importable as a
top-level package, same as `repositories`/`models`/`services` are, since it
lives at the same directory depth.
"""

import sys
from pathlib import Path

_COIL_SIMULATION_ROOT = Path(__file__).resolve().parents[1]
if str(_COIL_SIMULATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_COIL_SIMULATION_ROOT))
