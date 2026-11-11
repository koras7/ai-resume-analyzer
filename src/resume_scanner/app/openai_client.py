"""Shared, lazily created OpenAI client, and the one seam all LLM calls go through.

The client is created on first use instead of at import time, so the app can
start (and report "not ready") even when OPENAI_API_KEY is missing.

`chat_completion()` owns the full call: timeout, retries, logging, metrics,
and the mapping of upstream failures to HTTP status codes — 503 when no key
is configured, 502 when OpenAI fails or returns a response that doesn't fit
the expected schema. Clients never see the raw upstream error string; it goes
to the log instead.

This module is synchronous on purpose, so the RQ worker (a plain sync
process) can call it directly. Async HTTP handlers must therefore never call
it inline — a sync call inside `async def` blocks the event loop and
serializes every in-flight request behind one upstream round trip. The
routers call their helpers via `run_in_threadpool(...)`, which keeps the
loop free; concurrency is then bounded by anyio's thread limiter (40 by
default), not by 1.
"""

import logging
import os
import time
from typing import Any, Optional, TypeVar

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from resume_scanner import cache, metrics

load_dotenv()

logger = logging.getLogger("resume_scanner.openai")

DEFAULT_MODEL = "gpt-4o-mini"

UPSTREAM_ERROR_DETAIL = "Upstream AI service error."
UPSTREAM_INVALID_RESPONSE_DETAIL = "Upstream AI service returned an invalid response."

_client: Optional[OpenAI] = None

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


def is_openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        if not is_openai_configured():
            raise HTTPException(status_code=503, detail="OpenAI API key is not configured.")
        _client = OpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            # Without a timeout, one hung upstream call holds its connection
            # and threadpool thread forever.
            timeout=httpx.Timeout(
                float(os.getenv("OPENAI_TIMEOUT_S", "30")),
                connect=float(os.getenv("OPENAI_CONNECT_TIMEOUT_S", "5")),
            ),
            # The SDK retries with exponential backoff, and only on retryable
            # failures: connection errors, 408, 409, 429 and 5xx.
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "2")),
        )
    return _client


def chat_completion(
    operation: str,
    prompt: str,
    response_model: type[ResponseModelT],
    model: str = DEFAULT_MODEL,
    cache_text: Optional[str] = None,
) -> ResponseModelT:
    """Run one JSON-mode chat completion and validate the reply into `response_model`.

    When `cache_text` is given (the raw input the prompt was built from), the
    result cache is checked first and a successful result is stored after: a
    hit skips the OpenAI call entirely, including the API-key check.

    Raises HTTPException: 503 when no API key is configured, 502 when the
    upstream call fails or its reply doesn't parse into the model.
    """
    digest = cache.text_digest(cache_text) if cache_text is not None else None
    if digest is not None:
        cached = cache.get(operation, model, digest)
        if cached is not None:
            try:
                return response_model.model_validate(cached)
            except ValidationError:
                logger.warning(
                    "cached result no longer fits the schema; ignoring",
                    extra={"operation": operation},
                )

    client = get_openai_client()
    started = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        record_openai_call(operation, started, error=e)
        raise HTTPException(status_code=502, detail=UPSTREAM_ERROR_DETAIL)
    record_openai_call(operation, started, response=response)

    raw = response.choices[0].message.content
    try:
        result = response_model.model_validate_json(raw)
    except (ValidationError, TypeError) as e:
        logger.warning(
            "openai response failed validation",
            extra={"operation": operation, "error_type": type(e).__name__},
        )
        raise HTTPException(status_code=502, detail=UPSTREAM_INVALID_RESPONSE_DETAIL)

    # Only validated successes are cached; 502s and garbage never are.
    if digest is not None:
        cache.put(operation, model, digest, result.model_dump())
    return result


def record_openai_call(
    operation: str,
    started: float,
    response: Any = None,
    error: Optional[Exception] = None,
) -> None:
    """Record one OpenAI call: a log line (latency, model, token usage) and metrics.

    Never logs the prompt or the completion: they contain resume text (PII).
    """
    duration_s = time.perf_counter() - started
    metrics.observe_openai_call(operation, duration_s, success=error is None)
    fields: dict[str, Any] = {
        "operation": operation,
        "latency_ms": round(duration_s * 1000, 2),
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
