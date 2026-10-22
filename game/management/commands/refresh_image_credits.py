"""Aggiorna l'attribuzione Commons delle foto (autore, licenza) a blocchi.

Lavora solo sulle persone il cui credito manca o si riferisce a una foto
diversa da quella attuale, quindi a regime è quasi sempre un no-op. Da
cron giornaliero (deploy/scheduler.sh).
"""
from django.core.management.base import BaseCommand

from game.models import WikipediaPerson
from game.person_images import credit_is_stale, refresh_image_credits


class Command(BaseCommand):
    help = "Aggiorna autore e licenza delle foto Commons delle persone"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500,
                            help='Massimo di persone da aggiornare in un giro (default 500)')

    def handle(self, *args, **options):
        people = WikipediaPerson.objects.exclude(image_url='').only(
            'pk', 'image_url', 'image_credit')
        stale = [p for p in people.iterator() if credit_is_stale(p)][:options['limit']]
        updated = 0
        for i in range(0, len(stale), 50):
            updated += refresh_image_credits(stale[i:i + 50])
        self.stdout.write(f'Crediti aggiornati: {updated} (da aggiornare: {len(stale)})')
