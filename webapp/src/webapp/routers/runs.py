from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from .. import config_store, job_runner, template_registry
from ..job_runner import JobStatus
from ..templating import templates

router = APIRouter()

try:
    from run_comparison import DEFAULT_RC_TEST_DISTANCES
except ImportError:
    # noelle's run_comparison.py (plan section 2) may not exist yet in this
    # checkout - fall back to the same default distance list it documents,
    # so /runs/new still renders instead of failing the whole router import.
    DEFAULT_RC_TEST_DISTANCES = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 75, 100]


def _selectable_items() -> list[dict]:
    """Templates + saved configs for the /runs/new picker, tagged with a
    'template:<name>' / 'config:<id>' key that _resolve_selection() reverses."""
    items = []
    try:
        for name, coilset in template_registry.list_templates().items():
            items.append({"key": f"template:{name}", "label": coilset.config_name.strip()})
    except ModuleNotFoundError:
        pass
    for record in config_store.list_configs():
        items.append({"key": f"config:{record['id']}", "label": record["name"]})
    return items


def _resolve_selection(key: str):
    """key is 'template:<name>' or 'config:<id>'. Returns (CoilSet, display_label)."""
    kind, _, ident = key.partition(":")
    if kind == "template":
        coilset = template_registry.get_template(ident)
        return coilset, coilset.config_name.strip()
    if kind == "config":
        record = config_store.load_config(ident)
        if record is None:
            raise KeyError(f"No such config: {ident}")
        from models.coilset import CoilSet

        return CoilSet(**record["coilset"]), record["name"]
    raise ValueError(f"Malformed selection key: {key!r}")


@router.get("/runs/new")
def new_run_form(request: Request):
    return templates.TemplateResponse(
        request,
        "run_new.html",
        {"items": _selectable_items(), "default_distances": DEFAULT_RC_TEST_DISTANCES},
    )


@router.post("/runs/new")
async def create_run(request: Request):
    form = await request.form()
    selected_keys = form.getlist("selection")
    distances_raw = form.get("distances", "")
    distances = [float(d.strip()) for d in distances_raw.split(",") if d.strip()] or list(DEFAULT_RC_TEST_DISTANCES)

    if not selected_keys:
        return templates.TemplateResponse(
            request,
            "run_new.html",
            {
                "items": _selectable_items(),
                "default_distances": DEFAULT_RC_TEST_DISTANCES,
                "error": "Select at least one config to run.",
            },
            status_code=422,
        )

    coilsets, labels = [], []
    for key in selected_keys:
        coilset, label = _resolve_selection(key)
        coilsets.append(coilset)
        labels.append(label)

    job = job_runner.submit_job(coilsets, labels, distances)
    return RedirectResponse(f"/runs/{job.id}", status_code=303)


@router.get("/runs/{job_id}")
def run_detail(request: Request, job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None:
        return PlainTextResponse(f"No such job: {job_id}", status_code=404)
    return templates.TemplateResponse(request, "run_detail.html", {"job": job})


@router.get("/runs/{job_id}/status-fragment")
def run_status_fragment(request: Request, job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None:
        return PlainTextResponse(f"No such job: {job_id}", status_code=404)
    polling = job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    return templates.TemplateResponse(
        request, "partials/status_fragment.html", {"job": job, "polling": polling}
    )


@router.get("/runs/{job_id}/log-tail")
def run_log_tail(job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None or job.log_path is None or not job.log_path.exists():
        return PlainTextResponse("")
    return PlainTextResponse(job.log_path.read_text())


@router.get("/runs/{job_id}/figure.svg")
def run_figure(job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None or job.result_dir is None:
        return PlainTextResponse(f"No figure for job: {job_id}", status_code=404)
    matches = sorted(job.result_dir.glob("*.svg"))
    if not matches:
        return PlainTextResponse(f"No figure file found in {job.result_dir}", status_code=404)
    return FileResponse(matches[0], media_type="image/svg+xml")


@router.get("/runs/{job_id}/log")
def run_log(job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None or job.log_path is None or not job.log_path.exists():
        return PlainTextResponse(f"No log for job: {job_id}", status_code=404)
    return FileResponse(job.log_path, media_type="text/plain", filename=job.log_path.name)


@router.get("/runs/{job_id}/summary.json")
def run_summary_json(job_id: str):
    job = job_runner.JOBS.get(job_id)
    if job is None or job.result_dir is None:
        return PlainTextResponse(f"No summary for job: {job_id}", status_code=404)
    matches = sorted(job.result_dir.glob("summary_*.json"))
    if not matches:
        return PlainTextResponse(f"No summary file found in {job.result_dir}", status_code=404)
    return FileResponse(matches[0], media_type="application/json", filename=matches[0].name)
