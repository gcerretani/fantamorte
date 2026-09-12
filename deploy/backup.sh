#!/bin/sh
# Backup giornaliero del database (gira in un container mariadb).
# Dump compresso su /backups con rotazione a BACKUP_KEEP_DAYS giorni.
#
# Il dump e la compressione sono volutamente due step distinti: una pipeline
# `mariadb-dump | gzip` in POSIX sh espone solo l'exit status dell'ultimo
# comando e può quindi trasformare un dump fallito in un falso successo.
set -u

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

while :; do
    ts=$(date +%F)
    out="/backups/fantamorte-${ts}.sql.gz"
    tmp_sql=$(mktemp /backups/.fantamorte-${ts}.XXXXXX.sql)
    tmp_gz="${tmp_sql}.gz"

    echo "Backup: $out"

    if mariadb-dump -h db -u root -p"$MARIADB_ROOT_PASSWORD" \
        --single-transaction --routines --triggers fantamorte > "$tmp_sql" \
        && [ -s "$tmp_sql" ] \
        && gzip -c "$tmp_sql" > "$tmp_gz" \
        && gzip -t "$tmp_gz"; then
        # Pubblica il nuovo backup solo dopo che dump e gzip sono entrambi
        # riusciti. `mv` sullo stesso filesystem rende la sostituzione atomica.
        mv -f "$tmp_gz" "$out"
        rm -f "$tmp_sql"
        # La retention parte soltanto dopo avere un backup nuovo verificato.
        find /backups -name 'fantamorte-*.sql.gz' -mtime "+${KEEP_DAYS}" -delete
        echo "Backup completato: $out"
    else
        echo "Backup fallito, preservo i backup esistenti e riprovo al prossimo ciclo" >&2
        rm -f "$tmp_sql" "$tmp_gz"
    fi

    sleep 86400
done
