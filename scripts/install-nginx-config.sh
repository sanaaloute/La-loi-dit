#!/usr/bin/env bash
# Install/update the host nginx configuration for the Yawoto frontend.
#
# Usage:
#   sudo bash scripts/install-nginx-config.sh [DOMAIN] [CERT_PATH] [KEY_PATH]
#
# Defaults:
#   DOMAIN=yawoto.neobytech.net
#   CERT_PATH=/etc/letsencrypt/live/<DOMAIN>/fullchain.pem
#   KEY_PATH=/etc/letsencrypt/live/<DOMAIN>/privkey.pem
#
# If the TLS certificate files do not exist, the script stops nginx, obtains a
# certificate with certbot standalone on port 80, then installs the vhost and
# restarts nginx.
#
# The script is idempotent and reloads/restarts nginx only when the config is
# valid.
set -euo pipefail

cd "$(dirname "$0")/.."

DOMAIN="${1:-yawoto.neobytech.net}"
CERT_PATH="${2:-${CERT_PATH:-/etc/letsencrypt/live/$DOMAIN/fullchain.pem}}"
KEY_PATH="${3:-${KEY_PATH:-/etc/letsencrypt/live/$DOMAIN/privkey.pem}}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"

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

$SUDO mkdir -p "$DEST_DIR" "$ENABLED_DIR"

# ---------------------------------------------------------------------------
# 1. Ensure TLS certificates exist; obtain them with certbot if missing.
# ---------------------------------------------------------------------------
if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
    echo "TLS certificates not found at $CERT_PATH / $KEY_PATH"

    if ! command -v certbot >/dev/null 2>&1; then
        echo "ERROR: certbot is not installed. Install it or provide valid CERT_PATH/KEY_PATH." >&2
        exit 1
    fi

    echo "Obtaining certificate for $DOMAIN with certbot standalone..."
    $SUDO systemctl stop nginx || true

    CERTBOT_ARGS=(
        certbot certonly --standalone
        --non-interactive --agree-tos
        -d "$DOMAIN"
    )
    if [ -n "$CERTBOT_EMAIL" ]; then
        CERTBOT_ARGS+=(--email "$CERTBOT_EMAIL")
    else
        CERTBOT_ARGS+=(--register-unsafely-without-email)
    fi

    if ! $SUDO "${CERTBOT_ARGS[@]}"; then
        echo "ERROR: certbot failed to obtain a certificate for $DOMAIN" >&2
        $SUDO systemctl start nginx || true
        exit 1
    fi

    $SUDO systemctl start nginx || true
fi

# ---------------------------------------------------------------------------
# 2. Install the final HTTPS vhost.
# ---------------------------------------------------------------------------
echo "Installing nginx config for $DOMAIN"

sed -e "s|__DOMAIN__|$DOMAIN|g" \
    -e "s|__CERT_PATH__|$CERT_PATH|g" \
    -e "s|__KEY_PATH__|$KEY_PATH|g" \
    "$SRC" | $SUDO tee "$DEST" >/dev/null

$SUDO ln -sf "$DEST" "$LINK"

if $SUDO nginx -t; then
    $SUDO systemctl reload-or-restart nginx || {
        echo "ERROR: nginx config is valid but reload/restart failed" >&2
        exit 1
    }
    echo "Nginx config installed and reloaded for $DOMAIN"
else
    echo "ERROR: nginx config test failed — $DOMAIN config left in place but not reloaded" >&2
    exit 1
fi
