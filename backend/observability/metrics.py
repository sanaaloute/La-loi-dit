"""Prometheus metrics with a safe no-op fallback.

If `prometheus_client` is not installed, every metric object degrades to a
no-op so instrumented code paths never fail. Imports are lazy (inside
`_build_metrics`) to keep module import cheap and dependency-optional.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator


class _NoOpMetric:
    """Metric stand-in exposing the prometheus_client surface we use."""

    def labels(self, *args: Any, **kwargs: Any) -> "_NoOpMetric":
        return self

    def inc(self, amount: float = 1.0) -> None:
        pass

    def observe(self, amount: float) -> None:
        pass

    def set(self, value: float) -> None:
        pass


def _build_metrics() -> dict[str, Any]:
    try:
        from prometheus_client import Counter, Histogram
    except Exception:  # pragma: no cover - dependency optional
        return {}

    latency_buckets = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
    return {
        "http_requests_total": Counter(
            "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
        ),
        "http_request_latency_seconds": Histogram(
            "http_request_latency_seconds", "HTTP request latency", buckets=latency_buckets
        ),
        "chat_requests_total": Counter("chat_requests_total", "Total chat requests"),
        "chat_latency_seconds": Histogram(
            "chat_latency_seconds", "End-to-end chat latency", buckets=latency_buckets
        ),
        "retrieval_latency_seconds": Histogram(
            "retrieval_latency_seconds", "Retrieval latency", buckets=latency_buckets
        ),
        "llm_latency_seconds": Histogram(
            "llm_latency_seconds", "LLM call latency", buckets=latency_buckets
        ),
        "agent_latency_seconds": Histogram(
            "agent_latency_seconds", "Per-node agent latency", ["node"], buckets=latency_buckets
        ),
        "errors_total": Counter("errors_total", "Errors by kind", ["kind"]),
        "tool_calls_total": Counter("tool_calls_total", "Tool invocations by tool", ["tool"]),
        "retries_total": Counter("retries_total", "Retries by kind", ["kind"]),
        "memory_hits_total": Counter("memory_hits_total", "Memory recall hits"),
        "cache_hits_total": Counter("cache_hits_total", "Cache hits"),
        "tokens_used_total": Counter(
            "tokens_used_total", "LLM tokens used", ["direction"]  # input | output
        ),
    }


_metrics = _build_metrics()


def _get(name: str) -> Any:
    return _metrics.get(name) or _NoOpMetric()


http_requests_total = _get("http_requests_total")
http_request_latency_seconds = _get("http_request_latency_seconds")
chat_requests_total = _get("chat_requests_total")
chat_latency_seconds = _get("chat_latency_seconds")
retrieval_latency_seconds = _get("retrieval_latency_seconds")
llm_latency_seconds = _get("llm_latency_seconds")
agent_latency_seconds = _get("agent_latency_seconds")
errors_total = _get("errors_total")
tool_calls_total = _get("tool_calls_total")
retries_total = _get("retries_total")
memory_hits_total = _get("memory_hits_total")
cache_hits_total = _get("cache_hits_total")
tokens_used_total = _get("tokens_used_total")


@contextmanager
def time_histogram(histogram: Any) -> Iterator[None]:
    """Observe the wrapped block's duration into `histogram` (no-op safe)."""
    started = time.perf_counter()
    try:
        yield
    finally:
        try:
            histogram.observe(time.perf_counter() - started)
        except Exception:
            pass  # metrics must never break the request path
