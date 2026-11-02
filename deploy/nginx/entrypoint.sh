#!/bin/sh
set -eu
/select-cert.sh
# # Every 6 hours pick up the certificate renewed by certbot.
( while :; do sleep 6h; /select-cert.sh && nginx -s reload || true; done ) &
exec /docker-entrypoint.sh nginx -g 'daemon off;'
