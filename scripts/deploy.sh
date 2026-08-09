#!/usr/bin/env bash
# First-time deploy / full re-deployment: tears down everything (containers,
# images, volumes — including the Milvus index and the Postgres user DB),
# rebuilds all images from scratch, starts the stack, and indexes the
# documents in ./data/legal_docs.
#
# Usage:  scripts/deploy.sh [-y|--yes]   (-y skips the confirmation prompt)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" != "-y" && "${1:-}" != "--yes" ]]; then
    echo "WARNING: this WIPES all containers, images and volumes (index, user DB),"
    echo "rebuilds everything from scratch and re-indexes ./data/legal_docs."
    read -r -p "Continue? [y/N] " answer
    [[ "$answer" =~ ^[yY]$ ]] || { echo "aborted"; exit 1; }
fi

echo "== 1/4  stopping and wiping containers + volumes =="
docker compose down -v --remove-orphans

echo "== 2/4  rebuilding images (no cache) =="
docker compose build --no-cache

echo "== 3/4  starting the stack =="
docker compose up -d

echo "== waiting for the API to be healthy =="
for i in $(seq 1 72); do
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    if [[ "$i" == "72" ]]; then
        echo "ERROR: API did not become healthy in time" >&2
        exit 1
    fi
    sleep 5
done

echo "== 4/4  indexing documents (data/legal_docs) =="
docker compose exec api python -m backend.ingestion.pipeline /app/data/legal_docs

echo "deploy done."
