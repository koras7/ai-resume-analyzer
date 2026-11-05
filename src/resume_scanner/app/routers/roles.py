import json

import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile
from openai import OpenAIError

from resume_scanner.app.models.schemas import ResumeTextRequest, RoleMatch, RolesResponse
from resume_scanner.app.openai_client import get_openai_client

router = APIRouter()

ROLES_PROMPT = """\
You are a career advisor. Given the resume text below, identify the 3 job roles \
that best match the candidate's skills and experience.

Respond with valid JSON only — no markdown, no extra text — in this exact format:
{{
  "roles": [
    {{
      "title": "<role title>",
      "reason": "<why this role fits>",
      "match_score": <integer 0-100>,
      "key_skills": ["<skill1>", "<skill2>", "<skill3>"]
    }}
  ]
}}

Resume:
{resume_text}
"""


def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text() for page in doc)


def _get_roles_from_openai(resume_text: str) -> RolesResponse:
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": ROLES_PROMPT.format(resume_text=resume_text)}],
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
        return RolesResponse(roles=[RoleMatch(**r) for r in data["roles"]])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse OpenAI response: {str(e)}")


@router.post("/roles/text", response_model=RolesResponse, include_in_schema=False)
async def analyze_roles_text(body: ResumeTextRequest):
    """
    Submit your resume as plain text and discover the top 3 job roles
    that best match your skills and experience. Each role includes a
    match score out of 100 and the key skills that make you a strong fit.
    Useful for quick testing without a PDF file.
    """
    if not body.resume_text.strip():
        raise HTTPException(status_code=400, detail="Resume text is empty or missing.")
    return _get_roles_from_openai(body.resume_text.strip())


@router.post("/roles", response_model=RolesResponse, include_in_schema=False)
async def analyze_roles_upload(file: UploadFile = File(...)):
    """
    Upload your PDF resume and discover the top 3 job roles that best
    match your skills and experience. Each role includes a match score
    out of 100 and the key skills from your resume that make you a
    strong fit for that role.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    text = _extract_text_from_pdf(pdf_bytes)
    if not text.strip():
        raise HTTPException(status_code=400, detail="Resume text is empty or missing.")

    return _get_roles_from_openai(text.strip())
