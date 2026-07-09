#!/bin/sh
set -e

python manage.py migrate --noinput

# STATIC_ROOT è un named volume condiviso con nginx e persistente tra i
# deploy: dal secondo avvio in poi il suo contenuto OSCURA quello raccolto
# in fase di build dell'immagine. Senza questo collect a runtime i template
# nuovi verrebbero serviti con CSS/JS (e manifest) della versione precedente.
python manage.py collectstatic --noinput || \
  echo "WARNING: collectstatic fallito, gli asset statici potrebbero essere obsoleti" >&2

exec "$@"
