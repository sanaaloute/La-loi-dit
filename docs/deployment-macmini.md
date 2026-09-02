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

## Phase 0 — arm64 wheels (critical)

The image build installs from `docker/wheels` with `--no-index`, and
`scripts/download_wheels.sh` fetches **x86_64** wheels only. On the Mac
(arm64), re-fetch for aarch64 before building:

```bash
python -m pip download -r requirements.txt -d docker/wheels \
  --only-binary=:all: --implementation cp --abi cp312 --abi abi3 --abi none \
  --platform manylinux_2_17_aarch64 --platform manylinux2014_aarch64 \
  --python-version 3.12
```

The one risk is **paddlepaddle** (OCR): if no manylinux aarch64 wheel is
found, the build cannot succeed — check before going further. All other
services (postgres, redis, milvus, etcd, minio) ship arm64 images.

## Phase 1 — Mac Mini base setup

```bash
# Docker runtime (OrbStack is lighter than Docker Desktop):
brew install orbstack && open -a OrbStack

# Ollama natively (Metal acceleration):
brew install ollama && brew services start ollama
ollama pull qwen3-embedding:latest

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

`.env` adjustments for the Mac:

```ini
LEGAL_AI_WEB_WORKERS=3                                       # 32 GB: fine
LEGAL_AI_EMBEDDING_API_BASE=http://host.docker.internal:11434  # Mac-native Ollama
API_PROXY_TARGET=http://api:8000
# Standalone Milvus server (compose services etcd+minio+milvus), shared by
# every api worker — do NOT point this at the embedded Lite file:
LEGAL_AI_MILVUS_ENABLED=true
LEGAL_AI_MILVUS_HOST=milvus
LEGAL_AI_MILVUS_PORT=19530
LEGAL_AI_MILVUS_URI=            # empty -> http://milvus:19530
# POSTGRES_* secrets, STT provider, etc.
```

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
