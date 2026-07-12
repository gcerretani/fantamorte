"""Emette le notifiche di ciclo di vita della lega (inizio / fine) nel feed
in-app dei membri.

Pensato per girare dal cron insieme a `check_deaths`. Idempotente: usa
l'esistenza della riga `Notification (user, league, kind)` come marker, così la
stessa transizione non viene notificata due volte. Non invia push/email di suo:
crea solo le righe del feed (i canali si applicano solo se l'utente li ha
attivati per la categoria "Inizio/fine lega e blocco squadra", default off).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from game.models import League, Notification
from game.notifications import emit_league_lifecycle_notifications


class Command(BaseCommand):
    help = 'Crea le notifiche feed di inizio/fine lega per i membri.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Mostra cosa verrebbe creato senza scrivere.')
        parser.add_argument('--league', type=str, default=None,
                            help='Slug di una lega specifica.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        slug = options.get('league')
        today = timezone.now().date()

        leagues_qs = League.objects.all()
        if slug:
            leagues_qs = leagues_qs.filter(slug=slug)

        created_total = 0
        for league in leagues_qs:
            transitions = []
            if league.start_date and league.start_date <= today:
                transitions.append(Notification.KIND_LEAGUE_STARTED)
            if league.end_date and league.end_date < today:
                transitions.append(Notification.KIND_LEAGUE_ENDED)

            for kind in transitions:
                if dry_run:
                    pending = not Notification.objects.filter(
                        league=league, kind=kind,
                    ).exists()
                    if pending:
                        self.stdout.write(f'[dry-run] {kind}: {league.name}')
                    continue
                created = emit_league_lifecycle_notifications(league, kind)
                if created:
                    created_total += created
                    self.stdout.write(f'{kind}: {league.name} → {created} notifiche')

        self.stdout.write(self.style.SUCCESS(
            f'Notifiche lifecycle create: {created_total}'
        ))
