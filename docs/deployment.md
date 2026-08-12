# Deployment

## Compose topology

`docker compose up -d --build` starts the full stack on one bridge network
(`legal-net`):

| Service | Image | Port | Role |
|---|---|---|---|
| api | `docker/backend.Dockerfile` | 8000 (localhost only) | FastAPI app (uvicorn) |
| celery-worker | same image | — | background ingestion/batch jobs |
| frontend | `frontend/Dockerfile` | 3000 (localhost only) | Next.js UI — proxy from your external nginx |
| postgres | postgres:16-alpine | 5432 | system of record |
| redis | redis:7-alpine | 6379 | cache + Celery broker |
| etcd, minio, milvus | milvusdb/milvus:v2.4.15 stack | 19530 | vector store |
| temporal, temporal-ui | temporalio/auto-setup:1.24 | 7233, 8080 | durable workflows + UI |
| prometheus | prom/prometheus:v2.53.0 | 9090 | metrics |
| grafana | grafana/grafana:10.4.2 | 3001 | dashboards (admin/admin — change) |
| langfuse | langfuse/langfuse:2 | 3000 | LLM traces (schema `langfuse` in postgres) |

Named volumes: `postgres-data`, `redis-data`, `etcd-data`, `minio-data`,
`milvus-data`, `temporal-data`, `prometheus-data`, `grafana-data`.
The host folder `./data` is bind-mounted to `/app/data` in `api` and
`celery-worker`, so document edits under `./data/legal_docs` are immediately
visible inside the containers.

## Deploy scripts

| Script | Purpose |
|---|---|
| `scripts/deploy.sh` | First-time / full redeploy: wipes containers, images and volumes (fresh Milvus index and DBs), rebuilds with `--no-cache`, starts the stack, indexes `./data/legal_docs`. `-y` skips the confirmation. |
| `scripts/update.sh` | Code update: `git pull --ff-only`, rebuild (cached), restart. Index untouched. `--no-pull` skips the pull. |
| `scripts/reindex.sh` | Incremental index update: new/edited documents are indexed, deleted documents purged (content-hash diff). `--full-reindex` wipes the index first. |

Set `LEGAL_AI_INGEST_ON_STARTUP=true` to have the API index
`data/legal_docs` in the background on every boot (idempotent, lock-guarded
against multi-worker double runs).

## Host nginx (external reverse proxy)

The compose stack does **not** include an nginx container. The host's nginx is
managed by:

- `docker/host-nginx/yawoto.neobytech.net.conf` — the vhost template.
- `scripts/install-nginx-config.sh` — installs the template and reloads nginx.

Both `scripts/deploy.sh` and `scripts/update.sh` call the install script
automatically when a host nginx binary is available. The default config assumes
Let's Encrypt certificates at:

```
/etc/letsencrypt/live/yawoto.neobytech.net/fullchain.pem
/etc/letsencrypt/live/yawoto.neobytech.net/privkey.pem
```

Obtain certificates first (e.g. `certbot --nginx`), or override the paths:

```bash
sudo scripts/install-nginx-config.sh yawoto.neobytech.net \
  /path/to/fullchain.pem /path/to/privkey.pem
```

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
- **Scalability / HA**: `LEGAL_AI_STRICT_INFRA` (default: on when
  `LEGAL_AI_ENV=production`), `LEGAL_AI_WEB_WORKERS` (uvicorn workers,
  default 2 in the compose `api` command)
- **Observability**: `LEGAL_AI_OTEL_ENDPOINT` / `LEGAL_AI_OTEL_ENABLED`,
  `LEGAL_AI_LANGFUSE_*`
- **Billing**: `LEGAL_AI_PADDLE_*` — disabled by default; see
  [billing.md](billing.md) for the full Paddle setup (sandbox vs prod,
  price ids, webhook registration, ngrok for local testing).

## Strict infrastructure mode

Outside production the app degrades gracefully: Milvus/Redis/Postgres
outages silently fall back to in-memory/SQLite substitutes so development
works with zero services. **Strict infrastructure mode** (on by default when
`LEGAL_AI_ENV=production`, override with `LEGAL_AI_STRICT_INFRA=true|false`)
changes the contract:

- fallbacks are **reported, not silent** — every dependency gets an
  `infra_status` entry (`ok` / `degraded: reason`) collected at boot. This
  covers Milvus/Redis/Postgres and the softer fallbacks too: mock LLM
  provider, hash embeddings, in-memory cache, SQLite-pivoting user/memory/
  legal-graph stores;
- the user store may not fall back to a local SQLite file: with Postgres
  down it stays unavailable (registration returns 503) instead of writing
  accounts to a throwaway store;
- `/ready` returns **HTTP 503** when a critical dependency is down, so the
  orchestrator stops routing traffic. The critical set is configurable via
  `LEGAL_AI_STRICT_CRITICAL_COMPONENTS` (comma-separated, validated at boot;
  default `milvus,postgres,database_probe,llm,embeddings,user_store`, plus
  `vector_store_probe` while Milvus is enabled). `redis`, `memory_store` and
  `legal_graph` stay non-critical unless added explicitly.

## Health and readiness probes

- `GET /health` — dumb liveness, always 200 when the process is up.
- `GET /ready` — live per-dependency probes (`SELECT 1` on the configured
  database, cache round-trip, vector-store count) plus the boot-time
  `infra_status`. Always returns `{status, checks}` with
  `status ∈ {"ready", "degraded", "not_ready"}`; the HTTP code is 200 except
  in strict mode with a critical dependency down (503, see above). Both
  endpoints are exempt from rate limiting, as is `/metrics`.

## Per-tier rate limits

`LEGAL_AI_RATE_LIMIT_PER_MINUTE` is now only the **anonymous/IP** default.
Authenticated requests are limited per subscription tier from the catalog
(`backend/core/catalog.py`, overridable via `LEGAL_AI_TIER_CATALOG_JSON`):

| Tier | Requests/minute |
|---|---|
| anonymous (per IP) | `LEGAL_AI_RATE_LIMIT_PER_MINUTE` (30) |
| gratuit | 30 |
| pro | 120 |
| cabinet | 600 |

The middleware resolves the tier from the JWT `tier` claim — no database
lookup on the hot path; invalid/expired tokens fall back to the anonymous IP
bucket.

## Scaling workers

The compose `api` service runs
`uvicorn ... --workers ${LEGAL_AI_WEB_WORKERS:-2}`. Each worker is a full
process with its own lifespan (its own AppContext). Anything held in process
memory is therefore **per-worker**:

- the rate limiter uses shared Redis counters automatically when
  `LEGAL_AI_REDIS_ENABLED=true` (fixed window, atomic INCR); without Redis
  each worker enforces its own limit (effective limit × workers);
- the answer cache, retrieval cache and memory hot cache already sit on the
  shared cache abstraction, so they are coherent across workers as soon as
  Redis is enabled;
- the in-memory vector store fallback is per-worker — do not scale workers
  without Milvus.

Rule of thumb: **workers > 1 requires Redis, Postgres and Milvus** — exactly
what strict mode verifies at boot and via `/ready`.

## Production checklist

1. **Secrets** — set strong `LEGAL_AI_SECRET_KEY`, Postgres credentials,
   Grafana admin password, Langfuse `NEXTAUTH_SECRET`/`SALT`. Never commit
   `.env` (it is gitignored).
2. **TLS / reverse proxy** — the compose stack no longer ships its own nginx.
   Use the nginx already installed on the host. A minimal vhost for
   `yawoto.neobytech.net` is shown in the `.env.example` comments. Put TLS
   certificates in the host's normal cert location (e.g. Let's Encrypt) and
   proxy `https://yawoto.neobytech.net` to `http://127.0.0.1:3000` (the
   frontend container). Redirect :80 → :443 on the host.
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
   registration; enforce rate limits at your host Nginx (10 r/s per IP with
   burst 20 is a sensible starting point).
8. **Image pinning** — the compose file pins minor versions; review and pin
   digests for regulated environments.
