"""LLM-driven coilset search agent - standalone, no frontend/ dependency.

Run:  cd backend && uv run python -m agent

Layout - two phases, each laid out like engine/ (models/ = plain data and rules,
services/ = behavior, tests/), which this builds on:
  input_chat/   the interview: a chat turns a request into an AgentSpec
  femm_agent/   the search: proposes candidates and simulates them against that AgentSpec
Each phase's services/ also holds its runner.py - the background-thread entry point the GUI
calls into. Shared by both, at this level: services/ (llm_client), repositories/ (ai_run_repository - one run store for both phases), config.py.

Every submodule here imports backend's own code as top-level packages
(e.g. `from engine.core import simulate`, `from engine.repositories.coilset_repository
import get_coilset`, `from agent.femm_agent.services.scorer import ...`) - not as
`backend.engine`. That only resolves if backend/ itself is
on sys.path, which this does once, here, before any submodule's own
top-level imports run (Python always fully executes a package's __init__.py
before any of its submodules) - the same trick frontend/src/frontend/_sim_path.py
uses for the same reason. This also makes `agent` itself importable as a
top-level package, same as `engine` is, since it lives at the same directory
depth.
"""

import sys
from pathlib import Path

_COIL_SIMULATION_ROOT = Path(__file__).resolve().parents[1]
if str(_COIL_SIMULATION_ROOT) not in sys.path:
    sys.path.insert(0, str(_COIL_SIMULATION_ROOT))
