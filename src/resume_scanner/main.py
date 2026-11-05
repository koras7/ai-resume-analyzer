import os

import uvicorn
from fastapi import FastAPI, Response

from resume_scanner import metrics
from resume_scanner.app.routers import analyze, ats, health, resume, roles
from resume_scanner.app.routers.health import APP_VERSION
from resume_scanner.logging_config import configure_logging
from resume_scanner.middleware import RequestContextMiddleware

configure_logging()

app = FastAPI(
    title="Resume Analyzer API",
    description="AI-powered resume analysis and job role matching",
    version=APP_VERSION,
)

app.add_middleware(RequestContextMiddleware)

app.include_router(health.router)
app.include_router(roles.router)
app.include_router(analyze.router)
app.include_router(resume.router)
app.include_router(ats.router)


@app.get("/")
async def root():
    return {"message": "Resume Analyzer API is running!"}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus scrape endpoint (text exposition format)."""
    body, content_type = metrics.render_latest()
    return Response(content=body, media_type=content_type)


def run() -> None:
    uvicorn.run(
        "resume_scanner.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        # Auto-reload is for local development only; opt in with RELOAD=true.
        reload=os.getenv("RELOAD", "false").lower() in ("1", "true", "yes"),
        # Keep uvicorn from installing its own text log handlers; ours write JSON.
        log_config=None,
        # Our middleware writes the access log (with request ID and duration).
        access_log=False,
    )
