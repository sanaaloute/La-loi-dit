#!/usr/bin/env bash
# Install/update the host nginx configuration for the Yawoto frontend.
#
# Usage:
#   sudo scripts/install-nginx-config.sh [DOMAIN] [CERT_PATH] [KEY_PATH]
#
# Defaults:
#   DOMAIN=yawoto.neobytech.net
#   CERT_PATH=/etc/letsencrypt/live/<DOMAIN>/fullchain.pem
#   KEY_PATH=/etc/letsencrypt/live/<DOMAIN>/privkey.pem
#
# The script is idempotent and reloads nginx only when the config is valid.
set -euo pipefail

cd "$(dirname "$0")/.."

DOMAIN="${1:-yawoto.neobytech.net}"
CERT_PATH="${2:-${CERT_PATH:-/etc/letsencrypt/live/$DOMAIN/fullchain.pem}}"
KEY_PATH="${3:-${KEY_PATH:-/etc/letsencrypt/live/$DOMAIN/privkey.pem}}"

SRC="docker/host-nginx/yawoto.neobytech.net.conf"
DEST_DIR="/etc/nginx/sites-available"
ENABLED_DIR="/etc/nginx/sites-enabled"
DEST="$DEST_DIR/$DOMAIN.conf"
LINK="$ENABLED_DIR/$DOMAIN.conf"

if [ ! -f "$SRC" ]; then
    echo "ERROR: nginx config template not found: $SRC" >&2
    exit 1
fi

if [ "$EUID" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    SUDO=""
fi

if ! command -v nginx >/dev/null 2>&1; then
    echo "ERROR: nginx is not installed on this host" >&2
    exit 1
fi

echo "Installing nginx config for $DOMAIN"
$SUDO mkdir -p "$DEST_DIR" "$ENABLED_DIR"

sed -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__CERT_PATH__|$CERT_PATH|g" \
    -e "s|__KEY_PATH__|$KEY_PATH|g" \
    "$SRC" | $SUDO tee "$DEST" >/dev/null

$SUDO ln -sf "$DEST" "$LINK"

if $SUDO nginx -t; then
    $SUDO systemctl reload nginx || $SUDO service nginx reload || {
        echo "ERROR: nginx config is valid but reload failed" >&2
        exit 1
    }
    echo "Nginx config installed and reloaded for $DOMAIN"
else
    echo "ERROR: nginx config test failed — $DOMAIN config left in place but not reloaded" >&2
    exit 1
fi
