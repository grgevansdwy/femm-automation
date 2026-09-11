"""Thin entry point - the real implementation moved to coil_simulation/agent/
(config.py/schema.py/prompts.py/scorer.py/llm_client.py/orchestrator.py).
Kept here, at this exact path, so the originally documented run command
still works unchanged.

Run:  uv run --package coil_simulation python coil_simulation/experiments/agent_prototype.py
Docs/data: coil_simulation/agent/docs/{hard_requirement_run,latest_run,free_variables}.md
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # coil_simulation/ - makes `agent` importable

from agent.orchestrator import main  # noqa: E402

if __name__ == "__main__":
    main()
