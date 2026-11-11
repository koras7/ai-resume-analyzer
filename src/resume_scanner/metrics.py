"""Prometheus metrics (RED: Rate, Errors, Duration).

- Rate:     rate(http_requests_total[5m])
- Errors:   rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])
- Duration: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

Labels use the route *template* (e.g. "/items/{id}"), never the raw URL, so the
number of time series stays bounded no matter what paths clients send.

Metrics live in this process's memory. With several uvicorn workers you'd need
prometheus_client's multiprocess mode; this service runs a single worker.
"""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily

# Sized for this API: most requests are fast, but LLM-backed ones take seconds.
LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60)

# Requests that matched no route (404s for random paths) share one label value.
UNMATCHED_ROUTE = "unmatched"

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route and status code.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Time to handle an HTTP request.",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being handled.",
)
OPENAI_REQUESTS = Counter(
    "openai_requests_total",
    "Calls to the OpenAI API, by operation and outcome (success or error).",
    ["operation", "outcome"],
)
OPENAI_REQUEST_DURATION = Histogram(
    "openai_request_duration_seconds",
    "Time spent waiting on the OpenAI API.",
    ["operation"],
    buckets=LATENCY_BUCKETS,
)


JOBS_ENQUEUED = Counter(
    "jobs_enqueued_total",
    "Background jobs submitted to the queue, by operation.",
    ["operation"],
)
CACHE_OPERATIONS = Counter(
    "cache_operations_total",
    "Result cache lookups, by operation and result (hit or miss).",
    ["operation", "result"],
)


class JobQueueCollector:
    """Scrape-time view of the RQ queue, read from Redis.

    The RQ worker is a separate process, so its Prometheus counters can't
    live in this registry; queue state is read from Redis at scrape time
    instead. When Redis is unreachable the collector yields nothing, so the
    rest of /metrics still renders.
    """

    def collect(self):
        from resume_scanner import jobs

        try:
            queue = jobs.get_queue()
            depth = GaugeMetricFamily(
                "job_queue_depth",
                "Jobs waiting in the queue.",
                labels=["queue"],
            )
            depth.add_metric([queue.name], queue.count)
            states = GaugeMetricFamily(
                "job_queue_registry_jobs",
                "Jobs currently tracked per state; finished and failed expire after their TTL.",
                labels=["queue", "state"],
            )
            states.add_metric([queue.name, "started"], queue.started_job_registry.count)
            states.add_metric([queue.name, "finished"], queue.finished_job_registry.count)
            states.add_metric([queue.name, "failed"], queue.failed_job_registry.count)
            return [depth, states]
        except Exception:
            return []


REGISTRY.register(JobQueueCollector())


def route_label(scope) -> str:
    route = scope.get("route")
    return getattr(route, "path", None) or UNMATCHED_ROUTE


def observe_request(method: str, route: str, status: int, duration_s: float) -> None:
    HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, route=route).observe(duration_s)


def observe_openai_call(operation: str, duration_s: float, success: bool) -> None:
    OPENAI_REQUESTS.labels(operation=operation, outcome="success" if success else "error").inc()
    OPENAI_REQUEST_DURATION.labels(operation=operation).observe(duration_s)


def observe_job_enqueued(operation: str) -> None:
    JOBS_ENQUEUED.labels(operation=operation).inc()


def observe_cache_lookup(operation: str, hit: bool) -> None:
    CACHE_OPERATIONS.labels(operation=operation, result="hit" if hit else "miss").inc()


def render_latest() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
