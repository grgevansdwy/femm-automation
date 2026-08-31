"""Puts noelle/ on sys.path so its bare, noelle-root-relative imports resolve.

noelle's modules import each other as `from repositories import coilset_repository`,
`from models.coilset import CoilSet`, etc. - relative to noelle/ being on
sys.path[0], the same way `compare_coilsets.py` already relies on via its cwd
when run as a script. Import this module before importing anything that
transitively imports noelle code (main.py does this first, before its routers).
"""

import os
import sys
from pathlib import Path

NOELLE_DIR = Path(os.environ.get("NOELLE_DIR", Path(__file__).resolve().parents[3] / "noelle"))

if str(NOELLE_DIR) not in sys.path:
    sys.path.insert(0, str(NOELLE_DIR))
