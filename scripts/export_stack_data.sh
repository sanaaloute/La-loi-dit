#!/usr/bin/env bash
# Export the built stack data (Milvus index, MinIO blobs, etcd state, Postgres
# user DB) from the LOCAL docker volumes into ./migration/, ready to scp/rsync
# to the cloud server. Run scripts/import_stack_data.sh there to restore.
#
# This stops the local stack for a consistent copy, then restarts it.
# Usage:  scripts/export_stack_data.sh
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="migration"
mkdir -p "$OUT"

VOLUMES=(
    milvus-data    # the vector index itself
    minio-data     # Milvus blob store (required by Milvus)
    etcd-data      # Milvus metadata
    postgres-data  # user accounts, dossiers, bookmarks, usage
    # redis-data intentionally skipped: cache/queues only, rebuilds itself.
)

echo "== stopping the stack for a consistent export =="
docker compose stop

for vol in "${VOLUMES[@]}"; do
    name="legal-ai-burkina_${vol}"
    echo "== exporting $name =="
    docker run --rm -v "${name}:/src:ro" -v "$PWD/$OUT:/dst" alpine \
        tar czf "/dst/${vol}.tgz" -C /src .
done

echo "== restarting the stack =="
docker compose start

cat <<EOF

Export complete in ./$OUT. Next steps:

  # 1. Copy the volumes archive to the server
  scp $OUT/*.tgz user@server:~/yawoto-migration/

  # 2. Copy the data directory (11 GB — rsync is resumable)
  rsync -avz --progress --exclude 'tmp/' data/ user@server:~/legal-ai-burkina/data/

  # 3. On the server, from the repo root:
  #    scripts/import_stack_data.sh ~/yawoto-migration
EOF
