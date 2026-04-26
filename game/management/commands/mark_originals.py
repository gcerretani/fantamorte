"""Marca le 'giocate originali' su una lega.

Una giocata è originale se la persona è stata scelta da un solo manager
all'inizio della lega. Una volta marcata, resta tale anche se altri
copiano la scelta in seguito (sostituzioni). Esegui il comando subito
dopo la chiusura delle iscrizioni.

Uso:
    python manage.py mark_originals --league <slug>     # specifica una lega
    python manage.py mark_originals                     # tutte le leghe in corso
"""
from collections import Counter
from django.core.management.base import BaseCommand
from django.utils import timezone
from game.models import League, TeamMember


class Command(BaseCommand):
    help = "Calcola e imposta il flag is_original sui TeamMember della lega"

    def add_arguments(self, parser):
        parser.add_argument('--league', type=str, default=None, help='Slug della lega')
        parser.add_argument('--reset', action='store_true', help='Resetta is_original a False prima del calcolo')

    def handle(self, *args, **options):
        slug = options.get('league')
        leagues = League.objects.all()
        if slug:
            leagues = leagues.filter(slug=slug)
        else:
            today = timezone.now().date()
            leagues = leagues.filter(start_date__lte=today, end_date__gte=today)
        leagues = list(leagues)
        if not leagues:
            self.stderr.write('Nessuna lega trovata.')
            return

        for league in leagues:
            if options['reset']:
                TeamMember.objects.filter(team__league=league).update(is_original=False)

            # Conteggio sulla rosa iniziale: TeamMember che non sono stati creati come sostituti.
            initial_members = TeamMember.objects.filter(team__league=league, replaces__isnull=True)
            counts = Counter(initial_members.values_list('person_id', flat=True))
            unique_person_ids = [pid for pid, c in counts.items() if c == 1]
            updated = TeamMember.objects.filter(
                team__league=league, person_id__in=unique_person_ids, replaces__isnull=True
            ).update(is_original=True)

            self.stdout.write(self.style.SUCCESS(
                f'{league.name}: {updated} giocate originali su {sum(counts.values())} totali.'
            ))
