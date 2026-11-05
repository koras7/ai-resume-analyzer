import json

import fitz
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from openai import OpenAIError

from resume_scanner.app.models.schemas import ATSResponse, ATSResult
from resume_scanner.app.openai_client import get_openai_client

router = APIRouter()

ATS_PROMPT = """\
You are an ATS (Applicant Tracking System) expert. Compare the resume below against \
the provided job description and respond with valid JSON only — no markdown, no extra \
text — in this exact format:
{{
  "ats_score": <integer 0-100 representing how well the resume matches the job description>,
  "matched_keywords": ["<keyword found in both resume and job description>"],
  "missing_keywords": ["<important keyword in job description but absent from resume>"],
  "suggestions": ["<actionable suggestion to improve ATS compatibility>"],
  "verdict": "<one sentence on whether this resume would pass ATS screening>"
}}

Job Description:
{job_description}

Resume:
{resume_text}
"""


def _run_ats_analysis(resume_text: str, job_description: str) -> ATSResult:
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": ATS_PROMPT.format(
                        job_description=job_description,
                        resume_text=resume_text,
                    ),
                }
            ],
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
        return ATSResult(**data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse OpenAI response: {str(e)}")


@router.post("/ats", response_model=ATSResponse)
async def ats_check(
    file: UploadFile = File(...),
    job_description: str = Form(...),
):
    """
    Upload a PDF resume and provide a job description to receive an ATS compatibility
    score. Returns a score out of 100, keywords matched and missing relative to the
    job description, actionable suggestions to improve ATS pass-through, and a
    one-sentence verdict on whether the resume would clear ATS screening.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    resume_text = "\n".join(page.get_text() for page in doc)

    if not resume_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF.")

    if not job_description.strip():
        raise HTTPException(status_code=400, detail="Job description is empty or missing.")

    result = _run_ats_analysis(resume_text.strip(), job_description.strip())
    return ATSResponse(filename=file.filename, result=result)
