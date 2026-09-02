"""Puts coil_simulation/ on sys.path so its bare, root-relative imports resolve.

coil_simulation's modules import each other as `from repositories import coilset_repository`,
`from models.coilset import CoilSet`, etc. - relative to coil_simulation/ being on
sys.path[0], the same way `main.py` already relies on via its cwd
when run as a script. Import this module before importing anything that
transitively imports coil_simulation code (main.py does this first, before its routers).
"""

import os
import sys
from pathlib import Path

SIM_DIR = Path(os.environ.get("SIM_DIR", Path(__file__).resolve().parents[3] / "coil_simulation"))

if str(SIM_DIR) not in sys.path:
    sys.path.insert(0, str(SIM_DIR))
