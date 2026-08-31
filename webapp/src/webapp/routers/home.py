from fastapi import APIRouter, Request

from .. import job_runner
from ..templating import templates

router = APIRouter()


@router.get("/")
def index(request: Request):
    jobs = sorted(job_runner.JOBS.values(), key=lambda job: job.created_at, reverse=True)
    return templates.TemplateResponse(request, "home.html", {"jobs": jobs})
