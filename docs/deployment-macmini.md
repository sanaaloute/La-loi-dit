# Deployment: Mac Mini + Cloudflare Tunnel

Architecture: the full stack runs on a Mac Mini (32 GB RAM). Public access
goes through a dedicated Cloudflare Tunnel (`yawoto`) — no public inbound
port, no relay VM, no Tailscale dependency for traffic. The zone
`neobytech.net` is hosted on Cloudflare DNS.

```
public → https://yawoto.neobytech.net (Cloudflare DNS + proxy)
       → cloudflared tunnel "yawoto" on the Mac (outbound only)
       → 127.0.0.1:3100 → docker compose frontend → api / postgres / …
```

> The tunnel is **dedicated** to this project: its own config
> (`~/.cloudflared/config-yawoto.yml`), credentials and LaunchAgent
> (`com.yawoto.cloudflared`). Other projects on the same Mac (e.g.
> ai-website) run their own tunnels — do not share ingress configs.

## Phase 0 — Architecture and build mirrors

`scripts/download_wheels.sh` now detects the host architecture automatically:
Apple Silicon downloads `aarch64` wheels; Intel hosts download `x86_64`.
Run it before the first build and whenever `requirements.txt` changes:

```bash
bash scripts/download_wheels.sh
```

The backend uses `uv pip install`: local wheels are tried offline first, and an
incomplete or wrong-architecture wheelhouse falls back to the configured
package mirror instead of failing. China-friendly defaults are provided for
`PYTHON_IMAGE` / `NODE_IMAGE` (DaoCloud), `PIP_INDEX_URL` (Aliyun), and
`NPM_REGISTRY` (npmmirror); all can be overridden in `.env`.

The remaining risk is **paddlepaddle** (OCR): verify that a manylinux aarch64
wheel exists for your target version. Other services (postgres, redis,
milvus, etcd, minio) ship arm64 images.

## Phase 1 — Mac Mini base setup

```bash
# Docker runtime (OrbStack is lighter than Docker Desktop):
brew install orbstack && open -a OrbStack

# Ollama natively (Metal acceleration), with the fixed chat role models and
# the separate local embedding model:
brew install ollama && brew services start ollama
ollama pull nemotron-3-nano:4b
ollama pull gemma4:12b
ollama pull nomic-embed-text

# cloudflared:
brew install cloudflared
cloudflared login          # opens the browser, writes ~/.cloudflared/cert.pem
```

Tailscale is **optional** and no longer part of the serving path — keep it
only if you want remote SSH/admin access to the Mac.

## Phase 2 — The stack on the Mac Mini

```bash
git clone <your-repo> yawoto && cd yawoto
cp .env.example .env
```

Verify the local AI settings in `.env` (the template already uses these values):

```ini
LEGAL_AI_WEB_WORKERS=3                                       # 32 GB: fine
LEGAL_AI_LLM_PROVIDER=ollama
LEGAL_AI_LLM_API_BASE=http://host.docker.internal:11434
LEGAL_AI_LLM_API_KEY=
LEGAL_AI_EMBEDDING_MODEL=ollama/nomic-embed-text
LEGAL_AI_EMBEDDING_DIMENSION=768
LEGAL_AI_EMBEDDING_API_BASE=http://host.docker.internal:11434
LEGAL_AI_EMBEDDING_API_KEY=
LEGAL_AI_STT_PROVIDER=faster-whisper
LEGAL_AI_OLLAMA_WARMUP_ON_STARTUP=true
API_PROXY_TARGET=http://api:8000
# Standalone Milvus server (compose services etcd+minio+milvus), shared by
# every api worker — do NOT point this at the embedded Lite file:
LEGAL_AI_MILVUS_ENABLED=true
LEGAL_AI_MILVUS_HOST=milvus
LEGAL_AI_MILVUS_PORT=19530
LEGAL_AI_MILVUS_URI=            # empty -> http://milvus:19530
# Also replace the POSTGRES_* and application secret placeholders.
```

Nemotron 3 Nano 4B handles language/query routing, planning, claim refinement,
ingestion classification, and optional LLM rerank scoring. Gemma 4 12B handles
reasoning/reflection, direct/general/grounded synthesis, drafting enrichment,
and memory summaries. Users cannot select models or override these roles through
the environment; there is no external chat failover. Daily quotas and anti-abuse
rate limits remain active. Embeddings, STT, OCR, semantic guardrails, and
cross-encoder reranking remain separate capabilities.

The frontend binds **127.0.0.1:3100** (see `docker-compose.yml`) — the
tunnel reaches it on localhost, and nothing is exposed on the LAN. Port 3100
rather than 3000 because another project's frontend already owns
localhost:3000 on this Mac; pick any free port and keep the tunnel config in
sync.

Then:

```bash
docker compose build
docker compose up -d
docker compose ps                    # all healthy
curl http://127.0.0.1:3100           # answers locally

# Ingest the corpus (no Milvus server readiness check on this branch):
bash scripts/reindex.sh
```

## Phase 3 — Cloudflare Tunnel

Prerequisite: the zone (`neobytech.net`) is added to your Cloudflare account
and its nameservers are set at the registrar (Spaceship → Domain →
Nameservers → the two `*.ns.cloudflare.com` assigned by Cloudflare).

```bash
# Dedicated tunnel (credentials land in ~/.cloudflared/<id>.json):
cloudflared tunnel create yawoto

# DNS route — creates the proxied CNAME yawoto.neobytech.net → tunnel:
cloudflared tunnel route dns --overwrite-dns yawoto yawoto.neobytech.net
```

> If the CLI cannot see the zone yet (fresh zone still "pending"), create
> the record by hand instead: zone → DNS → Records → CNAME `yawoto` →
> `<tunnel-id>.cfargotunnel.com`, proxy enabled. Same result.
> Never let the record fall into another zone: a suffix-matching mistake
> creates hostnames like `yawoto.neobytech.net.<other-zone>`.

Config `~/.cloudflared/config-yawoto.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /Users/<you>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: yawoto.neobytech.net
    service: http://localhost:3100
  - service: http_status:404
```

The frontend proxies `/backend-api/*` to the api container, so the frontend
port is the **only** service to expose. SSE chat streaming works through the
tunnel with no extra settings.

### Public API endpoint for mobile apps

Native apps must NOT go through the frontend's `/backend-api` rewrite (it
buffers SSE and adds a hop). Expose the API container directly on its own
hostname — it already binds `127.0.0.1:8000` on the host.

> The hostname must be a **first-level** subdomain (`api-yawoto.neobytech.net`,
> NOT `api.yawoto.neobytech.net`): Cloudflare's free Universal SSL covers
> `*.neobytech.net` only, so a second-level name gets no certificate and TLS
> fails at the edge. Create the DNS record in the **dashboard** (zone
> `neobytech.net` → DNS → Records → CNAME, name `api-yawoto`, target
> `<tunnel-id>.cfargotunnel.com`, proxy enabled) — `cloudflared tunnel route
> dns` from this Mac writes into the wrong zone (the local cert.pem cannot
> see `neobytech.net` and suffix-mangles the hostname).

The matching ingress rule lives in `~/.cloudflared/config-yawoto.yml`
**before** the frontend rule (order matters):

```yaml
ingress:
  - hostname: api-yawoto.neobytech.net
    service: http://localhost:8000
  - hostname: yawoto.neobytech.net
    service: http://localhost:3100
  - service: http_status:404
```

then `launchctl kickstart -k gui/$(id -u)/com.yawoto.cloudflared`. Verify SSE
arrives unbuffered (you should see frames trickle in, then `: hb` heartbeats
every ~10 s):

```bash
TOKEN=$(curl -s https://api-yawoto.neobytech.net/api/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"username":"…","password":"…"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -N -H "Authorization: Bearer $TOKEN" -H "X-Device-Id: smoke-test" \
  "https://api-yawoto.neobytech.net/api/v1/chat/stream?query=Bonjour&language=fr"
```

Mobile clients must send `X-Device-Id` on every request (see
[api.md](api.md)#native-clients-x-device-id-header) and use the SSE endpoint
for chat — Cloudflare kills responses with >100 s to first byte (error 524),
which rules out the non-streaming `POST /chat` for long runs.

LaunchAgent `~/Library/LaunchAgents/com.yawoto.cloudflared.plist` (model it
on any existing cloudflared plist; add `--config
/Users/<you>/.cloudflared/config-yawoto.yml` before `run`), then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.yawoto.cloudflared.plist
# logs: /tmp/cloudflared-yawoto-{out,err}.log — expect 4 "Registered tunnel connection"
```

The apex `neobytech.net` stays a plain DNS record (company site) — only the
`yawoto` hostname routes to this app.

## Phase 4 — Verify and harden

- `https://yawoto.neobytech.net`: the login gate shows; ask a legal
  question (SSE stream); upload a document from the admin panel.
- `curl https://yawoto.neobytech.net/backend-api/health` → `{"status":"ok"}`.
- After each api restart, warm the exact answer cache with the canonical FAQ
  so the first asker of each common question doesn't pay full pipeline time:
  `WARMUP_USER=<login> WARMUP_PASS=<password> bash scripts/warm_cache.sh`
  (hits the container directly, safe to cron).
- New laws: a weekly crawl pulls newly promulgated laws from
  assembleenationale.bf and ingests them (idempotent; known law numbers are
  skipped). Cron example (Mondays 06:30):
  `30 6 * * 1 cd /path/to/La-loi-dit && .venv/bin/python scripts/crawl_assemblee.py --pages 2 --ingest >> data/tmp/crawl.log 2>&1`
  The freshness loop (`LEGAL_AI_FRESHNESS_CHECK_ENABLED=true`) detects changes
  on official sources daily and pushes "Nouveautés juridiques" alerts to
  registered mobile devices (`LEGAL_AI_PUSH_NOTIFICATIONS_ENABLED=true`).
  **Android production push needs FCM**: upload a Firebase service-account key
  to the EAS project (Expo dashboard → Credentials → Android → FCM) — without
  it, push works in Expo Go only.
- Mac Mini: System Settings → Energy → prevent sleep + start up after power
  failure; OrbStack, Ollama (`brew services`) and both LaunchAgents start at
  login.
- Backups: nightly `pg_dump` + copy of `data/` to an external disk or offsite
  storage (there is no longer an EC2 to rsync to).

## Caveats

- Cloudflare free plan caps request bodies at **100 MB** — matches the
  backend's admin upload limit (100 MB), fine as-is.
- Cloudflare kills requests with **>100 s to first byte** (error 524). The
  frontend uses the SSE endpoint (`/api/v1/chat/stream`) whose heartbeats
  keep the stream open, so long agent runs are fine; avoid adding
  non-streaming endpoints that answer only at the end.
- Your home/office **uplink** is the user-facing bandwidth — fine for a
  legal tool's traffic profile.
- The Mac is production hardware: UPS recommended, and mind confidentiality
  obligations for legal data stored on premises.
- `docker/host-nginx/yawoto.neobytech.net.conf` is **legacy** — it was the
  EC2 relay's nginx config (public → EC2 → Tailscale → Mac) and is kept for
  reference only; the tunnel replaced that whole path.
