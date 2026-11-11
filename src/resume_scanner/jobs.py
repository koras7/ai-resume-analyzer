"""Background job queue (Redis + RQ) for PDF analysis.

The API enqueues extracted resume text (never the PDF bytes: binary blobs
don't belong in Redis) and returns a job ID. A separate `rq worker resume`
process runs the same analysis helper the sync endpoint uses and stores the
result in Redis, where the status endpoint reads it back.
"""

import os
from typing import Optional

from redis import Redis
from rq import Queue
from rq.job import Job

QUEUE_NAME = "resume"

# How long a finished or failed job (and its result) stays readable.
RESULT_TTL_S = 3600

# Must exceed the worst-case OpenAI call: 30s timeout x 3 attempts plus backoff.
JOB_TIMEOUT_S = 120

_redis: Optional[Redis] = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    return _redis


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis())


def enqueue_resume_analysis(text: str) -> Job:
    return get_queue().enqueue(
        run_resume_analysis,
        text,
        result_ttl=RESULT_TTL_S,
        failure_ttl=RESULT_TTL_S,
        job_timeout=JOB_TIMEOUT_S,
    )


def run_resume_analysis(text: str) -> dict:
    """The job the RQ worker executes.

    Imported lazily so the worker resolves the analysis helper at run time
    (tests patch it) and importing this module never pulls in the web app.
    """
    from resume_scanner.app.routers.resume import _analyze_resume

    return _analyze_resume(text).model_dump()
