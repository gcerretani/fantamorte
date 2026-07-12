#!/bin/sh
# Job periodici Fantamorte (gira in un container con l'immagine della app).
#
# - check_deaths: a ogni ciclo (il command rispetta già l'intervallo
#   wikidata_check_interval_hours dei SiteSettings, quindi girare spesso
#   non genera richieste extra a Wikidata)
# - una volta al giorno (alle SCHEDULER_DAILY_HOUR, default 06):
#   send_substitution_reminders, emit_league_lifecycle
set -u

DAILY_HOUR="${SCHEDULER_DAILY_HOUR:-6}"
INTERVAL="${SCHEDULER_INTERVAL_SECONDS:-3600}"
last_daily=""

echo "Scheduler avviato (ciclo ${INTERVAL}s, job giornalieri alle ${DAILY_HOUR}:00)"

while :; do
    python manage.py check_deaths || echo "check_deaths fallito (ritento al prossimo ciclo)"

    hour=$(date +%H)
    day=$(date +%F)
    if [ "$hour" -eq "$DAILY_HOUR" ] && [ "$day" != "$last_daily" ]; then
        python manage.py send_substitution_reminders || echo "send_substitution_reminders fallito"
        python manage.py emit_league_lifecycle || echo "emit_league_lifecycle fallito"
        last_daily="$day"
    fi

    sleep "$INTERVAL"
done
