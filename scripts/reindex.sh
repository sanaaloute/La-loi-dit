#!/usr/bin/env bash
# Index update: after adding / editing / deleting documents in ./data/legal_docs,
# re-index into the stack's index. Incremental by content hash:
#   - new or edited documents are chunked, embedded and indexed
#   - unchanged documents are skipped
#   - documents deleted from the folder are removed from the index (GC)
#
# The vector store is embedded Milvus Lite (minimac branch): it is a
# single-process engine guarded by a file lock, so a pipeline started via
# `docker compose exec` CANNOT open the database while the api container holds
# it (it silently falls back to a throwaway in-memory store). The api is
# therefore stopped for a one-off ingestion container, then restarted — a
# short, deliberate maintenance window.
#
# Usage:  scripts/reindex.sh [--full-reindex]
#         --full-reindex wipes the whole index first, then re-ingests everything.
set -euo pipefail
cd "$(dirname "$0")/.."

# Normalise the common shorthand so argparse receives the canonical flag.
ARGS=("$@")
if [[ "${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "--full" ]]; then
    ARGS[0]="--full-reindex"
fi

echo "stopping api (Milvus Lite lock must be free; short maintenance window)"
docker compose stop api

# -T keeps this non-interactive so it works from cron/SSH without a tty.
# ${ARGS[@]+...} guard: on bash 3.2 (stock macOS bash) expanding an empty
# array under `set -u` aborts with "unbound variable".
docker compose run --rm -T api python -m backend.ingestion.pipeline /app/data/legal_docs ${ARGS[@]+"${ARGS[@]}"}

docker compose up -d api

echo "reindex done. Answers use the new index immediately (allow ~5 min for the retrieval cache)."
