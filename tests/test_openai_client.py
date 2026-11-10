import json
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import APITimeoutError, OpenAIError
from pydantic import BaseModel

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


# ---- timeout and retry configuration ----


def test_client_default_timeout_and_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for var in ("OPENAI_TIMEOUT_S", "OPENAI_CONNECT_TIMEOUT_S", "OPENAI_MAX_RETRIES"):
        monkeypatch.delenv(var, raising=False)

    built = openai_client.get_openai_client()

    assert built.max_retries == 2
    assert built.timeout.read == 30.0
    assert built.timeout.connect == 5.0


def test_client_timeout_and_retries_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_TIMEOUT_S", "7")
    monkeypatch.setenv("OPENAI_CONNECT_TIMEOUT_S", "2")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "5")

    built = openai_client.get_openai_client()

    assert built.max_retries == 5
    assert built.timeout.read == 7.0
    assert built.timeout.connect == 2.0


# ---- chat_completion error mapping ----


class _Echo(BaseModel):
    answer: str


def _mock_completion(content):
    return Mock(choices=[Mock(message=Mock(content=content))], usage=None, model="gpt-4o-mini")


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_chat_completion_success(mock_get_client):
    mock_get_client.return_value.chat.completions.create.return_value = _mock_completion(
        json.dumps({"answer": "hi"})
    )

    result = openai_client.chat_completion("test", "prompt", _Echo)

    assert result == _Echo(answer="hi")


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_chat_completion_upstream_error_is_502_and_generic(mock_get_client):
    mock_get_client.return_value.chat.completions.create.side_effect = OpenAIError(
        "secret internals"
    )

    with pytest.raises(HTTPException) as exc_info:
        openai_client.chat_completion("test", "prompt", _Echo)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == openai_client.UPSTREAM_ERROR_DETAIL
    assert "secret internals" not in exc_info.value.detail


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_chat_completion_timeout_is_502(mock_get_client):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_get_client.return_value.chat.completions.create.side_effect = APITimeoutError(request)

    with pytest.raises(HTTPException) as exc_info:
        openai_client.chat_completion("test", "prompt", _Echo)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == openai_client.UPSTREAM_ERROR_DETAIL


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_chat_completion_invalid_json_is_502(mock_get_client):
    mock_get_client.return_value.chat.completions.create.return_value = _mock_completion(
        "not json {{"
    )

    with pytest.raises(HTTPException) as exc_info:
        openai_client.chat_completion("test", "prompt", _Echo)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == openai_client.UPSTREAM_INVALID_RESPONSE_DETAIL


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_chat_completion_wrong_shape_is_502(mock_get_client):
    mock_get_client.return_value.chat.completions.create.return_value = _mock_completion(
        json.dumps({"unexpected": "shape"})
    )

    with pytest.raises(HTTPException) as exc_info:
        openai_client.chat_completion("test", "prompt", _Echo)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == openai_client.UPSTREAM_INVALID_RESPONSE_DETAIL
