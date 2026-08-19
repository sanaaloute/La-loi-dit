#!/usr/bin/env bash
# Index update: after adding / editing / deleting documents in ./data/legal_docs,
# re-index into the stack's index. Incremental by content hash:
#   - new or edited documents are chunked, embedded and indexed
#   - unchanged documents are skipped
#   - documents deleted from the folder are removed from the index (GC)
#
# The vector store is the standalone Milvus server (compose service `milvus`),
# shared by every process — the api can STAY UP during reindexing. (The old
# embedded Milvus Lite needed an api stop because of its file lock.)
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
# ${ARGS[@]+...} guard: on bash 3.2 (stock macOS bash) expanding an empty
# array under `set -u` aborts with "unbound variable".
docker compose run --rm -T api python -m backend.ingestion.pipeline /app/data/legal_docs ${ARGS[@]+"${ARGS[@]}"}

echo "reindex done. Answers use the new index immediately (allow ~5 min for the retrieval cache)."
