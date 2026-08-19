# Deployment: Mac Mini + EC2 public relay

Architecture: the full stack runs on a Mac Mini (32 GB RAM) reachable only
through Tailscale; a small EC2 instance is a dumb public nginx relay. The
public domain (Spaceship DNS) points at the EC2's Elastic IP.

```
public → https://your-domain (Spaceship → EC2 Elastic IP) → nginx on EC2
       → Tailscale tunnel → Mac Mini (100.x.y.z:3000) → docker compose stack
```

> The Tailscale IP (`100.64.0.0/10`) is NOT publicly routable — never point
> public DNS at it. The EC2 relay is what makes the platform public.

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
services (postgres, redis, milvus, etcd, minio, temporal, grafana,
prometheus) ship arm64 images.

## Phase 1 — Mac Mini base setup

```bash
# Docker runtime (OrbStack is lighter than Docker Desktop):
brew install orbstack && open -a OrbStack

# Ollama natively (Metal acceleration, much faster than EC2 CPU):
brew install ollama && brew services start ollama
ollama pull qwen3-embedding:latest

# Tailscale:
brew install --cask tailscale   # sign in from the menu bar icon
tailscale ip -4                  # note the 100.x.y.z — this is MAC_TS_IP
```

In the Tailscale admin console: **disable key expiry** for the Mac
(Machines → ⋯ → Disable key expiry), or the tunnel dies silently after
90 days.

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
MAC_TS_IP=100.x.y.z                                          # from Phase 1
# Standalone Milvus server (compose services etcd+minio+milvus), shared by
# every api worker — do NOT point this at the embedded Lite file:
LEGAL_AI_MILVUS_ENABLED=true
LEGAL_AI_MILVUS_HOST=milvus
LEGAL_AI_MILVUS_PORT=19530
LEGAL_AI_MILVUS_URI=            # empty -> http://milvus:19530
# POSTGRES_* secrets, STT provider, etc. as on the EC2
```

On the `minimac` branch the main `docker-compose.yml` IS the minimal stack
(`api`, `frontend`, `postgres`, `redis`, `etcd`, `minio`, `milvus`). The cut
services and why they are safe to drop:

- **temporal + temporal-ui + celery-worker** — dormant subsystems: nothing in
  the API/request path imported them (the code is removed on this branch);
- **prometheus + grafana** — optional observability; `/metrics` keeps working;
- **ollama-relay** — a WSL-only workaround; Ollama runs natively on the Mac.

> Note: the stack previously ran embedded Milvus Lite
> (`LEGAL_AI_MILVUS_URI=/app/data/milvus_lite.db`). Lite is single-process:
> with more than one api worker, the others silently fell back to an empty
> in-memory store — which is why the standalone server stack is back.

Then:

```bash
# Copy data (OCR/STT models, legal docs) from the EC2:
rsync -avz ec2:/path/to/yawoto/data/ ./data/

docker compose build
docker compose up -d
docker compose ps                    # all healthy
curl http://100.x.y.z:3000           # answers over Tailscale

# Ingest the corpus (no Milvus server readiness check on this branch):
bash scripts/reindex.sh
```

## Phase 3 — EC2 becomes a pure relay

```bash
# 1. Assign an Elastic IP (AWS console) so the address survives restarts.

# 2. Spaceship: Domain → DNS records → A record:
#    Host: @ (and/or www)   Value: <EC2 Elastic IP>   TTL: 300

# 3. Stop the heavyweight stack on the EC2 — only nginx stays:
cd /path/to/yawoto && docker compose down

# 4. Tailscale + nginx + certbot:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
ping 100.x.y.z                       # MAC_TS_IP must answer
sudo apt install -y nginx certbot python3-certbot-nginx
```

Disable key expiry for the EC2 in the Tailscale console too.

Install `docker/host-nginx/yawoto.neobytech.net.conf` with the proxy target
pointing at the Mac over Tailscale:

```bash
sudo sed 's/__DOMAIN__/your-domain.com/g; s|http://127.0.0.1:3000|http://100.x.y.z:3000|g' \
  docker/host-nginx/yawoto.neobytech.net.conf | sudo tee /etc/nginx/sites-available/yawoto
sudo ln -s /etc/nginx/sites-available/yawoto /etc/nginx/sites-enabled/
sudo certbot --nginx -d your-domain.com   # provisions TLS, fills cert paths
sudo nginx -t && sudo systemctl reload nginx
```

EC2 security group: inbound **80/tcp and 443/tcp** only (plus SSH). Milvus,
Postgres and friends now live exclusively on the tailnet.

## Phase 4 — Verify and harden

- `https://your-domain.com` publicly: the login/register gate shows; ask a
  legal question; upload a document from the admin panel.
- Tailscale ACLs (admin console): allow the EC2 node to reach the Mac only
  on port 3000 — a compromised relay still can't touch the databases.
- Mac Mini: System Settings → Energy → prevent sleep + start up after power
  failure; OrbStack, Ollama (`brew services`) and Tailscale launch at login.
- Backups: nightly `pg_dump` + rsync of `data/` to the EC2 (it has the disk
  and does almost nothing else now).

## Caveats

- Your home/office **uplink** is now the user-facing bandwidth — fine for a
  legal tool's traffic profile, but keep it in mind for large PDF uploads.
- The Mac is production hardware: UPS recommended, and mind confidentiality
  obligations for legal data stored on premises.
