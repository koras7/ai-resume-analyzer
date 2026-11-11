#!/usr/bin/env bash
# Load test: the same k6 scenario against the real service, twice —
# CACHE_ENABLED=false then true — with OpenAI replaced at the network
# boundary by loadtest/stub_openai.py (the SDK honors OPENAI_BASE_URL; no
# app code changes, no real OpenAI calls, no real API key).
#
# Usage: ./loadtest/run.sh            # defaults: 20 VUs, 30s per run, 200ms stub delay
#        VUS=50 DURATION=60s STUB_DELAY_MS=200 ./loadtest/run.sh
#
# Needs: k6, docker (for Redis), uv. Writes k6 summaries, logs and a results
# table to loadtest/out/ (gitignored). Leaves the Redis container running.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/loadtest/out"
STUB_PORT="${STUB_PORT:-9100}"
API_PORT="${API_PORT:-8000}"
VUS="${VUS:-20}"
DURATION="${DURATION:-30s}"
export STUB_DELAY_MS="${STUB_DELAY_MS:-200}"

mkdir -p "$OUT"
cd "$ROOT"

command -v k6 >/dev/null || { echo "k6 is not installed (brew install k6)"; exit 1; }

STUB_PID=""
API_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait_for() { # url, name
  for _ in $(seq 1 50); do
    curl -sf -o /dev/null "$1" && return 0
    sleep 0.2
  done
  echo "$2 did not come up at $1"; exit 1
}

# Fixtures, Redis, stub.
[ -f loadtest/fixtures/resume_1.pdf ] || uv run python loadtest/generate_fixtures.py
docker compose up -d redis >/dev/null 2>&1
for _ in $(seq 1 50); do nc -z 127.0.0.1 6379 2>/dev/null && break; sleep 0.2; done
uv run python loadtest/stub_openai.py >"$OUT/stub.log" 2>&1 &
STUB_PID=$!
wait_for "http://127.0.0.1:$STUB_PORT/docs" "stub"

run_once() { # cache_enabled
  local mode="$1"
  # Cold cache for every run, so each run's numbers stand on their own.
  docker compose exec -T redis redis-cli -n 0 flushdb >/dev/null

  OPENAI_API_KEY=dummy \
  OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/" \
  CACHE_ENABLED="$mode" \
  PORT="$API_PORT" \
    uv run resume-scanner >"$OUT/api_cache_$mode.log" 2>&1 &
  API_PID=$!
  wait_for "http://127.0.0.1:$API_PORT/health" "api"

  echo "=== k6 run with CACHE_ENABLED=$mode ($VUS VUs, $DURATION, stub delay ${STUB_DELAY_MS}ms) ==="
  VUS="$VUS" DURATION="$DURATION" k6 run \
    --summary-export "$OUT/summary_cache_$mode.json" \
    loadtest/resume.js

  # Metrics reset with the API process, so these counts are this run's only.
  curl -s "http://127.0.0.1:$API_PORT/metrics" | grep "^cache_operations_total" \
    >"$OUT/cache_metrics_$mode.txt" || true

  kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null || true
  API_PID=""
}

run_once false
run_once true

uv run python - "$OUT" "$VUS" "$DURATION" "$STUB_DELAY_MS" <<'EOF' | tee "$OUT/results_table.md"
import json, re, sys
out, vus, duration, delay = sys.argv[1:5]

def load(mode):
    with open(f"{out}/summary_cache_{mode}.json") as f:
        m = json.load(f)["metrics"]
    row = {
        "reqs": m["http_reqs"]["count"],
        "rps": m["http_reqs"]["rate"],
        "p50": m["http_req_duration"]["med"],
        "p95": m["http_req_duration"]["p(95)"],
        "err": m["http_req_failed"]["value"],
    }
    hits = misses = 0
    try:
        for line in open(f"{out}/cache_metrics_{mode}.txt"):
            n = float(line.rsplit(" ", 1)[1])
            if 'result="hit"' in line: hits += n
            if 'result="miss"' in line: misses += n
    except FileNotFoundError:
        pass
    row["hit_rate"] = hits / (hits + misses) if hits + misses else None
    return row

off, on = load("false"), load("true")
def pct(v): return "—" if v is None else f"{100 * v:.1f}%"
print(f"\n{vus} VUs, {duration} per run, stub delay {delay}ms\n")
print("| Run | Requests | Throughput (req/s) | p50 (ms) | p95 (ms) | Error rate | Cache hit rate |")
print("|---|---|---|---|---|---|---|")
for name, r in (("Cache off", off), ("Cache on", on)):
    print(f"| {name} | {r['reqs']:.0f} | {r['rps']:.1f} | {r['p50']:.1f} | {r['p95']:.1f} | {pct(r['err'])} | {pct(r['hit_rate'])} |")
EOF
