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

# Normalise the common shorthand so argparse receives the canonical flag.
ARGS=("$@")
if [[ "${#ARGS[@]}" -gt 0 && "${ARGS[0]}" == "--full" ]]; then
    ARGS[0]="--full-reindex"
fi

# Wait for Milvus to actually accept connections from the api container.
# docker-compose only guarantees the container is started, not that the gRPC
# port is open; a premature reindex fails with "Connection refused".
echo "Checking Milvus readiness..."
docker compose exec api python - <<'PY'
import os, socket, sys, time
import urllib.request

host = os.getenv("LEGAL_AI_MILVUS_HOST", "milvus")
grpc_port = int(os.getenv("LEGAL_AI_MILVUS_PORT", "19530"))
# Milvus exposes the standalone health endpoint on port 9091.
health_url = f"http://{host}:9091/healthz"
attempts = 45
for i in range(attempts):
    try:
        # 1) TCP port 19530 is accepting connections.
        socket.create_connection((host, grpc_port), timeout=2).close()
        # 2) Milvus itself reports healthy on /healthz.
        with urllib.request.urlopen(health_url, timeout=2) as resp:
            if resp.status == 200:
                print(f"Milvus ready at {host}:{grpc_port}")
                sys.exit(0)
    except OSError:
        pass
    print(f"Waiting for Milvus at {host}:{grpc_port}... ({i + 1}/{attempts})")
    time.sleep(2)
print(f"ERROR: Milvus not reachable at {host}:{grpc_port} after {attempts * 2}s", file=sys.stderr)
sys.exit(1)
PY

docker compose exec api python -m backend.ingestion.pipeline /app/data/legal_docs "${ARGS[@]}"

echo "reindex done. Answers use the new index immediately (allow ~5 min for the retrieval cache)."
