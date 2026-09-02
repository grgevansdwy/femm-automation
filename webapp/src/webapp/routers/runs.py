from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from .. import job_runner
from ..job_runner import JobStatus
from ..templating import templates

router = APIRouter()

try:
    from engine import DEFAULT_RC_TEST_DISTANCE
except ImportError:
    # coil_simulation's engine.py may not exist yet in this checkout - fall
    # back to the same default it documents, so /runs/new still renders
    # instead of failing the whole router import.
    DEFAULT_RC_TEST_DISTANCE = 5.0


def _run_new_context(**overrides) -> dict:
    from repositories import coilset_repository as cc

    context = {
        "configs": cc.list_configs(),
        "default_distance": DEFAULT_RC_TEST_DISTANCE,
        "error": None,
    }
    context.update(overrides)
    return context


@router.get("/runs/new")
def new_run_form(request: Request):
    return templates.TemplateResponse(request, "run_new.html", _run_new_context())


@router.post("/runs/new")
async def create_run(request: Request):
    from repositories import coilset_repository as cc

    form = await request.form()
    config_id = (form.get("config_id") or "").strip()
    distance_raw = (form.get("distance") or "").strip()

    if not config_id:
        return templates.TemplateResponse(
            request, "run_new.html",
            _run_new_context(error="Select a config to run."), status_code=422,
        )
    try:
        distance = float(distance_raw) if distance_raw else DEFAULT_RC_TEST_DISTANCE
    except ValueError:
        return templates.TemplateResponse(
            request, "run_new.html",
            _run_new_context(error=f"Distance must be a number: {distance_raw!r}"), status_code=422,
        )

    try:
        coilset = cc.get_coilset(config_id)
    except KeyError:
        return templates.TemplateResponse(
            request, "run_new.html",
            _run_new_context(error=f"No such config: {config_id}"), status_code=422,
        )

    job = job_runner.submit_job(coilset, config_id, coilset.config_name.strip(), distance)
    return RedirectResponse(f"/runs/{job.id}", status_code=303)


def _run_row(job_id: str):
    """Prefers the live in-memory job (for polling); falls back to disk so a
    run detail page still works after a restart."""
    job = job_runner.JOBS.get(job_id)
    if job is not None:
        return {
            "run_id": job.id, "config_name": job.config_name, "distance": job.distance,
            "status": job.status.value, "error": job.error, "result": job.result,
            "polling": job.status in (JobStatus.QUEUED, JobStatus.RUNNING),
        }
    from repositories.run_result_repository import RunResultRepository

    record = RunResultRepository.load_result(job_id, root=job_runner.RESULTS_ROOT)
    if record is None:
        return None
    return {
        "run_id": record["run_id"], "config_name": record["config_name"], "distance": record["distance"],
        "status": record["status"], "error": record["error"], "result": record["result"],
        "polling": False,
    }


@router.get("/runs/{job_id}")
def run_detail(request: Request, job_id: str):
    row = _run_row(job_id)
    if row is None:
        return PlainTextResponse(f"No such run: {job_id}", status_code=404)
    return templates.TemplateResponse(request, "run_detail.html", {"row": row})


@router.get("/runs/{job_id}/status-fragment")
def run_status_fragment(request: Request, job_id: str):
    row = _run_row(job_id)
    if row is None:
        return PlainTextResponse(f"No such run: {job_id}", status_code=404)
    return templates.TemplateResponse(request, "partials/status_fragment.html", {"row": row})
