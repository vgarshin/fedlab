#!/usr/bin/env bash
# First Let's Encrypt certificate. Run ON THE VM after DNS for SERVER_NAME points to it and 80/443 are open.
# STAGING=1 uses the test CA (no rate limits; browsers will not trust it).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${SERVER_NAME:?set SERVER_NAME in .env}" "${CERTBOT_EMAIL:?set CERTBOT_EMAIL in .env}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
EXTRA=()
[[ "${STAGING:-0}" == "1" ]] && EXTRA+=(--staging) && echo "STAGING mode: the certificate will be a test one"

$COMPOSE up -d --build nginx          # starts with a temporary self-signed certificate
$COMPOSE run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
    -d "$SERVER_NAME" --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email --non-interactive "${EXTRA[@]}"
$COMPOSE exec nginx sh -c '/select-cert.sh && nginx -s reload'
$COMPOSE up -d certbot
echo "Done. Check: curl -I https://$SERVER_NAME/api/health"
