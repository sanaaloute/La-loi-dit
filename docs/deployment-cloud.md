# Cloud deployment (production VM + Cloudflare web)

Target layout:

```
                       ┌──────────────────────────────┐
 Browser  ───────────▶ │ Cloudflare Workers (OpenNext)│  yawoto.neobytech.net
 (web app)             │  Next.js frontend            │
                       └─────────────┬────────────────┘
                                     │ /backend-api/* proxied at the edge
                                     ▼
 Mobile app ──────────▶ api-yawoto.neobytech.net  (proxied, Universal SSL)
                        Cloudflare Tunnel "yawoto-vm" (remotely managed)
                                     ▼
                       ┌──────────────────────────────┐
                       │ Cloud VM (Docker Compose)     │
                       │  cloudflared · api (GHCR)     │
                       │  postgres · redis · etcd      │
                       │  minio · milvus               │
                       └──────────────────────────────┘
```

Exposure is a **remotely-managed Cloudflare Tunnel**: cloudflared runs as a
compose service, routes are configured in the Zero Trust dashboard, TLS is
terminated at the Cloudflare edge. No nginx, no certbot, no open inbound
ports on the VM — same pattern as the other neobytech.net services.

Hostname is `api-yawoto.neobytech.net` (dash). Cloudflare's Universal SSL
covers only one label deep (`*.neobytech.net`); a multi-level name like
`api.yawoto.neobytech.net` is **not** covered and would fail TLS at the edge.

Known trade-off: tunneled traffic is proxied, so Cloudflare's ~100 s
time-to-first-byte limit applies. The dossier analysis endpoints are
non-streaming and can run for several minutes (measured 77–250 s) — long
analyses may return 524 until those endpoints become async (job + poll) or
heartbeat-streamed. This constraint already existed with the previous
Mac-hosted tunnel setup; the fix is tracked as a follow-up.

## Prerequisites

- VM with Docker + Docker Compose **v2.24+** (`docker compose version`).
- A GitHub PAT with `read:packages` for `docker login ghcr.io`
  (unless the package is made public).
- Cloudflare Zero Trust access to create a remotely-managed tunnel.
- Repo cloned on the VM: `git clone -b minimac https://github.com/sanaaloute/La-loi-dit.git legal-ai-burkina && cd legal-ai-burkina`

## First-time server setup

```bash
# 1. Environment — copy and edit (checklist below)
cp .env.example .env && $EDITOR .env

# 2. Pull images (docker.io goes through the daemon mirrors in
#    /etc/docker/daemon.json if the VM's network blocks Docker Hub)
docker login ghcr.io -u <github-user>    # PAT with read:packages
docker compose -f docker-compose.yml -f docker-compose.server.yml pull --ignore-pull-failures

# 3. MinIO only: upstream registries no longer serve minio/minio tags
#    (docker.io denies, quay repo is gone). Sideload the exact pinned image
#    from a machine that has it (e.g. the Mac):
ssh root@<vm> 'docker load' < /tmp/minio_RELEASE.2024-05-28.tar.gz
#    (produced on the Mac with:
#     docker save minio/minio:RELEASE.2024-05-28T17-19-04Z | gzip > /tmp/minio_RELEASE.2024-05-28.tar.gz)

# 4. Migrate data (from the Mac, once) — see next section

# 5. Start
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d
curl http://127.0.0.1:8000/health
```

### `.env` checklist for the server (differs from the Mac)

| Variable | Server value |
| --- | --- |
| `LEGAL_AI_ENV` | `production` (already the template default) |
| `LEGAL_AI_SECRET_KEY` | new value — `openssl rand -hex 32` |
| `LEGAL_AI_WEB_WORKERS` | `1` (RAM) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | **copy verbatim from the Mac `.env`** |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | **copy verbatim from the Mac `.env`** — volumes store credentials at first init; mismatches lock the api out and panic Milvus |
| `LEGAL_AI_LLM_API_BASE` / `LEGAL_AI_EMBEDDING_API_BASE` | `https://afrikia.neobytech.net/v1` (public gateway; the Mac's `localhost:4010` does not exist on the VM) |
| `LEGAL_AI_EMBEDDING_BATCH_SIZE` | `16` |
| `LEGAL_AI_DEV_USERS` | the one admin account |
| `LEGAL_AI_CORS_ORIGINS` | `https://yawoto.neobytech.net` (web origin; mobile needs no CORS) |
| `LEGAL_AI_FRONTEND_URL` | `https://yawoto.neobytech.net` |
| `AFRIKIA_API_KEY` | same provider key as on the Mac |
| `CLOUDFLARE_TUNNEL_TOKEN` | token of the `yawoto-vm` tunnel (dashboard shows it at creation; secret) |
| `NEXT_PUBLIC_API_URL` | leave empty (same-origin proxy through Cloudflare) |
| `API_PROXY_TARGET` | unused on the server (no frontend service) |
| `PYTHON_IMAGE` / `PIP_INDEX_URL` | irrelevant — no build on the VM |

`LEGAL_AI_DATA_DIR=/app/data` stays: `./data` is bind-mounted into the api
container, so the rsynced documents land in the right place.

To verify the credential lines match the Mac without printing secrets, run
on both machines and compare hashes (must be identical):

```bash
for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
  printf '%s ' "$v"; grep -E "^$v=" .env | tr -d '\r' | sed 's/[[:space:]]*$//' | shasum -a 256 | cut -c1-12
done
```

## Cloudflare Tunnel ("yawoto-vm")

The tunnel is **remotely managed**: configuration lives in the Zero Trust
dashboard (Networks → Tunnels), cloudflared on the VM only needs the token
(from `.env`, wired into the `cloudflared` compose service).

Public hostnames (dashboard → `yawoto-vm` → Public Hostnames):

| Hostname | Service | Purpose |
| --- | --- | --- |
| `api2-yawoto.neobytech.net` | `http://api:8000` | pre-cutover testing of the VM stack |
| `api-yawoto.neobytech.net` | `http://api:8000` | production API (moved from the old Mac tunnel at cutover) |

cloudflared reaches the api over the compose network — use `api:8000` as the
service URL, not `localhost:8000`.

## Data migration (one-time, from the Mac)

The PDFs do not travel via git (excluded by `.gitignore`), and the vector
index lives in docker volumes — both are moved directly:

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
API_PROXY_TARGET=https://api-yawoto.neobytech.net npm run deploy:cloudflare
```

`API_PROXY_TARGET` at build time bakes the rewrite destination; the same value
is set for the runtime route handlers via `vars` in `wrangler.jsonc` (edit the
file if the API hostname ever changes).

## Cutover checklist (Mac → VM)

1. VM stack healthy: `https://api2-yawoto.neobytech.net/health` answers.
2. Stop the Mac ingestion run and remove `data/.ingest-on-startup.lock`,
   then run the data migration above.
3. Dashboard, old Mac tunnel `yawoto`: **remove** the
   `api-yawoto.neobytech.net` route. Immediately add the same hostname to
   `yawoto-vm` → `http://api:8000`. (A hostname can live on only one tunnel
   at a time; the swap is near-atomic. Web worker and mobile apps follow
   automatically — both already point at this hostname.)
4. Dashboard, old tunnel `yawoto`: remove `yawoto.neobytech.net`, then
   Workers & Pages → `yawoto-web` → Custom domains → add `yawoto.neobytech.net`.
5. Delete the test hostname `api2-yawoto.neobytech.net`.
6. The old `yawoto` tunnel now has zero routes — delete it in the dashboard
   and stop its cloudflared on the Mac. Keep the Mac's `afrikia-llm` tunnel
   running: it is the LLM provider for the VM.
7. Optional: resume any pending document ingestion on the VM:
   `docker compose -f docker-compose.yml -f docker-compose.server.yml exec -d api sh -c 'python -m backend.ingestion.pipeline /app/data/legal_docs/to_ingest --no-gc >> /app/data/ingest_run.log 2>&1'`

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
`https://api-yawoto.neobytech.net` and build new EAS releases.

## Notes / caveats

- **524 risk** on non-streaming endpoints slower than ~100 s (dossier
  analysis): accepted for now, fix planned (async job + poll, or heartbeat
  streaming). Chat SSE is unaffected.
- The VM footprint with the shipped caps is ≈ 3–4 GB plus Milvus (3 g cap is a
  ceiling, not a reservation); the VM has no swap — watch `docker stats`
  during the first days.
- GHCR image is private by default: the VM needs the `docker login ghcr.io`
  from step 2, or mark the package public under the repo's Packages settings.
- The `cloudflared` image tag is pinned to one already cached on the VM
  (2025.8.1); bump it deliberately, not accidentally.
