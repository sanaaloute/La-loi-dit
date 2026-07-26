# Monitoring

Three complementary layers: **Prometheus** for metrics, **Grafana** for
dashboards, **Langfuse** (+ OpenTelemetry) for LLM/pipeline traces.

## Prometheus metrics

The API exposes `GET /metrics`, scraped every 15 s
(`docker/prometheus/prometheus.yml`). Metrics are defined in
`backend/observability/metrics.py`; every metric degrades to a no-op if
`prometheus_client` is unavailable, so instrumentation can never break a
request.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | counter | `method`, `path`, `status` | HTTP request volume |
| `http_request_latency_seconds` | histogram | — | HTTP request latency |
| `chat_requests_total` | counter | — | chat requests (REST + SSE) |
| `chat_latency_seconds` | histogram | — | end-to-end chat latency (p50/p95 panels) |
| `retrieval_latency_seconds` | histogram | — | retrieval coordinator latency |
| `llm_latency_seconds` | histogram | — | LLM call latency |
| `agent_latency_seconds` | histogram | `node` | per-agent-node latency |
| `errors_total` | counter | `kind` | errors by kind (e.g. `chat_stream`) |
| `retries_total` | counter | `kind` | retry usage by kind |
| `tool_calls_total` | counter | `tool` | sandboxed tool invocations |
| `memory_hits_total` | counter | — | memory recall hits |
| `cache_hits_total` | counter | — | cache hits (misses are not counted) |
| `tokens_used_total` | counter | `direction` (`input`/`output`) | LLM token usage |

All histograms use buckets `0.05 … 60` seconds.

## Grafana

Provisioned automatically (`docker/grafana/provisioning/`): the Prometheus
datasource and the **Legal AI — Overview** dashboard
(`docker/grafana/dashboards/legal-ai-overview.json`) with four starter
panels — request rate, chat latency p50/p95, errors & retries, and cache
hit rate. UI on :3001 (admin/admin in compose — change it).

## Langfuse traces

Set `LEGAL_AI_LANGFUSE_PUBLIC_KEY` / `LEGAL_AI_LANGFUSE_SECRET_KEY`
(`Settings.langfuse_enabled`; client wiring in
`backend/observability/langfuse_client.py`). Each chat run becomes a trace:
planner LLM call, retrieval spans, reasoning/reflection generations,
latency and token usage per step. UI on :3000 in compose (runs its own
migrations into the `langfuse` schema of the postgres service).

## OpenTelemetry

`LEGAL_AI_OTEL_ENABLED=true` + `LEGAL_AI_OTEL_ENDPOINT` (default
`http://localhost:4318`) exports traces via OTLP/HTTP using
`opentelemetry-instrumentation-fastapi`
(`backend/observability/tracing.py`). Point it at any OTLP collector.

## Alert suggestions

| Condition | Suggested threshold | Why |
|---|---|---|
| p95 chat latency | `histogram_quantile(0.95, rate(chat_latency_seconds_bucket[5m]))` > 30 s for 10 min | pipeline degradation or LLM slowness |
| `rate(errors_total[5m])` | > 0.1/s sustained | subsystem failing behind fallbacks |
| `rate(retries_total[5m])` | sustained above baseline | retrieval quality dropping (budget is max 1 — high usage = evidence gaps) |
| `rate(cache_hits_total[5m])` | drops to ~0 | Redis down or cache-key regression |
| `llm_latency_seconds` p95 | > `LEGAL_AI_LLM_TIMEOUT_SECONDS` (60 s) | provider stalling → deterministic fallback mode |
| `/ready` failing | 2 consecutive scrapes | backend dependency down |
| Postgres disk usage | > 80% | message/memory growth |
