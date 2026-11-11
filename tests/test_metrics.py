import json
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openai import OpenAIError
from prometheus_client import REGISTRY

from resume_scanner import metrics
from resume_scanner.app.routers.ats import _run_ats_analysis
from resume_scanner.main import app
from resume_scanner.middleware import RequestContextMiddleware

client = TestClient(app)


def _value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _requests(method, route, status):
    return _value("http_requests_total", method=method, route=route, status=str(status))


def _duration_count(method, route):
    return _value("http_request_duration_seconds_count", method=method, route=route)


# ---- HTTP metrics ----


def test_request_counted_with_labels():
    before = _requests("GET", "/", 200)
    client.get("/")
    assert _requests("GET", "/", 200) == before + 1


def test_request_duration_observed():
    before = _duration_count("GET", "/health")
    client.get("/health")
    assert _duration_count("GET", "/health") == before + 1


def test_error_status_counted():
    before = _requests("POST", "/ats", 400)
    client.post(
        "/ats", files={"file": ("r.txt", b"x", "text/plain")}, data={"job_description": "x"}
    )
    assert _requests("POST", "/ats", 400) == before + 1


def test_route_template_used_not_raw_path():
    templated = FastAPI()
    templated.add_middleware(RequestContextMiddleware)

    @templated.get("/items/{item_id}")
    async def item(item_id: int):
        return {"id": item_id}

    c = TestClient(templated)
    before = _requests("GET", "/items/{item_id}", 200)
    c.get("/items/1")
    c.get("/items/2")
    assert _requests("GET", "/items/{item_id}", 200) == before + 2
    assert (
        REGISTRY.get_sample_value(
            "http_requests_total", {"method": "GET", "route": "/items/1", "status": "200"}
        )
        is None
    )


def test_unmatched_paths_share_one_label():
    before = _requests("GET", metrics.UNMATCHED_ROUTE, 404)
    client.get("/does-not-exist-1")
    client.get("/does-not-exist-2")
    assert _requests("GET", metrics.UNMATCHED_ROUTE, 404) == before + 2


def test_unhandled_exception_counted_as_500():
    broken = FastAPI()
    broken.add_middleware(RequestContextMiddleware)

    @broken.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    before = _requests("GET", "/boom", 500)
    TestClient(broken, raise_server_exceptions=False).get("/boom")
    assert _requests("GET", "/boom", 500) == before + 1


def test_in_progress_gauge_returns_to_baseline():
    before = _value("http_requests_in_progress")
    client.get("/")
    assert _value("http_requests_in_progress") == before


def test_in_progress_gauge_counts_active_request():
    seen = {}
    probe = FastAPI()
    probe.add_middleware(RequestContextMiddleware)

    @probe.get("/slow")
    async def slow():
        seen["during"] = _value("http_requests_in_progress")
        return {}

    before = _value("http_requests_in_progress")
    TestClient(probe).get("/slow")
    assert seen["during"] == before + 1


# ---- /metrics endpoint ----


def test_metrics_endpoint_exposes_prometheus_text():
    client.get("/")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'http_requests_total{method="GET",route="/",status="200"}' in response.text
    assert "http_request_duration_seconds_bucket" in response.text


def test_metrics_scrapes_not_counted():
    client.get("/metrics")
    client.get("/metrics")
    assert (
        REGISTRY.get_sample_value(
            "http_requests_total", {"method": "GET", "route": "/metrics", "status": "200"}
        )
        is None
    )


def test_metrics_hidden_from_openapi_docs():
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


# ---- OpenAI metrics ----


def _openai(operation, outcome):
    return _value("openai_requests_total", operation=operation, outcome=outcome)


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_openai_success_counted(mock_get_client):
    message = Mock(
        content=json.dumps(
            {
                "ats_score": 80,
                "matched_keywords": [],
                "missing_keywords": [],
                "suggestions": [],
                "verdict": "ok",
            }
        )
    )
    mock_get_client.return_value.chat.completions.create.return_value = Mock(
        choices=[Mock(message=message)], usage=None, model="gpt-4o-mini"
    )
    before = _openai("ats", "success")
    before_duration = _value("openai_request_duration_seconds_count", operation="ats")

    _run_ats_analysis("resume", "job")

    assert _openai("ats", "success") == before + 1
    assert _value("openai_request_duration_seconds_count", operation="ats") == before_duration + 1


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_openai_error_counted(mock_get_client):
    mock_get_client.return_value.chat.completions.create.side_effect = OpenAIError("down")
    before = _openai("ats", "error")

    with pytest.raises(HTTPException):
        _run_ats_analysis("resume", "job")

    assert _openai("ats", "error") == before + 1
