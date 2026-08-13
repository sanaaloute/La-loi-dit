#!/bin/sh
# Simple wait-for startup: give dependencies a moment, then run the command.
# The app itself degrades gracefully (offline fallbacks), so this is only a
# convenience to avoid noisy connection errors during stack boot.
set -e

wait_for() {
    host="$1"; port="$2"; name="$3"; tries=30
    while [ $tries -gt 0 ]; do
        if (echo > "/dev/tcp/$host/$port") >/dev/null 2>&1; then
            echo "[entrypoint] $name is up ($host:$port)"
            return 0
        fi
        tries=$((tries - 1))
        sleep 1
    done
    echo "[entrypoint] WARNING: $name not reachable at $host:$port after 30s; continuing anyway"
    return 0
}

if [ "${LEGAL_AI_WAIT_FOR:-}" = "1" ]; then
    case "${LEGAL_AI_DATABASE_URL:-}" in *postgres*) wait_for postgres 5432 postgres ;; esac
    if [ "${LEGAL_AI_REDIS_ENABLED:-}" = "true" ]; then wait_for redis 6379 redis; fi
    if [ "${LEGAL_AI_MILVUS_ENABLED:-}" = "true" ]; then wait_for milvus 19530 milvus; fi
fi

exec "$@"
