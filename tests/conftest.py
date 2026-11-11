import os
import sys

import pytest
from fakeredis import FakeStrictRedis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["OPENAI_API_KEY"] = "test-key"


@pytest.fixture(autouse=True)
def _isolated_redis(monkeypatch):
    """Point every test at a fresh in-memory Redis.

    Without this, any test that exercises the cache or queue against a Redis
    that happens to be running on localhost (e.g. left up by docker compose
    or loadtest/run.sh) would read and write real state — cached results
    from one test then satisfy another test's request before its mock runs.
    Tests that need their own connection or a down Redis still override this
    with their own monkeypatching.
    """
    from resume_scanner import jobs

    monkeypatch.setattr(jobs, "_redis", FakeStrictRedis())
