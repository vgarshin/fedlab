#!/bin/sh
# Pick the nginx certificate: real Let's Encrypt if issued, else a temporary self-signed one so nginx can start and answer ACME.
set -eu
: "${SERVER_NAME:?SERVER_NAME is not set}"
LIVE="/etc/letsencrypt/live/${SERVER_NAME}"
SELF=/etc/nginx/certs/selfsigned
mkdir -p "$SELF"
if [ ! -f "$SELF/fullchain.pem" ]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=${SERVER_NAME}" -addext "subjectAltName=DNS:${SERVER_NAME}" \
        -keyout "$SELF/privkey.pem" -out "$SELF/fullchain.pem" 2>/dev/null
fi
if [ -f "$LIVE/fullchain.pem" ] && [ -f "$LIVE/privkey.pem" ]; then
    ln -sfn "$LIVE" /etc/nginx/certs/current
    echo "сертификат: Let's Encrypt (${SERVER_NAME})"
else
    ln -sfn "$SELF" /etc/nginx/certs/current
    echo "сертификат: временный самоподписанный (${SERVER_NAME})"
fi
