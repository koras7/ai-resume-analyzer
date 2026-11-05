"""Liveness and readiness probes.

- /health (liveness): is the process up and able to answer? No dependency
  checks, so a slow or down dependency never gets a healthy process restarted.
- /ready (readiness): can this instance serve real traffic right now? Returns
  503 when required config is missing, so load balancers route traffic elsewhere.

Neither probe calls OpenAI: probes run every few seconds, and calling a paid,
rate-limited external API that often would cost money and mark every instance
down during an OpenAI outage.
"""

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from resume_scanner.app.openai_client import is_openai_configured

try:
    APP_VERSION = version("resume-scanner")
except PackageNotFoundError:  # running from a source tree without the package installed
    APP_VERSION = "unknown"

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health():
    """Liveness probe: returns 200 whenever the process is running."""
    return HealthResponse(status="ok", version=APP_VERSION)


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Not ready"}},
)
async def ready(response: Response):
    """Readiness probe: returns 503 if required configuration is missing."""
    checks = {
        "openai_api_key": "ok" if is_openai_configured() else "missing",
    }
    is_ready = all(result == "ok" for result in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
