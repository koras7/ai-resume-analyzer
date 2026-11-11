import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

from resume_scanner import jobs, metrics
from resume_scanner.app.models.schemas import (
    JobStatusResponse,
    JobSubmitResponse,
    ResumeAnalysisResponse,
)

router = APIRouter()

QUEUE_UNAVAILABLE_DETAIL = "Job queue is unavailable."
JOB_FAILED_DETAIL = "Analysis failed. Submit the resume again."


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

    try:
        job = jobs.enqueue_resume_analysis(text.strip())
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
