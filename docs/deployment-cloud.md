# Cloud deployment (production VM + Cloudflare web)

Target layout:

```
                        ┌──────────────────────────────┐
 Browser  ────────────▶ │ Cloudflare Workers (OpenNext)│  yawoto.neobytech.net
 (web app)              │  Next.js frontend            │  orange-cloud (proxied)
                        └─────────────┬────────────────┘
                                       │ /backend-api/* proxied at the edge
                                       ▼
 Mobile app ──────────────────▶  api.yawoto.neobytech.net
 (direct API)                     GREY CLOUD (DNS only) → VM nginx :443
                                       │                 (certbot TLS)
                                       ▼
                        ┌──────────────────────────────┐
                        │ Cloud VM (Docker Compose)     │
                        │  api (GHCR image) · postgres  │
                        │  redis · etcd · minio · milvus│
                        └──────────────────────────────┘
```

Why grey-cloud for the API: Cloudflare's proxy aborts origin requests with no
response after ~100 s (error 524). The dossier analysis endpoints are
non-streaming and can legitimately run for several minutes; chat is fine
because SSE heartbeats keep the connection alive. Keep `api.*` DNS-only and
terminate TLS with nginx + certbot on the VM.

## Prerequisites

- VM with Docker + Docker Compose **v2.24+** (`docker compose version`).
- A GitHub PAT with `read:packages` for `docker login ghcr.io`
  (unless the package is made public).
- Cloudflare account with the zone `neobytech.net` (already the case —
  the Mac-mini tunnel uses it).
- Repo cloned on the VM: `git clone -b minimac https://github.com/sanaaloute/La-loi-dit.git legal-ai-burkina && cd legal-ai-burkina`

## First-time server setup

```bash
# 1. Environment — copy and edit (see checklist below)
cp .env.example .env && $EDITOR .env

# 2. Pull the CI-built backend image
docker login ghcr.io -u <github-user>    # PAT with read:packages
docker compose -f docker-compose.yml -f docker-compose.server.yml pull

# 3. Migrate data (from the Mac, once) — see next section

# 4. Start
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d
curl http://127.0.0.1:8000/health
```

### `.env` checklist for the server (differs from the Mac)

| Variable | Server value |
| --- | --- |
| `LEGAL_AI_ENV` | `production` (already the template default) |
| `LEGAL_AI_SECRET_KEY` | new value — `openssl rand -hex 32` |
| `LEGAL_AI_WEB_WORKERS` | `1` (RAM) |
| `POSTGRES_PASSWORD` / `MINIO_ROOT_PASSWORD` | new strong values |
| `LEGAL_AI_DEV_USERS` | the one admin account |
| `LEGAL_AI_CORS_ORIGINS` | `https://yawoto.neobytech.net` (web origin; mobile needs no CORS) |
| `LEGAL_AI_FRONTEND_URL` | `https://yawoto.neobytech.net` |
| `AFRIKIA_API_KEY` | same provider as today (reachable from the VM) |
| `NEXT_PUBLIC_API_URL` | leave empty (same-origin proxy through Cloudflare) |
| `API_PROXY_TARGET` | unused on the server (no frontend service) |
| `PYTHON_IMAGE` / `PIP_INDEX_URL` | irrelevant — no build on the VM |

`LEGAL_AI_DATA_DIR=/app/data` stays: `./data` is bind-mounted into the api
container, so the rsynced documents land in the right place.

## Data migration (one-time, from the Mac)

The 11 GB of PDFs do not travel via git (excluded by `.gitignore`), and the
vector index lives in docker volumes — both are moved directly:

```bash
# On the Mac (stops the stack briefly, restarts it after):
scripts/export_stack_data.sh

# Copy to the VM:
scp migration/*.tgz user@vm:~/yawoto-migration/
rsync -avz --progress --exclude 'tmp/' data/ user@vm:~/legal-ai-burkina/data/

# On the VM, before first start (stack stopped):
cd legal-ai-burkina
scripts/import_stack_data.sh ~/yawoto-migration
```

This restores the Milvus index, MinIO blobs, etcd state and the Postgres user
DB — no re-ingestion needed on the VM. Redis is deliberately not migrated
(cache only). If you ever prefer re-ingesting from scratch instead:
`docker compose -f docker-compose.yml -f docker-compose.server.yml exec api python -m backend.ingestion.pipeline /app/data/legal_docs`.

## Web on Cloudflare (from any machine with the repo)

The frontend deploys to Cloudflare Workers via the OpenNext adapter —
route handlers (chat SSE, search proxy) and the `/backend-api` rewrite keep
working.

```bash
cd frontend
npx wrangler login                 # once, opens the browser
API_PROXY_TARGET=https://api.yawoto.neobytech.net npm run deploy:cloudflare
```

`API_PROXY_TARGET` at build time bakes the rewrite destination; the same value
is set for the runtime route handlers via `vars` in `wrangler.jsonc` (edit the
file if the API hostname ever changes).

Then attach the custom domain in the Cloudflare dashboard:
**Workers & Pages → yawoto-web → Custom domains → add `yawoto.neobytech.net`**
(remove the old tunnel route for that hostname first).

## Routine updates

```bash
# Backend: CI builds ghcr.io/sanaaloute/legal-ai-burkina-api:latest on every
# push to minimac/main that touches backend code. On the VM:
git pull
docker compose -f docker-compose.yml -f docker-compose.server.yml pull
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d

# Web: from a checkout of the repo
cd frontend && npm run deploy:cloudflare
```

## Mobile

Nothing mobile-related runs on the server. Point the apps at
`https://api.yawoto.neobytech.net` (mobile already calls the API directly,
bypassing the web proxy) and build new EAS releases.

## Notes / caveats

- **Never orange-cloud `api.*`** until the dossier analysis endpoints stream
  or become job+poll (524 risk over ~100 s).
- The VM footprint with the shipped caps is ≈ 3–4 GB (api 3 g cap is a ceiling,
  not a reservation); watch `docker stats` during the first days.
- GHCR image is private by default: the VM needs the `docker login ghcr.io`
  from step 2, or mark the package public under the repo's Packages settings.
- The old Mac-mini tunnel (`~/.cloudflared/config-yawoto.yml`) must be
  disabled once the Cloudflare custom domain is live, so both don't serve the
  same hostname.
