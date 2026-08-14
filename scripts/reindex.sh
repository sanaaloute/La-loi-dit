#!/usr/bin/env bash
# Index update: after adding / editing / deleting documents in ./data/legal_docs,
# re-index inside the running stack. Incremental by content hash:
#   - new or edited documents are chunked, embedded and indexed
#   - unchanged documents are skipped
#   - documents deleted from the folder are removed from the index (GC)
#
# The vector store is embedded Milvus Lite inside the api process (minimac
# branch), so there is no Milvus server readiness check.
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

# -T keeps this non-interactive so it works from cron/SSH without a tty.
docker compose exec -T api python -m backend.ingestion.pipeline /app/data/legal_docs "${ARGS[@]}"

echo "reindex done. Answers use the new index immediately (allow ~5 min for the retrieval cache)."
