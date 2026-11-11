import io

import pdfplumber
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from resume_scanner.app.models.schemas import AnalysisResponse, AnalysisResult, ResumeTextRequest
from resume_scanner.app.openai_client import chat_completion

router = APIRouter()

ANALYZE_PROMPT = """\
You are a professional resume analyst. Analyze the resume below and respond with \
valid JSON only — no markdown, no extra text — in this exact format:
{{
  "score": <integer 0-100 rating overall resume quality>,
  "strengths": ["<strength1>", "<strength2>", "<strength3>"],
  "weaknesses": ["<weakness1>", "<weakness2>", "<weakness3>"],
  "skills": ["<skill1>", "<skill2>", "<skill3>"],
  "improved_summary": "<a rewritten, stronger professional summary>",
  "recommendation": "<one concrete next career step for this candidate>"
}}

Resume:
{resume_text}
"""


def _analyze_with_openai(resume_text: str) -> AnalysisResult:
    return chat_completion(
        "analyze",
        ANALYZE_PROMPT.format(resume_text=resume_text),
        AnalysisResult,
        cache_text=resume_text,
    )


@router.post("/analyze", response_model=AnalysisResponse, include_in_schema=False)
async def analyze(file: UploadFile = File(...)):
    """
    Upload your PDF resume for a comprehensive AI-powered analysis.
    Returns a structured breakdown including an overall quality score
    out of 100, key strengths, areas for improvement, top skills
    identified, and a recommendation for your next career step.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed.")

    contents = await file.read()
    with pdfplumber.open(io.BytesIO(contents)) as pdf:
        text = "".join(page.extract_text() or "" for page in pdf.pages)

    if not text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from PDF.")

    analysis = await run_in_threadpool(_analyze_with_openai, text.strip())
    return AnalysisResponse(filename=file.filename, analysis=analysis)


@router.post("/analyze/text", response_model=AnalysisResponse, include_in_schema=False)
async def analyze_text(body: ResumeTextRequest):
    """
    Submit your resume as plain text for a comprehensive AI-powered
    analysis. Returns the same structured breakdown as the PDF upload
    endpoint. Useful for quick testing without a PDF file.
    """
    if not body.resume_text.strip():
        raise HTTPException(status_code=400, detail="Resume text is empty or missing.")

    analysis = await run_in_threadpool(_analyze_with_openai, body.resume_text.strip())
    return AnalysisResponse(filename=None, analysis=analysis)
