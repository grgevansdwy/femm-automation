from fastapi import APIRouter, Request

from .. import job_runner
from ..job_runner import JobStatus
from ..templating import templates

router = APIRouter()


@router.get("/")
def index(request: Request):
    from repositories.run_result_repository import RunResultRepository

    # In-flight jobs haven't been written to disk yet (see job_runner._persist_job),
    # so they're merged in here rather than read back from RESULTS_ROOT.
    in_flight = [
        {
            "run_id": job.id,
            "config_name": job.config_name,
            "distance": job.distance,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
        }
        for job in job_runner.JOBS.values()
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
    ]
    disk = RunResultRepository.list_results(root=job_runner.RESULTS_ROOT)
    # A run persisted with no created_at (e.g. an interrupted job) has None
    # here - normalize to "" so it sorts as oldest and home.html's
    # `row.created_at[:16]` doesn't crash, instead of failing the whole page.
    for row in disk:
        row["created_at"] = row.get("created_at") or ""
    rows = sorted(in_flight + disk, key=lambda r: r["created_at"], reverse=True)
    return templates.TemplateResponse(request, "home.html", {"rows": rows})
