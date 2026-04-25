"""Marca le 'giocate originali' su una stagione.

Una giocata è originale se la persona è stata scelta da un solo manager
all'inizio della stagione. Una volta marcata, resta tale anche se altri
copiano la scelta in seguito (sostituzioni). Per questo il comando va
eseguito **all'inizio** della stagione, dopo la chiusura delle iscrizioni.

Uso:
    python manage.py mark_originals            # stagione attiva
    python manage.py mark_originals --year 2026
"""
from collections import Counter
from django.core.management.base import BaseCommand
from game.models import Season, TeamMember


class Command(BaseCommand):
    help = "Calcola e imposta il flag is_original sui TeamMember della stagione"

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=None)
        parser.add_argument('--reset', action='store_true', help='Resetta is_original a False prima del calcolo')

    def handle(self, *args, **options):
        if options['year']:
            season = Season.objects.filter(year=options['year']).first()
        else:
            season = Season.objects.filter(is_active=True).first()
        if not season:
            self.stderr.write('Nessuna stagione trovata.')
            return

        if options['reset']:
            TeamMember.objects.filter(team__season=season).update(is_original=False)

        # Conteggio sulla rosa iniziale (il manager può aver fatto sostituzioni
        # successive: contiamo solo i TeamMember senza `replaces` cioè le scelte iniziali).
        initial_members = TeamMember.objects.filter(team__season=season, replaces__isnull=True)
        counts = Counter(initial_members.values_list('person_id', flat=True))

        unique_person_ids = [pid for pid, c in counts.items() if c == 1]
        updated = TeamMember.objects.filter(
            team__season=season, person_id__in=unique_person_ids, replaces__isnull=True
        ).update(is_original=True)

        self.stdout.write(self.style.SUCCESS(
            f'Stagione {season.year}: {updated} giocate originali su {sum(counts.values())} totali.'
        ))
