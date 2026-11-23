#!/bin/sh
set -eu

DOMAIN="${NGINX_SERVER_NAME:-_}"
CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
KEY="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
HTTPS_TEMPLATE="/etc/nginx/modes/https.conf.template"

if [ "$DOMAIN" != "_" ] && [ -f "$CERT" ] && [ -f "$KEY" ] && [ -f "$HTTPS_TEMPLATE" ]; then
  echo "[batack-nginx] Certificat TLS trouvé pour ${DOMAIN} : activation HTTPS (443)."
  envsubst '${NGINX_SERVER_NAME}' < "$HTTPS_TEMPLATE" > /etc/nginx/conf.d/default.conf
else
  echo "[batack-nginx] Aucun certificat TLS pour ${DOMAIN} : mode HTTP (80) actif pour Certbot."
fi

