import os

import uvicorn
from fastapi import FastAPI

from resume_scanner.app.routers import analyze, ats, health, resume, roles
from resume_scanner.app.routers.health import APP_VERSION

app = FastAPI(
    title="Resume Analyzer API",
    description="AI-powered resume analysis and job role matching",
    version=APP_VERSION,
)

app.include_router(health.router)
app.include_router(roles.router)
app.include_router(analyze.router)
app.include_router(resume.router)
app.include_router(ats.router)


@app.get("/")
async def root():
    return {"message": "Resume Analyzer API is running!"}


def run() -> None:
    uvicorn.run(
        "resume_scanner.main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        # Auto-reload is for local development only; opt in with RELOAD=true.
        reload=os.getenv("RELOAD", "false").lower() in ("1", "true", "yes"),
    )
