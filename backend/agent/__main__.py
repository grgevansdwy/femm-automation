"""Lets this package run as `python -m agent`, provided backend/
is on sys.path so `agent` itself is importable - i.e. run it from
backend/:  cd backend && uv run python -m agent
"""

from agent.orchestrator import main

if __name__ == "__main__":
    main()
