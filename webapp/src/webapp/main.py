import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import _sim_path  # noqa: F401 - must run before any coil_simulation-dependent import below
from . import job_runner
from .routers import configs, home, runs

BASE_DIR = Path(__file__).resolve().parents[2]  # webapp/


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_task = asyncio.create_task(job_runner.worker_loop())
    try:
        yield
    finally:
        worker_task.cancel()


app = FastAPI(title="FEMM WPT Coil Simulation Tool", lifespan=lifespan)

# mkdir so mounting doesn't fail before the templates/static track (plan
# section 6, separate) has created these directories' real content.
static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(home.router)
app.include_router(configs.router)
app.include_router(runs.router)
