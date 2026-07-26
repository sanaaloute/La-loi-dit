# Deployment

## Compose topology

`docker compose up -d --build` starts the full stack on one bridge network
(`legal-net`):

| Service | Image | Port | Role |
|---|---|---|---|
| api | `docker/backend.Dockerfile` | 8000 | FastAPI app (uvicorn) |
| celery-worker | same image | — | background ingestion/batch jobs |
| nginx | nginx:1.27-alpine | 80/443 | reverse proxy, rate limit, TLS termination |
| postgres | postgres:16-alpine | 5432 | system of record |
| redis | redis:7-alpine | 6379 | cache + Celery broker |
| etcd, minio, milvus | milvusdb/milvus:v2.4.15 stack | 19530 | vector store |
| temporal, temporal-ui | temporalio/auto-setup:1.24 | 7233, 8080 | durable workflows + UI |
| prometheus | prom/prometheus:v2.53.0 | 9090 | metrics |
| grafana | grafana/grafana:10.4.2 | 3001 | dashboards (admin/admin — change) |
| langfuse | langfuse/langfuse:2 | 3000 | LLM traces (schema `langfuse` in postgres) |

Named volumes: `postgres-data`, `redis-data`, `etcd-data`, `minio-data`,
`milvus-data`, `temporal-data`, `prometheus-data`, `grafana-data`,
`app-data`.

## Environment variables

Everything uses the `LEGAL_AI_` prefix (see `.env.example`; the compose file
overrides service addresses). Key groups:

- **App**: `LEGAL_AI_ENV`, `LEGAL_AI_SECRET_KEY`, `LEGAL_AI_DATA_DIR`,
  `LEGAL_AI_LOG_LEVEL`
- **LLM**: `LEGAL_AI_LLM_PROVIDER` (`mock` default; `openai`, `anthropic`,
  `gemini`, `deepseek`, `qwen`, `kimi`, `ollama`), `LEGAL_AI_LLM_MODEL`,
  `LEGAL_AI_LLM_API_KEY`, `LEGAL_AI_LLM_API_BASE`,
  `LEGAL_AI_LLM_TEMPERATURE`, `LEGAL_AI_EMBEDDING_MODEL`
- **Stores**: `LEGAL_AI_DATABASE_URL`, `LEGAL_AI_REDIS_URL` /
  `LEGAL_AI_REDIS_ENABLED`, `LEGAL_AI_MILVUS_HOST` /
  `LEGAL_AI_MILVUS_PORT` / `LEGAL_AI_MILVUS_ENABLED`
- **Workflows**: `LEGAL_AI_TEMPORAL_ADDRESS` / `LEGAL_AI_TEMPORAL_ENABLED`,
  `LEGAL_AI_CELERY_BROKER_URL`
- **Security**: `LEGAL_AI_JWT_ALGORITHM`, `LEGAL_AI_JWT_EXPIRE_MINUTES`,
  `LEGAL_AI_RATE_LIMIT_PER_MINUTE`
- **Observability**: `LEGAL_AI_OTEL_ENDPOINT` / `LEGAL_AI_OTEL_ENABLED`,
  `LEGAL_AI_LANGFUSE_*`

## Production checklist

1. **Secrets** — set strong `LEGAL_AI_SECRET_KEY`, Postgres credentials,
   Grafana admin password, Langfuse `NEXTAUTH_SECRET`/`SALT`. Never commit
   `.env` (it is gitignored).
2. **TLS** — terminate at Nginx: drop certs into `docker/nginx/certs/`,
   uncomment the TLS block in `docker/nginx/nginx.conf` and the cert volume
   in `docker-compose.yml`, redirect :80 → :443.
3. **Backups** — schedule `pg_dump` of `legal_ai` and snapshots of the
   `milvus-data`, `minio-data` and `etcd-data` volumes (see
   [operations.md](operations.md)).
4. **Scaling** — `docker compose up -d --scale api=3 --scale celery-worker=2`
   behind Nginx; add Temporal workers with the same image for chat
   throughput. Redis and Postgres are single points — use managed/HA
   variants for real production.
5. **Resource limits** — Milvus standalone wants ≥ 4 GB RAM; give the api
   container a memory limit and uvicorn `--workers` matching CPU.
6. **Observability** — enable OTel (`LEGAL_AI_OTEL_ENABLED=true`) pointed at
   your collector; create a Langfuse project and set its keys; import/alert
   on the Grafana dashboards.
7. **Auth hardening** — create the first `admin` user, then disable public
   registration; enforce rate limits at Nginx (already configured at
   10 r/s per IP with burst 20).
8. **Image pinning** — the compose file pins minor versions; review and pin
   digests for regulated environments.
