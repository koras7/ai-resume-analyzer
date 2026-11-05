"""Shared, lazily created OpenAI client.

The client is created on first use instead of at import time, so the app can
start (and report "not ready") even when OPENAI_API_KEY is missing.
"""

import logging
import os
import time
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI

load_dotenv()

logger = logging.getLogger("resume_scanner.openai")

_client: Optional[OpenAI] = None


def is_openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        if not is_openai_configured():
            raise HTTPException(status_code=503, detail="OpenAI API key is not configured.")
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def record_openai_call(
    operation: str,
    started: float,
    response: Any = None,
    error: Optional[Exception] = None,
) -> None:
    """Log one line per OpenAI call: latency, model and token usage.

    Never logs the prompt or the completion: they contain resume text (PII).
    """
    fields: dict[str, Any] = {
        "operation": operation,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if error is not None:
        fields["error_type"] = type(error).__name__
        logger.warning("openai call failed", extra=fields)
        return

    usage = getattr(response, "usage", None)
    fields["model"] = getattr(response, "model", None)
    fields["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
    fields["completion_tokens"] = getattr(usage, "completion_tokens", None)
    logger.info("openai call completed", extra=fields)
