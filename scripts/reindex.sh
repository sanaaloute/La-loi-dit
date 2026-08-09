#!/usr/bin/env bash
# Index update: after adding / editing / deleting documents in ./data/legal_docs,
# re-index inside the running stack. Incremental by content hash:
#   - new or edited documents are chunked, embedded and indexed
#   - unchanged documents are skipped
#   - documents deleted from the folder are removed from the index (GC)
#
# Usage:  scripts/reindex.sh [--full-reindex]
#         --full-reindex wipes the whole index first, then re-ingests everything.
set -euo pipefail
cd "$(dirname "$0")/.."

docker compose exec api python -m backend.ingestion.pipeline /app/data/legal_docs "$@"

echo "reindex done. Answers use the new index immediately (allow ~5 min for the retrieval cache)."
