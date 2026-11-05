from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from resume_scanner.app import openai_client
from resume_scanner.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_cached_client(monkeypatch):
    monkeypatch.setattr(openai_client, "_client", None)


def test_client_created_lazily_and_cached(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    first = openai_client.get_openai_client()
    assert openai_client.get_openai_client() is first


def test_missing_key_raises_503(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc_info:
        openai_client.get_openai_client()
    assert exc_info.value.status_code == 503


@patch("resume_scanner.app.routers.ats.fitz.open")
def test_endpoint_returns_503_without_key(mock_fitz_open, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    page = Mock()
    page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [page]

    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
        data={"job_description": "Python developer"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenAI API key is not configured."
