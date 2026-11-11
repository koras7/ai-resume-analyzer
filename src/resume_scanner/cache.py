"""Result cache: Redis entries keyed by a content hash of the analyzed text.

Key design: ``cache:{operation}:{model}:{PROMPT_VERSION}:{sha256(text)}``.
Hashing the *extracted text* rather than the PDF bytes means a re-saved or
re-exported PDF of the same resume still hits. Operation, model and prompt
version in the key make invalidation a non-event: change the model or bump
PROMPT_VERSION and old entries simply stop being read, then expire via TTL —
no purge logic.

The cache is an optimization, never a dependency: every Redis failure is
swallowed and treated as a miss, so requests still succeed with Redis down.
"""

import hashlib
import json
import logging
import os
from typing import Any, Optional

from resume_scanner import metrics
from resume_scanner.jobs import get_redis

logger = logging.getLogger("resume_scanner.cache")

# Bump when a prompt or a response schema changes: entries written under the
# old prompt stop matching and quietly expire instead of being served.
PROMPT_VERSION = "v1"

DEFAULT_TTL_S = 24 * 60 * 60


def is_enabled() -> bool:
    """Read CACHE_ENABLED (default on) at call time, so it can be toggled per run."""
    return os.getenv("CACHE_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key(operation: str, model: str, digest: str) -> str:
    return f"cache:{operation}:{model}:{PROMPT_VERSION}:{digest}"


def get(operation: str, model: str, digest: str) -> Optional[Any]:
    """Return the cached result (a parsed JSON value), or None on a miss.

    A disabled cache does no lookup and records no metric; a Redis error or
    an unreadable entry counts as a miss.
    """
    if not is_enabled():
        return None
    raw = None
    try:
        raw = get_redis().get(_key(operation, model, digest))
    except Exception:
        logger.warning("cache read failed; treating as miss", extra={"operation": operation})
    value = None
    if raw is not None:
        try:
            value = json.loads(raw)
        except ValueError:
            logger.warning(
                "cache entry is not JSON; treating as miss", extra={"operation": operation}
            )
    metrics.observe_cache_lookup(operation, hit=value is not None)
    return value


def put(operation: str, model: str, digest: str, value: dict, ttl_s: int = DEFAULT_TTL_S) -> None:
    """Store one successful result. Failures are logged and otherwise ignored."""
    if not is_enabled():
        return
    try:
        get_redis().set(_key(operation, model, digest), json.dumps(value), ex=ttl_s)
    except Exception:
        logger.warning("cache write failed; result not stored", extra={"operation": operation})
