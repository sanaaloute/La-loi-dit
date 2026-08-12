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
# If the TLS certificate files do not exist, the script installs a temporary
# HTTP placeholder, stops nginx, and runs certbot standalone on port 80 to
# obtain the certificate. If certbot fails, the HTTP placeholder is reinstalled so
# nginx remains up while you fix DNS/security groups.
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
WEBROOT="/var/www/certbot"

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

$SUDO mkdir -p "$DEST_DIR" "$ENABLED_DIR" "$WEBROOT"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

install_http_placeholder() {
    $SUDO tee "$DEST" >/dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    root $WEBROOT;

    location /.well-known/acme-challenge/ {
        try_files \$uri =404;
    }

    location / {
        add_header Content-Type text/plain;
        return 200 "Yawoto placeholder page - certificate pending for $DOMAIN\n";
    }
}
EOF
    $SUDO ln -sf "$DEST" "$LINK"
    if $SUDO nginx -t; then
        $SUDO systemctl reload-or-restart nginx || {
            echo "ERROR: nginx HTTP placeholder is valid but reload/restart failed" >&2
            return 1
        }
    else
        echo "ERROR: nginx HTTP placeholder config test failed" >&2
        return 1
    fi
}

install_final_config() {
    sed -e "s|__DOMAIN__|$DOMAIN|g" \
        -e "s|__CERT_PATH__|$CERT_PATH|g" \
        -e "s|__KEY_PATH__|$KEY_PATH|g" \
        "$SRC" | $SUDO tee "$DEST" >/dev/null

    $SUDO ln -sf "$DEST" "$LINK"

    if $SUDO nginx -t; then
        $SUDO systemctl reload-or-restart nginx || {
            echo "ERROR: nginx final config is valid but reload/restart failed" >&2
            return 1
        }
        echo "Nginx config installed and reloaded for $DOMAIN"
    else
        echo "ERROR: nginx final config test failed" >&2
        return 1
    fi
}

# ---------------------------------------------------------------------------
# 1. If certificates exist, just install the final HTTPS config.
# ---------------------------------------------------------------------------
if [ -f "$CERT_PATH" ] && [ -f "$KEY_PATH" ]; then
    echo "Installing nginx config for $DOMAIN"
    install_final_config
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. No certificate yet: install an HTTP placeholder, then run certbot.
# ---------------------------------------------------------------------------
echo "TLS certificates not found at $CERT_PATH / $KEY_PATH"

if ! command -v certbot >/dev/null 2>&1; then
    echo "ERROR: certbot is not installed. Install it or provide valid CERT_PATH/KEY_PATH." >&2
    exit 1
fi

echo "Installing temporary HTTP placeholder so nginx stays valid..."
install_http_placeholder

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
    echo "Re-installing HTTP placeholder so nginx can start..."
    install_http_placeholder || true
    echo "Common causes: DNS A/AAAA record not pointing to this server, or port 80 blocked." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Certificate obtained: install the final HTTPS vhost.
# ---------------------------------------------------------------------------
echo "Certificate obtained. Installing final nginx config for $DOMAIN"
install_final_config
