import json
import logging
import re
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAIError

from resume_scanner.app.routers.ats import _run_ats_analysis
from resume_scanner.logging_config import JsonFormatter, request_id_var
from resume_scanner.main import app

client = TestClient(app)


def _access_records(caplog):
    return [r for r in caplog.records if r.name == "resume_scanner.access"]


# ---- request ID header ----


def test_request_id_echoed_back():
    response = client.get("/", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_request_id_generated_when_missing():
    response = client.get("/")
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


@pytest.mark.parametrize(
    "bad_id",
    ["has spaces", "x" * 200, 'quote"inject', "new\\nline"],
    ids=["spaces", "too_long", "quote", "escaped_newline"],
)
def test_invalid_request_id_replaced(bad_id):
    response = client.get("/", headers={"X-Request-ID": bad_id})
    assert response.headers["x-request-id"] != bad_id
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_each_request_gets_its_own_id():
    first = client.get("/").headers["x-request-id"]
    second = client.get("/").headers["x-request-id"]
    assert first != second


def test_request_id_cleared_after_request():
    client.get("/", headers={"X-Request-ID": "abc-123"})
    assert request_id_var.get() == "-"


# ---- access log ----


def test_access_log_has_request_fields(caplog):
    caplog.set_level(logging.INFO)
    client.get("/", headers={"X-Request-ID": "abc-123"})

    (record,) = _access_records(caplog)
    assert record.msg == "request completed"
    assert record.request_id == "abc-123"
    assert record.method == "GET"
    assert record.path == "/"
    assert record.status == 200
    assert record.duration_ms >= 0


def test_access_log_records_error_status(caplog):
    caplog.set_level(logging.INFO)
    client.post(
        "/ats", files={"file": ("r.txt", b"x", "text/plain")}, data={"job_description": "x"}
    )
    (record,) = _access_records(caplog)
    assert record.status == 400


def test_probes_logged_at_debug(caplog):
    caplog.set_level(logging.INFO)
    client.get("/health")
    client.get("/ready")
    assert _access_records(caplog) == []

    caplog.set_level(logging.DEBUG)
    client.get("/health")
    assert [r.levelno for r in _access_records(caplog)] == [logging.DEBUG]


def test_unhandled_exception_logged_with_request_id(caplog):
    from resume_scanner.middleware import RequestContextMiddleware

    broken = FastAPI()
    broken.add_middleware(RequestContextMiddleware)

    @broken.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    caplog.set_level(logging.INFO)
    response = TestClient(broken, raise_server_exceptions=False).get(
        "/boom", headers={"X-Request-ID": "req-1"}
    )

    assert response.status_code == 500
    errors = [r for r in _access_records(caplog) if r.msg == "unhandled exception"]
    assert len(errors) == 1
    assert errors[0].request_id == "req-1"
    assert errors[0].exc_info is not None
    (completed,) = [r for r in _access_records(caplog) if r.msg == "request completed"]
    assert completed.status == 500


# ---- JSON formatter ----


def _make_record(**extra):
    record = logging.LogRecord(
        "test.logger", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    record.request_id = "req-9"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_outputs_one_json_object():
    line = JsonFormatter().format(_make_record(status=200))
    data = json.loads(line)
    assert "\n" not in line
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"
    assert data["msg"] == "hello world"
    assert data["request_id"] == "req-9"
    assert data["status"] == 200
    assert data["ts"].endswith("+00:00")


def test_json_formatter_includes_exception():
    try:
        raise ValueError("bad")
    except ValueError:
        import sys

        record = _make_record()
        record.exc_info = sys.exc_info()
    data = json.loads(JsonFormatter().format(record))
    assert "ValueError: bad" in data["exc"]


def test_json_formatter_drops_uvicorn_color_message():
    data = json.loads(JsonFormatter().format(_make_record(color_message="\x1b[36mhi\x1b[0m")))
    assert "color_message" not in data


def test_json_formatter_handles_non_serializable_values():
    data = json.loads(JsonFormatter().format(_make_record(obj=object())))
    assert data["obj"].startswith("<object object")


# ---- OpenAI call logging ----

SECRET_RESUME = "Jane Doe, 555-0100, jane@example.com"


def _mock_completion(content):
    message = Mock(content=content)
    usage = Mock(prompt_tokens=120, completion_tokens=45)
    return Mock(choices=[Mock(message=message)], usage=usage, model="gpt-4o-mini-2024-07-18")


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_openai_call_logged_without_resume_text(mock_get_client, caplog):
    caplog.set_level(logging.INFO)
    mock_get_client.return_value.chat.completions.create.return_value = _mock_completion(
        json.dumps(
            {
                "ats_score": 80,
                "matched_keywords": [],
                "missing_keywords": [],
                "suggestions": [],
                "verdict": "ok",
            }
        )
    )

    _run_ats_analysis(SECRET_RESUME, "Python developer")

    (record,) = [r for r in caplog.records if r.name == "resume_scanner.openai"]
    assert record.msg == "openai call completed"
    assert record.operation == "ats"
    assert record.model == "gpt-4o-mini-2024-07-18"
    assert record.prompt_tokens == 120
    assert record.completion_tokens == 45
    assert record.latency_ms >= 0
    assert SECRET_RESUME not in caplog.text


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_openai_failure_logged(mock_get_client, caplog):
    caplog.set_level(logging.INFO)
    mock_get_client.return_value.chat.completions.create.side_effect = OpenAIError("down")

    with pytest.raises(Exception):
        _run_ats_analysis(SECRET_RESUME, "Python developer")

    (record,) = [r for r in caplog.records if r.name == "resume_scanner.openai"]
    assert record.levelno == logging.WARNING
    assert record.msg == "openai call failed"
    assert record.error_type == "OpenAIError"
