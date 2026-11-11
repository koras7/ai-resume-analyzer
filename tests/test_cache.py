import json
from unittest.mock import Mock, patch

import pytest
from fakeredis import FakeStrictRedis
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue

from resume_scanner import cache, jobs
from resume_scanner.app.routers.jobs import CACHED_JOB_PREFIX
from resume_scanner.main import app

client = TestClient(app)

ANALYSIS_JSON = json.dumps(
    {
        "score": 85,
        "strengths": ["Strong Python skills"],
        "weaknesses": ["Lacks certifications"],
        "skills": ["Python", "FastAPI"],
        "improved_summary": "Results-driven developer",
        "recommendation": "Apply for internships",
    }
)

RESUME_JSON = json.dumps(
    {
        "score": 85,
        "strengths": ["Strong Python skills"],
        "weaknesses": ["Lacks certifications"],
        "skills": ["Python", "FastAPI"],
        "improved_summary": "Results-driven developer",
        "recommendation": "Apply for internships",
        "matched_roles": [],
    }
)


@pytest.fixture
def fake_redis(monkeypatch):
    """In-memory Redis shared by the cache and the job queue."""
    connection = FakeStrictRedis()
    monkeypatch.setattr(jobs, "_redis", connection)
    return connection


@pytest.fixture
def fake_queue(fake_redis, monkeypatch):
    """Queue on the same fake Redis, running jobs inline in-process."""
    queue = Queue(jobs.QUEUE_NAME, connection=fake_redis, is_async=False)
    monkeypatch.setattr(jobs, "get_queue", lambda: queue)
    return queue


def _mock_completion(content):
    return Mock(choices=[Mock(message=Mock(content=content))], usage=None, model="gpt-4o-mini")


def _cache_samples(operation):
    hits = REGISTRY.get_sample_value(
        "cache_operations_total", {"operation": operation, "result": "hit"}
    )
    misses = REGISTRY.get_sample_value(
        "cache_operations_total", {"operation": operation, "result": "miss"}
    )
    return hits or 0, misses or 0


# ---- sync endpoints ----


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_second_identical_request_is_served_from_cache(mock_get_client, fake_redis):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(ANALYSIS_JSON)
    hits_before, misses_before = _cache_samples("analyze")

    first = client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})
    second = client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    mock_create.assert_called_once()
    hits_after, misses_after = _cache_samples("analyze")
    assert hits_after == hits_before + 1
    assert misses_after == misses_before + 1


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_distinct_text_misses(mock_get_client, fake_redis):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(ANALYSIS_JSON)

    client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})
    client.post("/analyze/text", json={"resume_text": "John Roe, Java developer"})

    assert mock_create.call_count == 2


@patch("resume_scanner.app.routers.resume.fitz.open")
@patch("resume_scanner.app.openai_client.get_openai_client")
def test_resume_pdf_reupload_hits_cache(mock_get_client, mock_fitz_open, fake_redis):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(RESUME_JSON)
    page = Mock()
    page.get_text.return_value = "Jane Doe resume content"
    mock_fitz_open.return_value = [page]

    for _ in range(2):
        response = client.post(
            "/resume", files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")}
        )
        assert response.status_code == 200

    mock_create.assert_called_once()


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_redis_down_degrades_to_miss_not_500(mock_get_client, monkeypatch):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(ANALYSIS_JSON)
    monkeypatch.setattr(cache, "get_redis", Mock(side_effect=RedisConnectionError("down")))

    first = client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})
    second = client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_create.call_count == 2  # every lookup was a miss


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_cache_disabled_via_env(mock_get_client, fake_redis, monkeypatch):
    monkeypatch.setenv("CACHE_ENABLED", "false")
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(ANALYSIS_JSON)
    hits_before, misses_before = _cache_samples("analyze")

    client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})
    client.post("/analyze/text", json={"resume_text": "Jane Doe, Python developer"})

    assert mock_create.call_count == 2
    assert _cache_samples("analyze") == (hits_before, misses_before)


def test_key_separates_operation_model_and_text():
    digest = cache.text_digest("same text")
    keys = {
        cache._key("resume", "gpt-4o-mini", digest),
        cache._key("analyze", "gpt-4o-mini", digest),
        cache._key("resume", "gpt-4o", digest),
        cache._key("resume", "gpt-4o-mini", cache.text_digest("other text")),
    }
    assert len(keys) == 4


# ---- job endpoints ----


def _submit_pdf():
    return client.post(
        "/resume/jobs",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
    )


@patch("resume_scanner.app.routers.jobs.fitz.open")
@patch("resume_scanner.app.openai_client.get_openai_client")
def test_job_cache_hit_skips_queue(mock_get_client, mock_fitz_open, fake_queue):
    """First submit runs the job (worker stores the result); second one never queues."""
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.return_value = _mock_completion(RESUME_JSON)
    page = Mock()
    page.get_text.return_value = "Jane Doe resume content"
    mock_fitz_open.return_value = [page]
    enqueued_before = REGISTRY.get_sample_value("jobs_enqueued_total", {"operation": "resume"}) or 0

    first = _submit_pdf()
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    polled = client.get(f"/resume/jobs/{first.json()['job_id']}")
    assert polled.json()["status"] == "finished"

    second = _submit_pdf()
    assert second.status_code == 202
    assert second.json()["status"] == "finished"
    assert second.json()["job_id"].startswith(CACHED_JOB_PREFIX)

    polled = client.get(f"/resume/jobs/{second.json()['job_id']}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "finished"
    assert polled.json()["result"] == json.loads(RESUME_JSON)

    mock_create.assert_called_once()
    enqueued_after = REGISTRY.get_sample_value("jobs_enqueued_total", {"operation": "resume"})
    assert enqueued_after == enqueued_before + 1  # only the first submit was queued


def test_unknown_cached_pseudo_job_is_404(fake_redis):
    response = client.get(f"/resume/jobs/{CACHED_JOB_PREFIX}{'0' * 64}")
    assert response.status_code == 404
    assert response.json()["detail"] == "No such job."
