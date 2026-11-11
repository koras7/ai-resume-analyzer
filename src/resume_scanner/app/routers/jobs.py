import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import ValidationError
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from resume_scanner import cache, jobs, metrics
from resume_scanner.app.models.schemas import (
    JobStatusResponse,
    JobSubmitResponse,
    ResumeAnalysisResponse,
)
from resume_scanner.app.openai_client import DEFAULT_MODEL

router = APIRouter()

QUEUE_UNAVAILABLE_DETAIL = "Job queue is unavailable."
JOB_FAILED_DETAIL = "Analysis failed. Submit the resume again."

# A cache hit at submit time needs no job at all: the response carries a
# pseudo job ID with this prefix, and the status endpoint resolves it back
# to the cache entry instead of to RQ. Must match the operation/model that
# resume._analyze_resume passes to chat_completion.
CACHED_JOB_PREFIX = "cached-"
CACHE_OPERATION = "resume"


@router.post("/resume/jobs", response_model=JobSubmitResponse, status_code=202)
async def submit_resume_job(file: UploadFile = File(...)):
    """
    Upload a PDF resume for background analysis. Returns 202 with a job ID
    immediately instead of holding the connection open for the AI call;
    poll GET /resume/jobs/{job_id} for the result.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF.")

    text = text.strip()
    digest = cache.text_digest(text)
    if cache.get(CACHE_OPERATION, DEFAULT_MODEL, digest) is not None:
        # The result already exists: skip the queue entirely and hand back a
        # pseudo-job that is finished the moment it is polled.
        return JobSubmitResponse(job_id=f"{CACHED_JOB_PREFIX}{digest}", status="finished")

    try:
        job = jobs.enqueue_resume_analysis(text)
    except RedisError:
        raise HTTPException(status_code=503, detail=QUEUE_UNAVAILABLE_DETAIL)

    metrics.observe_job_enqueued("resume")
    return JobSubmitResponse(job_id=job.id, status="queued")


@router.get(
    "/resume/jobs/{job_id}",
    response_model=JobStatusResponse,
    response_model_exclude_none=True,
)
async def get_resume_job(job_id: str):
    """
    Check a background analysis job. `status` is queued, started, finished
    (with the result) or failed.
    """
    if job_id.startswith(CACHED_JOB_PREFIX):
        cached = cache.get(CACHE_OPERATION, DEFAULT_MODEL, job_id[len(CACHED_JOB_PREFIX) :])
        if cached is not None:
            try:
                return JobStatusResponse(
                    job_id=job_id,
                    status="finished",
                    result=ResumeAnalysisResponse.model_validate(cached),
                )
            except ValidationError:
                pass  # unusable entry: fall through to 404, same as an expired one
        raise HTTPException(status_code=404, detail="No such job.")

    try:
        job = Job.fetch(job_id, connection=jobs.get_redis())
    except NoSuchJobError:
        raise HTTPException(status_code=404, detail="No such job.")
    except RedisError:
        raise HTTPException(status_code=503, detail=QUEUE_UNAVAILABLE_DETAIL)

    status = job.get_status()
    if status == JobStatus.FINISHED:
        return JobStatusResponse(
            job_id=job_id,
            status="finished",
            result=ResumeAnalysisResponse(**job.return_value()),
        )
    if status in (JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED):
        # The exception and traceback stay in the worker's log, never the response.
        return JobStatusResponse(job_id=job_id, status="failed", detail=JOB_FAILED_DETAIL)
    if status == JobStatus.STARTED:
        return JobStatusResponse(job_id=job_id, status="started")
    return JobStatusResponse(job_id=job_id, status="queued")
