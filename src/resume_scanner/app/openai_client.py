"""Shared, lazily created OpenAI client.

The client is created on first use instead of at import time, so the app can
start (and report "not ready") even when OPENAI_API_KEY is missing.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import HTTPException
from openai import OpenAI

load_dotenv()

_client: Optional[OpenAI] = None


def is_openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        if not is_openai_configured():
            raise HTTPException(status_code=503, detail="OpenAI API key is not configured.")
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client
