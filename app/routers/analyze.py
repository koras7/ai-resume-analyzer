from fastapi import FastAPI, File, UploadFile, APIRouter
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from openai import OpenAI
import pdfplumber
import io
import os

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
router = APIRouter()

@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if file.content_type != "application/pdf":
        return JSONResponse(
            status_code=400,
            content={"error": "Only PDF files are allowed."}
        )
    # Read and extract text from PDF
    contents = await file.read()
    with pdfplumber.open(io.BytesIO(contents)) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text() or ""

    if not text.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Could not extract text from PDF."}
        )
    response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[
            {"role": "system", "content": "You are a resume analyst. Analyze the resume and return a JSON with: summary, skills (list), strengths (list), improvements (list), and score (number out of 100 rating the overall quality of the resume)."},
            {"role": "user", "content": text}
        ]
    )

    analysis = response.choices[0].message.content
    return {"filename": file.filename, "analysis": analysis}
   
