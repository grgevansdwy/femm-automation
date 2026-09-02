"""Shared Jinja2Templates instance, imported by main.py and by routers.

Kept in its own module (rather than constructed in main.py) so routers can
import it without importing main.py and risking a circular import.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parents[2]  # webapp/

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
