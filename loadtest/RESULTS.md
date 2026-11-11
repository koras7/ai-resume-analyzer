# Load test results — result cache on vs off

Measured with k6 against the real service, with OpenAI replaced at the network
boundary by a latency-matched stub (`stub_openai.py`). The service runs
unmodified — the OpenAI SDK honors `OPENAI_BASE_URL` — so the measured path is
our HTTP handling, PDF extraction, OpenAI round trip (stubbed) and caching,
not OpenAI's actual latency. No real OpenAI calls are made.

## Numbers (2026-11-11)

Scenario: 20 virtual users POSTing one of 3 fixture PDFs (picked at random)
to `/resume` for 30s per run, cold cache each run, stub delay 200ms.

| Run | Requests | Throughput (req/s) | p50 (ms) | p95 (ms) | Error rate | Cache hit rate |
|---|---|---|---|---|---|---|
| `CACHE_ENABLED=false` | 151 | 4.6 | 4,104 | 7,891 | 0.0% | — (no lookups) |
| `CACHE_ENABLED=true` | 14,900 | 496.1 | 38.3 | 43.1 | 0.0% | 100.0% (14,897 hits / 3 misses) |

**~108× throughput and a ~185× lower p95, at a 0% error rate in both runs.**
Every cache hit is also one avoided OpenAI call — at this rate, 14,897 calls'
worth of API cost and latency in 30 seconds.

## Why the no-cache ceiling is ~5 req/s (and that's the next fix)

The OpenAI SDK client is synchronous but is called inside `async def`
handlers, so every (stubbed) upstream call blocks the event loop. With one
uvicorn worker, requests serialize behind it: the ceiling is
1 / stub delay = 1 / 0.2s = **5 req/s no matter how many clients connect**,
and with 20 VUs queued behind one 200ms call each, p50 sits near
20 × 200ms ≈ 4s. The measured 4.6 req/s and 4.1s p50 match that prediction.
The cache-on run skips the blocking call entirely, which is why it blows past
the ceiling. Fixing the blocking call itself (async client or thread pool) is
step 4b; its numbers will be added here.

The hit rate is ~100% by design: 3 fixture resumes mean 3 cold misses and
then every request repeats known content. Real-world hit rates depend on how
often the same resume is re-analyzed; the before/after contrast here measures
the cost of a miss vs the cost of a hit, not a production hit rate.

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
Raw output lands in `loadtest/out/` (gitignored).

## Environment

- MacBook, Apple M2, 8 cores, 16 GB RAM, macOS 26.3.1
- k6 v2.3.0, Python 3.14.2, single uvicorn worker
- Stub delay 200ms (`STUB_DELAY_MS`), Redis 8.2 in Docker (`docker compose up -d redis`)
