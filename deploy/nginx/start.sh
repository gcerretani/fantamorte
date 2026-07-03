#!/bin/sh
# Avvio nginx: collega i certificati Let's Encrypt se presenti, altrimenti
# genera un self-signed di bootstrap (permette il primo avvio e l'emissione
# del certificato reale via certbot webroot). Ricarica nginx ogni 6h per
# raccogliere i certificati rinnovati.
set -e

CERT_DIR="/etc/nginx/certs"
LE_DIR="/etc/letsencrypt/live/${SERVER_NAME}"

mkdir -p "$CERT_DIR"

if [ -f "$LE_DIR/fullchain.pem" ]; then
    ln -sf "$LE_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
    ln -sf "$LE_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
elif [ ! -f "$CERT_DIR/fullchain.pem" ]; then
    echo "Certificato Let's Encrypt assente: genero un self-signed di bootstrap."
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=${SERVER_NAME}" \
        -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem"
fi

# Reload periodico in background: aggancia certificati emessi/rinnovati.
(
    while :; do
        sleep 6h
        if [ -f "$LE_DIR/fullchain.pem" ]; then
            ln -sf "$LE_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
            ln -sf "$LE_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
        fi
        nginx -s reload || true
    done
) &

exec nginx -g 'daemon off;'
