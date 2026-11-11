# Load test results — blocking vs non-blocking, cache on vs off

Measured with k6 against the real service, with OpenAI replaced at the network
boundary by a latency-matched stub (`stub_openai.py`). The service runs
unmodified — the OpenAI SDK honors `OPENAI_BASE_URL` — so the measured path is
our HTTP handling, PDF extraction, OpenAI round trip (stubbed) and caching,
not OpenAI's actual latency. No real OpenAI calls are made.

## Numbers (2026-11-11)

Scenario, identical in every run: 20 virtual users POSTing one of 3 fixture
PDFs (picked at random) to `/resume` for 30s per run, cold cache each run,
stub delay 200ms, single uvicorn worker.

"Blocking" is the service before step 4b: the synchronous OpenAI client called
inline from `async def` handlers, blocking the event loop. "Non-blocking" is
after: the same client run via `run_in_threadpool`, keeping the loop free.

| Run | Requests | Throughput (req/s) | p50 (ms) | p95 (ms) | Error rate | Cache hit rate |
|---|---|---|---|---|---|---|
| Blocking, cache off | 151 | 4.6 | 4,104 | 7,891 | 0.0% | — (no lookups) |
| Blocking, cache on | 14,900 | 496.1 | 38.3 | 43.1 | 0.0% | 100.0% (14,897 hits / 3 misses) |
| Non-blocking, cache off | 2,516 | 83.4 | 238.7 | 277.5 | 0.0% | — (no lookups) |
| Non-blocking, cache on | 17,653 | 588.2 | 32.7 | 44.7 | 0.0% | 99.9% (17,633 hits / 20 misses) |

Two independent wins, measured separately:

- **Unblocking the event loop (cache off): 4.6 → 83.4 req/s (~18×) and p95
  7.9s → 278ms (~28×)**, same error rate. No caching involved — this is purely
  moving the blocking call off the loop.
- **Caching on top of that: 83.4 → 588 req/s and p95 278ms → 45ms**, with
  every hit also an avoided OpenAI call (17,633 calls' worth of API cost in
  30s at this rate).

## The numbers match the model

**Blocking, cache off:** every upstream call blocks the event loop, so one
worker serializes at 1 / stub delay = **5 req/s no matter how many clients
connect**, and 20 VUs queued behind one 200ms call each put p50 near
20 × 200ms ≈ 4s. Measured: 4.6 req/s, p50 4.1s.

**Non-blocking, cache off:** with the call in a threadpool, 20 VUs overlap
their 200ms waits, so the bound becomes VUs / delay = 20 / 0.2s =
**100 req/s**, p50 ≈ 200ms + per-request work. Measured: 83.4 req/s and p50
239ms — i.e. ~39ms of our own work per request (multipart parsing, PDF
extraction, JSON), which accounts for the gap from the bound. Scaling
further is now a knob (more VUs utilize more of anyio's default 40-thread
limiter; past that, more workers), not a rewrite.

The ~100% hit rate is by design: 3 fixture resumes mean a few cold misses and
then every request repeats known content. Real-world hit rates depend on how
often the same resume is re-analyzed; the cache rows measure the cost of a
miss vs the cost of a hit, not a production hit rate. (The 20 misses in the
last run are the race between the first concurrent requests per fixture —
each called the stub before any of them had stored the result; harmless
duplicate work that only exists on a cold cache.)

## Reproduce

```bash
./loadtest/run.sh                           # 20 VUs, 30s per run, 200ms stub delay
VUS=50 DURATION=60s STUB_DELAY_MS=200 ./loadtest/run.sh
```

The script generates the fixture PDFs (fake people, no real PII), starts
Redis (Docker) and the stub, runs the API twice with
`OPENAI_API_KEY=dummy OPENAI_BASE_URL=http://127.0.0.1:9100/` — once per
`CACHE_ENABLED` value, cold cache each time — and prints the table above from
the k6 summaries plus `cache_operations_total` scraped from `/metrics`.
Raw output lands in `loadtest/out/` (gitignored). The blocking rows were
measured on the pre-step-4b code (commit `e480e8e` and earlier).

## Environment

- MacBook, Apple M2, 8 cores, 16 GB RAM, macOS 26.3.1
- k6 v2.3.0, Python 3.14.2, single uvicorn worker
- Stub delay 200ms (`STUB_DELAY_MS`), Redis 8.2 in Docker (`docker compose up -d redis`)
