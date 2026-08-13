#!/usr/bin/env bash
# Initialize the host ./data directory so the Docker 'app' user can write to it.
# Run this before 'docker compose up' on Linux/macOS hosts. It is idempotent
# and safe to run multiple times.
#
# Usage:
#   scripts/init-data-dir.sh [DATA_DIR]
#   DATA_DIR defaults to ./data

set -euo pipefail

DATA_DIR="${1:-./data}"

mkdir -p "$DATA_DIR/legal_docs" "$DATA_DIR/tmp"

# Try to set ownership to the container's 'app' user so the numeric UID/GID
# match. This only works once the backend image exists. If the image is not yet
# built or sudo is unavailable, fall back to world-writable permissions.
APP_UID_GID=""
if command -v docker >/dev/null 2>&1; then
    APP_UID_GID=$(docker run --rm --entrypoint sh legal-ai-burkina:latest -c 'echo "$(id -u app):$(id -g app)"' 2>/dev/null || true)
fi

if [ -n "$APP_UID_GID" ] && command -v sudo >/dev/null 2>&1; then
    echo "[init-data-dir] Setting $DATA_DIR ownership to $APP_UID_GID"
    if sudo chown -R "$APP_UID_GID" "$DATA_DIR"; then
        # Owner (container app) needs full access. Also keep the host user's
        # primary group writable so git operations on tracked data files still
        # work, and set the setgid bit on directories so new files inherit the
        # host group.
        HOST_GROUP=$(id -gn)
        sudo chgrp -R "$HOST_GROUP" "$DATA_DIR"
        sudo find "$DATA_DIR" -type d -exec chmod u+rwx,g+rwxs {} +
        sudo find "$DATA_DIR" -type f -exec chmod u+rw,g+rw {} +
        exit 0
    fi
fi

echo "[init-data-dir] WARNING: could not set container ownership; making $DATA_DIR writable for all users"
chmod -R 777 "$DATA_DIR"
