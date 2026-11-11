import json
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import OpenAIError

from resume_scanner.app.models.schemas import ATSResult
from resume_scanner.app.openai_client import (
    UPSTREAM_ERROR_DETAIL,
    UPSTREAM_INVALID_RESPONSE_DETAIL,
)
from resume_scanner.app.routers.ats import _run_ats_analysis
from resume_scanner.main import app

client = TestClient(app)

VALID_ATS_RESULT = ATSResult(
    ats_score=85,
    matched_keywords=["Python", "FastAPI"],
    missing_keywords=["Docker"],
    suggestions=["Add Docker experience"],
    verdict="Likely to pass ATS screening",
)


# content type tests
@pytest.mark.parametrize(
    "filename, content_type, expected_status_code",
    [
        pytest.param("resume.docx", "application/vnd.openxmlformats", 400, id="docx_not_allowed"),
        pytest.param("resume.txt", "text/plain", 400, id="txt_not_allowed"),
        pytest.param("resume.png", "image/png", 400, id="png_not_allowed"),
        pytest.param("resume.pdf", "application/pdf", 200, id="pdf_allowed"),
    ],
)
def test_ats_content_type(filename, content_type, expected_status_code):
    if content_type == "application/pdf":
        with (
            patch("resume_scanner.app.routers.ats.fitz.open") as mock_fitz_open,
            patch("resume_scanner.app.routers.ats._run_ats_analysis") as mock_run_ats,
        ):
            mock_page = Mock()
            mock_page.get_text.return_value = "Valid resume content"
            mock_fitz_open.return_value = [mock_page]
            mock_run_ats.return_value = VALID_ATS_RESULT

            response = client.post(
                "/ats",
                files={"file": (filename, b"some bytes", content_type)},
                data={"job_description": "We need a Python developer with FastAPI experience."},
            )
    else:
        response = client.post(
            "/ats",
            files={"file": (filename, b"some bytes", content_type)},
            data={"job_description": "We need a Python developer with FastAPI experience."},
        )

    assert response.status_code == expected_status_code


# pdf content tests
@pytest.mark.parametrize(
    "pages_text, expected_status_code",
    [
        pytest.param(["   "], 400, id="spaces_only"),
        pytest.param(["\n", "\t"], 400, id="whitespace_chars"),
        pytest.param(["", "", ""], 400, id="all_empty_pages"),
        pytest.param(["Valid resume content"], 200, id="valid_content_success"),
    ],
)
@patch("resume_scanner.app.routers.ats.fitz.open")
def test_ats_pdf_content(mock_fitz_open, pages_text, expected_status_code):
    mock_pages = []
    for text in pages_text:
        page = Mock()
        page.get_text.return_value = text
        mock_pages.append(page)
    mock_fitz_open.return_value = mock_pages

    if expected_status_code == 200:
        with patch("resume_scanner.app.routers.ats._run_ats_analysis") as mock_run_ats:
            mock_run_ats.return_value = VALID_ATS_RESULT
            response = client.post(
                "/ats",
                files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
                data={"job_description": "We need a Python developer with FastAPI experience."},
            )
    else:
        response = client.post(
            "/ats",
            files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
            data={"job_description": "We need a Python developer with FastAPI experience."},
        )

    assert response.status_code == expected_status_code


@patch("resume_scanner.app.routers.ats.fitz.open")
def test_ats_empty_job_description(mock_fitz_open):
    mock_page = Mock()
    mock_page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [mock_page]

    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
        data={"job_description": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Job description is empty or missing."


@patch("resume_scanner.app.routers.ats.fitz.open")
@patch("resume_scanner.app.routers.ats._run_ats_analysis")
def test_ats_openai_error(mock_run_ats, mock_fitz_open):
    mock_page = Mock()
    mock_page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [mock_page]
    mock_run_ats.side_effect = HTTPException(status_code=502, detail=UPSTREAM_ERROR_DETAIL)

    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
        data={"job_description": "We need a Python developer with FastAPI experience."},
    )

    assert response.status_code == 502


@patch("resume_scanner.app.routers.ats.fitz.open")
def test_ats_empty_pdf_text(mock_fitz_open):
    mock_page = Mock()
    mock_page.get_text.return_value = ""
    mock_fitz_open.return_value = [mock_page]

    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"%PDF-content", "application/pdf")},
        data={"job_description": "We need a Python developer with FastAPI experience."},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Could not extract text from PDF."


@patch("resume_scanner.app.routers.ats.fitz.open")
@patch("resume_scanner.app.routers.ats._run_ats_analysis")
def test_ats_parse_error(mock_run_ats, mock_fitz_open):
    mock_page = Mock()
    mock_page.get_text.return_value = "Valid resume content"
    mock_fitz_open.return_value = [mock_page]
    mock_run_ats.side_effect = HTTPException(
        status_code=502,
        detail=UPSTREAM_INVALID_RESPONSE_DETAIL,
    )

    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
        data={"job_description": "We need a Python developer with FastAPI experience."},
    )

    assert response.status_code == 502


MOCK_ATS_CONTENT = json.dumps(
    {
        "ats_score": 85,
        "matched_keywords": ["Python", "FastAPI"],
        "missing_keywords": ["Docker"],
        "suggestions": ["Add Docker experience"],
        "verdict": "Likely to pass ATS screening",
    }
)


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_ats_openai_error_in_function(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.side_effect = OpenAIError("test error")

    job_desc = "We need a Python developer with FastAPI experience."
    with pytest.raises(HTTPException) as exc_info:
        _run_ats_analysis("Sample resume text", job_desc)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == UPSTREAM_ERROR_DETAIL
    # The raw SDK error must never reach the client.
    assert "test error" not in exc_info.value.detail


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_ats_json_parse_error_in_function(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_message = Mock()
    mock_message.content = "invalid json {{{{"
    mock_choice = Mock()
    mock_choice.message = mock_message
    mock_create.return_value = Mock(choices=[mock_choice])

    job_desc = "We need a Python developer with FastAPI experience."
    with pytest.raises(HTTPException) as exc_info:
        _run_ats_analysis("Sample resume text", job_desc)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == UPSTREAM_INVALID_RESPONSE_DETAIL


def test_ats_empty_file_bytes():
    response = client.post(
        "/ats",
        files={"file": ("resume.pdf", b"", "application/pdf")},
        data={"job_description": "We need a Python developer with FastAPI experience."},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."


@patch("resume_scanner.app.openai_client.get_openai_client")
def test_ats_analysis_function(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_message = Mock()
    mock_message.content = MOCK_ATS_CONTENT
    mock_choice = Mock()
    mock_choice.message = mock_message
    mock_create.return_value = Mock(choices=[mock_choice])

    job_desc = "We need a Python developer with FastAPI experience."
    result = _run_ats_analysis("Sample resume text", job_desc)

    assert result.ats_score == 85
    assert result.matched_keywords == ["Python", "FastAPI"]
    assert result.missing_keywords == ["Docker"]
    assert result.suggestions == ["Add Docker experience"]
    assert result.verdict == "Likely to pass ATS screening"
