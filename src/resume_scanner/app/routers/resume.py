import fitz
from fastapi import APIRouter, File, HTTPException, UploadFile

from resume_scanner.app.models.schemas import ResumeAnalysisResponse
from resume_scanner.app.openai_client import chat_completion

router = APIRouter()

RESUME_PROMPT = """\
You are a professional resume analyst and career advisor. Analyze the resume below \
and respond with valid JSON only — no markdown, no extra text — in this exact format:
{{
  "score": <integer 0-100 rating overall resume quality>,
  "strengths": ["<strength1>", "<strength2>", "<strength3>"],
  "weaknesses": ["<weakness1>", "<weakness2>", "<weakness3>"],
  "skills": ["<skill1>", "<skill2>", "<skill3>"],
  "improved_summary": "<a rewritten, stronger professional summary>",
  "recommendation": "<one concrete next career step for this candidate>",
  "matched_roles": [
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


def _analyze_resume(resume_text: str) -> ResumeAnalysisResponse:
    return chat_completion(
        "resume",
        RESUME_PROMPT.format(resume_text=resume_text),
        ResumeAnalysisResponse,
        cache_text=resume_text,
    )


@router.post("/resume", response_model=ResumeAnalysisResponse)
async def analyze_resume(file: UploadFile = File(...)):
    """
    Upload a PDF resume for a combined AI-powered analysis and job role matching
    in a single request. Returns an overall quality score out of 100, strengths,
    weaknesses, top skills, an improved professional summary, a career recommendation,
    and the top matched job roles with per-role match scores and key skills.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)

    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF.")

    return _analyze_resume(text.strip())
