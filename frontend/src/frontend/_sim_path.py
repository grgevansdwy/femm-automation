"""Puts backend/ on sys.path so `import engine` / `import agent` resolve.

backend's two packages import each other as top-level names -
`from engine.repositories import coilset_repository`, `from engine.models.coilset import CoilSet`,
`from agent.femm_agent.services.scorer import ...` - relative to backend/ being on
sys.path, the same way `python -m engine.main` relies on via its cwd. Import this
module before importing anything that transitively imports backend code
(main.py does this first, before anything else).
"""

import os
import sys
from pathlib import Path

SIM_DIR = Path(os.environ.get("SIM_DIR", Path(__file__).resolve().parents[3] / "backend"))

if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
