"""Lets this package run as `python -m agent`, provided coil_simulation/
is already on sys.path (e.g. cwd is coil_simulation/) so `agent` itself is
importable. The backward-compatible shim at
coil_simulation/experiments/agent_prototype.py handles that bootstrap for
you and is the reliable way to run this from the repo root - prefer it
unless you know cwd already puts coil_simulation/ on sys.path.
"""

from agent.orchestrator import main

if __name__ == "__main__":
    main()
