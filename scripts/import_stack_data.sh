#!/usr/bin/env bash
# Restore the volumes exported by scripts/export_stack_data.sh into this
# server's docker volumes. Run BEFORE the first `docker compose up` (or with
# the stack stopped). Usage:  scripts/import_stack_data.sh /path/to/migration
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?usage: scripts/import_stack_data.sh /path/to/migration (dir with *.tgz)}"

if docker compose ps -q 2>/dev/null | grep -q .; then
    echo "ERROR: stop the stack first (docker compose stop) — restoring into live volumes corrupts data." >&2
    exit 1
fi

for tgz in "$SRC"/*.tgz; do
    vol="$(basename "$tgz" .tgz)"
    name="legal-ai-burkina_${vol}"
    echo "== restoring $name =="
    docker volume create "$name" >/dev/null
    docker run --rm -v "${name}:/dst" -v "$SRC:/src:ro" alpine \
        sh -c "rm -rf /dst/* /dst/..?* /dst/.[!.]* 2>/dev/null; tar xzf /src/$(basename "$tgz") -C /dst"
done

echo "Restore done. Start the stack with:"
echo "  docker compose -f docker-compose.yml -f docker-compose.server.yml up -d"
