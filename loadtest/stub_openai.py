"""A stand-in for the OpenAI API, used by the load test.

Serves POST /chat/completions with a canned, schema-valid response after an
artificial delay (STUB_DELAY_MS, default 200ms) that imitates OpenAI's
latency. The real service runs against it unmodified: the OpenAI SDK honors
OPENAI_BASE_URL, so the stub replaces the dependency at the network
boundary and the measured path includes our HTTP handling, PDF extraction,
JSON parsing and caching — our service, not OpenAI.

Run: uv run python loadtest/stub_openai.py
"""

import asyncio
import json
import os

import uvicorn
from fastapi import FastAPI

app = FastAPI()

DELAY_S = float(os.getenv("STUB_DELAY_MS", "200")) / 1000.0

# A valid ResumeAnalysisResponse (the /resume endpoint's schema).
CONTENT = json.dumps(
    {
        "score": 85,
        "strengths": ["Strong Python skills", "Clear project outcomes", "Good formatting"],
        "weaknesses": ["No certifications", "Short tenure", "Missing metrics"],
        "skills": ["Python", "FastAPI", "Docker"],
        "improved_summary": "Results-driven developer with measurable impact.",
        "recommendation": "Quantify achievements in each role.",
        "matched_roles": [
            {
                "title": "Backend Engineer",
                "reason": "API and infrastructure experience",
                "match_score": 88,
                "key_skills": ["Python", "FastAPI", "Docker"],
            }
        ],
    }
)


@app.post("/chat/completions")
async def chat_completions():
    await asyncio.sleep(DELAY_S)
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": CONTENT},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 500, "completion_tokens": 150, "total_tokens": 650},
    }


if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.getenv("STUB_PORT", "9100")), log_level="warning"
    )
