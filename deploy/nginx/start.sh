#!/bin/sh
# Hook dell'entrypoint ufficiale nginx: prepara i certificati prima che
# 20-envsubst-on-templates.sh generi default.conf e nginx venga avviato.
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

# Il processo resta figlio dell'entrypoint; al primo wake nginx è già attivo.
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
