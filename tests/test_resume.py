import json
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openai import OpenAIError

from resume_scanner.app.models.schemas import ResumeAnalysisResponse
from resume_scanner.app.routers.resume import _analyze_resume
from resume_scanner.main import app

client = TestClient(app)

VALID_RESUME_RESULT = ResumeAnalysisResponse(
    score=85,
    strengths=["Strong Python skills"],
    weaknesses=["Lacks certifications"],
    skills=["Python", "FastAPI", "Docker"],
    improved_summary="Results-driven developer...",
    recommendation="Apply for software engineering internships",
    matched_roles=[],
)


# content type tests
@pytest.mark.parametrize(
    "filename, content_type, expected_status_code",
    [
        pytest.param("resume.docx", "application/vnd.openxmlformats", 400, id="docx_not_allowed"),
        pytest.param("resume.txt", "text/plain", 400, id="txt_not_allowed"),
        pytest.param("resume.png", "image/png", 400, id="png_not_allowed"),
        pytest.param("resume.csv", "text/csv", 400, id="csv_not_allowed"),
        pytest.param("resume.pdf", "application/pdf", 200, id="pdf_allowed"),
    ],
)
def test_content_type(filename, content_type, expected_status_code):
    # Only mock for valid PDF case
    if content_type == "application/pdf":
        with (
            patch("resume_scanner.app.routers.resume.fitz.open") as mock_fitz_open,
            patch("resume_scanner.app.routers.resume._analyze_resume") as mock_analyze,
        ):
            mock_page = Mock()
            mock_page.get_text.return_value = "Valid resume text"
            mock_fitz_open.return_value = [mock_page]
            mock_analyze.return_value = VALID_RESUME_RESULT

            response = client.post(
                "/resume",
                files={"file": (filename, b"some bytes", content_type)},
            )
    else:
        response = client.post(
            "/resume",
            files={"file": (filename, b"some bytes", content_type)},
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
@patch("resume_scanner.app.routers.resume.fitz.open")
def test_pdf_content(mock_fitz_open, pages_text, expected_status_code):
    # create mock pages
    mock_pages = []
    for text in pages_text:
        page = Mock()
        page.get_text.return_value = text
        mock_pages.append(page)
    mock_fitz_open.return_value = mock_pages

    if expected_status_code == 200:
        with patch("resume_scanner.app.routers.resume._analyze_resume") as mock_analyze:
            mock_analyze.return_value = VALID_RESUME_RESULT
            response = client.post(
                "/resume",
                files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
            )
    else:
        response = client.post(
            "/resume",
            files={"file": ("resume.pdf", b"%PDF-blank", "application/pdf")},
        )

    assert response.status_code == expected_status_code


MOCK_ANALYZE_CONTENT = json.dumps(
    {
        "score": 85,
        "strengths": ["Strong Python skills"],
        "weaknesses": ["Lacks certifications"],
        "skills": ["Python", "FastAPI"],
        "improved_summary": "Results-driven developer",
        "recommendation": "Apply for internships",
        "matched_roles": [
            {
                "title": "Backend Developer",
                "reason": "Strong Python experience",
                "match_score": 90,
                "key_skills": ["Python", "FastAPI"],
            }
        ],
    }
)


@patch("resume_scanner.app.routers.resume.get_openai_client")
def test_resume_analyze_function(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_message = Mock()
    mock_message.content = MOCK_ANALYZE_CONTENT
    mock_choice = Mock()
    mock_choice.message = mock_message
    mock_create.return_value = Mock(choices=[mock_choice])

    result = _analyze_resume("Sample resume text")

    assert result.score == 85
    assert result.strengths == ["Strong Python skills"]
    assert result.weaknesses == ["Lacks certifications"]
    assert result.skills == ["Python", "FastAPI"]
    assert result.improved_summary == "Results-driven developer"
    assert result.recommendation == "Apply for internships"
    assert len(result.matched_roles) == 1
    assert result.matched_roles[0].title == "Backend Developer"
    assert result.matched_roles[0].match_score == 90


@patch("resume_scanner.app.routers.resume.get_openai_client")
def test_resume_openai_error(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_create.side_effect = OpenAIError("test error")

    with pytest.raises(HTTPException) as exc_info:
        _analyze_resume("Sample resume text")

    assert exc_info.value.status_code == 500


@patch("resume_scanner.app.routers.resume.get_openai_client")
def test_resume_json_parse_error(mock_get_client):
    mock_create = mock_get_client.return_value.chat.completions.create
    mock_message = Mock()
    mock_message.content = "invalid json {{{{"
    mock_choice = Mock()
    mock_choice.message = mock_message
    mock_create.return_value = Mock(choices=[mock_choice])

    with pytest.raises(HTTPException) as exc_info:
        _analyze_resume("Sample resume text")

    assert exc_info.value.status_code == 500
    assert "Failed to parse OpenAI response" in exc_info.value.detail


def test_resume_empty_file_bytes():
    response = client.post(
        "/resume",
        files={"file": ("resume.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded file is empty."
