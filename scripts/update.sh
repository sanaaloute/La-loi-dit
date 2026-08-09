#!/usr/bin/env bash
# Code update: pull the latest codebase, rebuild the images (cached) and
# restart the containers. The index and databases are NOT touched — if you
# also changed documents, run scripts/reindex.sh afterwards.
#
# Usage:  scripts/update.sh [--no-pull]   (--no-pull skips git pull)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" != "--no-pull" ]]; then
    echo "== 1/3  git pull =="
    git pull --ff-only
else
    echo "== 1/3  git pull skipped =="
fi

echo "== 2/3  rebuilding images =="
docker compose build

echo "== 3/3  restarting the stack =="
docker compose up -d

echo "update done. The document index was left untouched (run scripts/reindex.sh if documents changed)."
