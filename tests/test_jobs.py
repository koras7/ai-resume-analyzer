from unittest.mock import Mock, patch

import pytest
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue

from resume_scanner import jobs
from resume_scanner.app.models.schemas import ResumeAnalysisResponse
from resume_scanner.app.routers.jobs import JOB_FAILED_DETAIL, QUEUE_UNAVAILABLE_DETAIL
from resume_scanner.main import app

client = TestClient(app)

RESULT = ResumeAnalysisResponse(
    score=85,
    strengths=["Strong Python skills"],
    weaknesses=["Lacks certifications"],
    skills=["Python", "FastAPI"],
    improved_summary="Results-driven developer",
    recommendation="Apply for internships",
    matched_roles=[],
)


@pytest.fixture
def fake_queue(monkeypatch):
    """Point the jobs module at an in-memory Redis whose queue runs jobs inline."""
    connection = FakeStrictRedis()
    monkeypatch.setattr(jobs, "_redis", connection)
    queue = Queue(jobs.QUEUE_NAME, connection=connection, is_async=False)
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)
    return queue


def _submit_pdf():
    return client.post(
        "/resume/jobs",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
    )


@patch("resume_scanner.app.routers.resume._analyze_resume")
@patch("resume_scanner.app.routers.jobs.fitz.open")
def test_submit_then_fetch_finished(mock_fitz_open, mock_analyze, fake_queue):
    page = Mock()
    page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [page]
    mock_analyze.return_value = RESULT

    submitted = _submit_pdf()
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    assert submitted.json()["status"] == "queued"

    polled = client.get(f"/resume/jobs/{job_id}")
    assert polled.status_code == 200
    body = polled.json()
    assert body["status"] == "finished"
    assert body["result"] == RESULT.model_dump()
    mock_analyze.assert_called_once_with("Valid resume content")


@patch("resume_scanner.app.routers.resume._analyze_resume")
@patch("resume_scanner.app.routers.jobs.fitz.open")
def test_failed_job_returns_generic_detail(mock_fitz_open, mock_analyze, fake_queue):
    page = Mock()
    page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [page]
    mock_analyze.side_effect = RuntimeError("secret internals")

    job_id = _submit_pdf().json()["job_id"]
    polled = client.get(f"/resume/jobs/{job_id}")

    assert polled.status_code == 200
    assert polled.json()["status"] == "failed"
    assert polled.json()["detail"] == JOB_FAILED_DETAIL
    assert "secret internals" not in polled.text
    assert "result" not in polled.json()


def test_unknown_job_is_404(fake_queue):
    response = client.get("/resume/jobs/no-such-job")
    assert response.status_code == 404
    assert response.json()["detail"] == "No such job."


def test_submit_rejects_non_pdf(fake_queue):
    response = client.post(
        "/resume/jobs",
        files={"file": ("resume.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Only PDF files are allowed."


def test_submit_rejects_empty_file(fake_queue):
    response = client.post(
        "/resume/jobs",
        files={"file": ("resume.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."


@patch("resume_scanner.app.routers.jobs.fitz.open")
def test_submit_rejects_pdf_without_text(mock_fitz_open, fake_queue):
    page = Mock()
    page.get_text.return_value = "   "
    mock_fitz_open.return_value = [page]

    response = _submit_pdf()
    assert response.status_code == 400
    assert response.json()["detail"] == "Could not extract text from PDF."


@patch("resume_scanner.app.routers.jobs.fitz.open")
def test_submit_with_redis_down_is_503(mock_fitz_open, monkeypatch):
    page = Mock()
    page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [page]
    monkeypatch.setattr(jobs, "get_queue", Mock(side_effect=RedisConnectionError("down")))

    response = _submit_pdf()
    assert response.status_code == 503
    assert response.json()["detail"] == QUEUE_UNAVAILABLE_DETAIL


def test_fetch_with_redis_down_is_503(monkeypatch):
    monkeypatch.setattr(jobs, "get_redis", Mock(side_effect=RedisConnectionError("down")))
    response = client.get("/resume/jobs/some-id")
    assert response.status_code == 503
    assert response.json()["detail"] == QUEUE_UNAVAILABLE_DETAIL


# ---- metrics ----


@patch("resume_scanner.app.routers.resume._analyze_resume")
@patch("resume_scanner.app.routers.jobs.fitz.open")
def test_enqueue_counted(mock_fitz_open, mock_analyze, fake_queue):
    page = Mock()
    page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [page]
    mock_analyze.return_value = RESULT
    before = REGISTRY.get_sample_value("jobs_enqueued_total", {"operation": "resume"}) or 0

    _submit_pdf()

    after = REGISTRY.get_sample_value("jobs_enqueued_total", {"operation": "resume"})
    assert after == before + 1


def test_queue_depth_in_metrics(fake_queue):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert 'job_queue_depth{queue="resume"} 0.0' in response.text
    assert 'job_queue_registry_jobs{queue="resume",state="failed"}' in response.text


def test_metrics_render_without_redis(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", Mock(side_effect=RedisConnectionError("down")))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "job_queue_depth" not in response.text
    assert "http_requests_total" in response.text
