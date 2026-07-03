#!/bin/sh
# Backup giornaliero del database (gira in un container mariadb).
# Dump compresso su /backups con rotazione a BACKUP_KEEP_DAYS giorni.
set -u

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

while :; do
    ts=$(date +%F)
    out="/backups/fantamorte-${ts}.sql.gz"
    echo "Backup: $out"
    if mariadb-dump -h db -u root -p"$MARIADB_ROOT_PASSWORD" \
        --single-transaction --routines --triggers fantamorte | gzip > "$out"; then
        find /backups -name 'fantamorte-*.sql.gz' -mtime "+${KEEP_DAYS}" -delete
    else
        echo "Backup fallito, riprovo al prossimo ciclo"
        rm -f "$out"
    fi
    sleep 86400
done
