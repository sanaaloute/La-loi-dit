# Operations Runbook

## Start / stop

```bash
docker compose up -d --build        # start everything
docker compose ps                   # status + healthchecks
docker compose logs -f api          # follow API logs
docker compose down                 # stop (keeps volumes)
docker compose down -v              # stop and DELETE all data
make docker-up / docker-down        # same via Makefile
```

Local dev without Docker: `uvicorn backend.api.main:app --reload`
(Makefile: `make dev`).

## Logs

- API / worker: `docker compose logs -f api celery-worker`
- Nginx access: inside the nginx container at `/var/log/nginx/access.log`
- Temporal workflow histories: Temporal UI on :8080
- LLM traces: Langfuse on :3000

## Backup / restore PostgreSQL

```bash
# backup
docker compose exec postgres pg_dump -U legal legal_ai | gzip > backup-$(date +%F).sql.gz

# restore
gunzip -c backup-2026-07-26.sql.gz | docker compose exec -T postgres psql -U legal legal_ai
```

Back up daily; also snapshot the `milvus-data` volume after large ingestion
runs (or rely on re-ingestion from source documents, which are the real
system of record).

## Re-indexing documents

The ingestion CLI takes a file or directory (`backend/ingestion/pipeline.py`):

```bash
# ingest one document or a whole directory (idempotent: unchanged content → skipped_duplicate)
docker compose exec api python -m backend.ingestion.pipeline /app/data/docs/code-du-travail.pdf \
    --name "Code du travail" --url "https://www.jo.gouv.bf/..."
```

Each successful ingest bumps the document `version` and records a
`document_versions` row, so the conflict resolver keeps timeline reasoning
correct (old versions remain queryable by scenario date). To rebuild from
scratch, drop the Milvus collection (`legal_chunks`) and re-ingest the
source directory.

## Freshness monitor

Official sources go stale. `backend/ingestion/freshness.py`
(`FreshnessMonitor`) polls configured RSS/web sources, compares against
saved state, and emits a `ChangeEvent` when a source publishes something
newer — wire its `on_change` callback to queue a re-ingestion (e.g. via
Celery beat). Verify with:

```bash
docker compose logs celery-worker | grep -i freshness
```

## Common failure modes

| Failure | System behavior | How to verify |
|---|---|---|
| **Milvus down** | `get_vector_store` falls back to the in-memory vector store; retrieval still works against whatever was indexed in-process (cold start = empty) | `GET /ready` reports milvus degraded; `docker compose logs api` shows the fallback notice |
| **Redis down** | Cache falls back to the process-local `InMemoryCache`; Celery/Temporal queueing is unavailable but chat works | `GET /ready`; latency may rise (no shared cache) — check the cache-hit Grafana panel |
| **LLM provider down / no key** | `mock` provider or deterministic fallbacks: heuristic planner, template response generator quoting only real evidence. Answers stay grounded; errors appear in `state.errors` | Response `trace` contains `planner_llm_fallback: …`; answer text comes from the evidence template |
| **Postgres down** | API `/ready` fails; chat answers still returned but memory persistence is skipped (best-effort) | `GET /ready` → 503; `docker compose logs postgres` |
| **Temporal down** | With `TEMPORAL_ENABLED=false` nothing changes; with it enabled, new workflows fail to start — disable the flag to run synchronously | Temporal UI unreachable; `docker compose logs temporal` |
| **celery-worker crash-loops** | The service expects `backend.workflows.celery_app` (background-task subsystem); until that module lands, comment the service out — the API does not depend on it | `docker compose ps` shows the worker restarting |
| **Nginx rate limit hit** | 429 responses for the offending IP only | `docker compose logs nginx` |

After fixing a dependency, `docker compose restart api` re-wires the
adapters on boot.

## Health endpoints

- `GET /health` — liveness (used by Docker healthchecks)
- `GET /ready` — per-backend status (postgres, redis, milvus live vs.
  fallback)
