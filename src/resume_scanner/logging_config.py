"""Structured JSON logging.

Every log line is one JSON object on stdout, so log tools (CloudWatch, Loki,
Datadog) can filter on fields instead of grepping text. Each record carries the
current request ID, taken from a ContextVar that the request middleware sets.
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# "-" means "not inside a request" (e.g. startup logs).
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes every LogRecord has; anything else was passed via `extra=` and gets logged as a field.
# uvicorn's "color_message" is a duplicate of msg with terminal color codes, so it's dropped.
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "request_id",
    "color_message",
}

_original_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    # Stamp the request ID when the record is created, i.e. inside the request's context.
    record = _original_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    return record


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging() -> None:
    """Send all logs (app + uvicorn) to stdout as JSON. Safe to call more than once."""
    logging.setLogRecordFactory(_record_factory)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    # Route uvicorn's logs through the root JSON handler. Its access log is
    # turned off because our middleware writes a richer one with the request ID.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
