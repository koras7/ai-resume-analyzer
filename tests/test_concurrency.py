"""The blocking-upstream regression test.

The OpenAI client is synchronous; called inline from an `async def` handler
it blocks the event loop and serializes all in-flight requests. The routers
run their analysis helpers via `run_in_threadpool`, so slow upstream calls
must overlap. These tests fail if a router goes back to calling its helper
inline.

They drive the app through one shared event loop (httpx.ASGITransport +
asyncio.gather). TestClient would not do: it gives each request its own
portal, so even an event-loop-blocking handler looks concurrent under it.
"""

import asyncio
import time
from unittest.mock import patch

import httpx

from resume_scanner.app.models.schemas import AnalysisResult
from resume_scanner.main import app

SLEEP_S = 0.8
RESULT = AnalysisResult(
    score=85,
    strengths=["a"],
    weaknesses=["b"],
    skills=["c"],
    improved_summary="d",
    recommendation="e",
)


def _slow_analysis(_text):
    time.sleep(SLEEP_S)  # a blocking sleep, like the sync OpenAI client under latency
    return RESULT


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@patch("resume_scanner.app.routers.analyze._analyze_with_openai", side_effect=_slow_analysis)
def test_slow_upstream_calls_overlap(_mock):
    async def main():
        async with _client() as ac:
            started = time.perf_counter()
            responses = await asyncio.gather(
                ac.post("/analyze/text", json={"resume_text": "some resume"}),
                ac.post("/analyze/text", json={"resume_text": "some resume"}),
            )
            return responses, time.perf_counter() - started

    responses, elapsed = asyncio.run(main())

    assert all(r.status_code == 200 for r in responses)
    # Serialized behind a blocked event loop this takes >= 2 * SLEEP_S; the
    # bound is generous enough to absorb CI jitter but not serialization.
    assert elapsed < 2 * SLEEP_S * 0.9, f"requests serialized: {elapsed:.2f}s"


@patch("resume_scanner.app.routers.analyze._analyze_with_openai", side_effect=_slow_analysis)
def test_event_loop_stays_responsive_during_upstream_call(_mock):
    """While one request waits on the (mocked) upstream, /health must answer."""

    async def main():
        async with _client() as ac:
            started = time.perf_counter()

            async def health_mid_flight():
                await asyncio.sleep(SLEEP_S / 4)  # let the slow request reach its sleep
                response = await ac.get("/health")
                # Timed from the moment both requests launched: a blocked
                # loop delays the sleep timer itself, so measuring only the
                # GET would start the clock after the block ended and hide
                # the regression.
                return response, time.perf_counter() - started

            slow, (health, health_s) = await asyncio.gather(
                ac.post("/analyze/text", json={"resume_text": "some resume"}),
                health_mid_flight(),
            )
            return slow, health, health_s

    slow, health, health_s = asyncio.run(main())

    assert slow.status_code == 200
    assert health.status_code == 200
    # Loop free: ~SLEEP_S/4 plus milliseconds. Loop blocked: >= SLEEP_S.
    assert health_s < SLEEP_S * 0.75, f"/health waited on the upstream call: {health_s:.2f}s"
