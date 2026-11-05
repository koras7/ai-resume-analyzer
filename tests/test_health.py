import pytest
from fastapi.testclient import TestClient

from resume_scanner.main import app

client = TestClient(app)


def test_health_status():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.0.0"}


def test_health_response_structure():
    response = client.get("/health")
    data = response.json()
    assert "status" in data
    assert "version" in data


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Resume Analyzer API is running!"}


def test_health_version_matches_package():
    from importlib.metadata import version

    assert client.get("/health").json()["version"] == version("resume-scanner")


def test_health_ok_without_openai_key(monkeypatch):
    # Liveness must not depend on config or external services.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert client.get("/health").status_code == 200


def test_ready_when_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"openai_api_key": "ok"}}


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "whitespace"])
def test_not_ready_without_openai_key(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", value)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"openai_api_key": "missing"}}
