#!/usr/bin/env bash
# One-time bootstrap: get the first real Let's Encrypt certificate.
#
# Chicken-and-egg problem: nginx refuses to start with an ssl_certificate
# directive pointing at files that don't exist yet, but certbot needs nginx
# running (on port 80) to answer the HTTP-01 challenge. Fix: generate a
# throwaway self-signed "dummy" cert first so nginx can start, then swap it
# for the real one issued by certbot.
#
# Run this ONCE per domain, after DNS for $DOMAIN already points at this
# server (check with: dig +short $DOMAIN). Ordinary deploys afterwards just
# use ./deploy.sh — renewal is handled automatically by the certbot
# container's renew loop in docker-compose.yml.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

: "${DOMAIN:?Set DOMAIN in .env first}"
: "${CERTBOT_EMAIL:?Set CERTBOT_EMAIL in .env first}"

DATA_PATH="./certbot"
RSA_KEY_SIZE=4096

if [ -d "$DATA_PATH/conf/live/$DOMAIN" ]; then
    echo "Certificate for $DOMAIN already exists in $DATA_PATH — nothing to do."
    echo "(To force a fresh one, delete that directory first.)"
    exit 0
fi

echo "==> Checking that $DOMAIN resolves to this server"
resolved="$(dig +short "$DOMAIN" | tail -n1 || true)"
public_ip="$(curl -fs https://api.ipify.org || true)"
if [ -n "$public_ip" ] && [ "$resolved" != "$public_ip" ]; then
    echo "WARNING: $DOMAIN resolves to '${resolved:-nothing}', this server's IP is '$public_ip'."
    echo "Let's Encrypt will fail the HTTP-01 challenge until DNS points here. Continuing anyway..."
fi

echo "==> Creating dummy certificate for $DOMAIN"
mkdir -p "$DATA_PATH/conf/live/$DOMAIN"
docker compose run --rm --entrypoint "\
  openssl req -x509 -nodes -newkey rsa:$RSA_KEY_SIZE -days 1 \
    -keyout '/etc/letsencrypt/live/$DOMAIN/privkey.pem' \
    -out '/etc/letsencrypt/live/$DOMAIN/fullchain.pem' \
    -subj '/CN=localhost'" certbot

echo "==> Starting nginx with the dummy certificate"
docker compose up -d nginx

echo "==> Deleting dummy certificate"
docker compose run --rm --entrypoint "\
  rm -rf /etc/letsencrypt/live/$DOMAIN && \
  rm -rf /etc/letsencrypt/archive/$DOMAIN && \
  rm -rf /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

echo "==> Requesting real certificate from Let's Encrypt for $DOMAIN"
docker compose run --rm --entrypoint "\
  certbot certonly --webroot -w /var/www/certbot \
    --email $CERTBOT_EMAIL -d $DOMAIN \
    --rsa-key-size $RSA_KEY_SIZE --agree-tos --no-eff-email" certbot

echo "==> Reloading nginx with the real certificate"
docker compose exec nginx nginx -s reload

echo "==> Starting the certbot renewal daemon"
docker compose up -d certbot

echo "==> Done — https://$DOMAIN should now work."
