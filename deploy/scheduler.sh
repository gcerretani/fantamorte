#!/bin/sh
# Job periodici Fantamorte (gira in un container con l'immagine della app).
#
# `last_daily` indica l'ultimo giorno completato con successo, non l'ultimo
# tentativo. Dopo un errore i job vengono quindi ritentati al ciclo successivo.
set -u

DAILY_HOUR="${SCHEDULER_DAILY_HOUR:-6}"
INTERVAL="${SCHEDULER_INTERVAL_SECONDS:-3600}"
last_daily=""

echo "Scheduler avviato (ciclo ${INTERVAL}s, job giornalieri dalle ${DAILY_HOUR}:00)"

while :; do
    python manage.py check_deaths || echo "check_deaths fallito (ritento al prossimo ciclo)"

    hour=$(date +%H)
    day=$(date +%F)
    # >= invece di == recupera un container fermo proprio all'ora prevista.
    if [ "$hour" -ge "$DAILY_HOUR" ] && [ "$day" != "$last_daily" ]; then
        daily_ok=1
        python manage.py send_substitution_reminders || {
            echo "send_substitution_reminders fallito"
            daily_ok=0
        }
        python manage.py emit_league_lifecycle || {
            echo "emit_league_lifecycle fallito"
            daily_ok=0
        }
        if [ "$daily_ok" -eq 1 ]; then
            last_daily="$day"
        else
            echo "Job giornalieri incompleti: ritento al prossimo ciclo"
        fi
    fi

    sleep "$INTERVAL"
done
