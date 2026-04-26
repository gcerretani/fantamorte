"""Controlla Wikidata per decessi dei morituri di tutte le leghe in corso.

Una lega è "in corso" quando `start_date <= oggi <= end_date`. Per ogni
anno coperto da almeno una lega in corso, vengono controllate le
persone che fanno parte di quelle leghe (TeamMember attivi, non sostituiti).
"""
from datetime import date as date_cls, timedelta
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from game.models import (
    BonusType, Death, DeathBonus, League, Season, SiteSettings, WikipediaPerson,
)
from wikidata_api.client import WikidataClient


class Command(BaseCommand):
    help = 'Controlla Wikidata per decessi dei morituri nelle leghe in corso'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Non salvare nulla')
        parser.add_argument('--league', type=str, help='Slug di una lega specifica')
        parser.add_argument('--year', type=int, help='Forza un singolo anno per la query SPARQL')
        parser.add_argument('--force', action='store_true', help='Ignora il filtro last_checked e data_frozen')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        slug = options.get('league')
        forced_year = options.get('year')

        leagues = League.objects.all()
        if slug:
            leagues = leagues.filter(slug=slug)
        else:
            today = timezone.now().date()
            leagues = leagues.filter(start_date__lte=today, end_date__gte=today)

        leagues = list(leagues)
        if not leagues:
            self.stdout.write(self.style.WARNING('Nessuna lega in corso.'))
            return

        self.stdout.write(f'Leghe da controllare: {[l.name for l in leagues]}')

        # Anni da controllare via SPARQL: l'unione degli anni coperti dalle leghe
        if forced_year:
            years = [forced_year]
        else:
            years = sorted({y for l in leagues for y in range(l.start_date.year, l.end_date.year + 1)})

        client = WikidataClient()
        bonus_types = BonusType.objects.filter(
            is_active=True, detection_method__in=['wikidata', 'age']
        )

        # Persone candidate: membri attivi di queste leghe, non già morti
        active_persons = WikipediaPerson.objects.filter(
            team_members__team__league__in=leagues,
            team_members__replaced_by__isnull=True,
            is_dead=False,
        ).distinct()

        if not options.get('force'):
            interval = SiteSettings.get().wikidata_check_interval_hours
            threshold = timezone.now() - timedelta(hours=interval)
            active_persons = active_persons.exclude(data_frozen=True).filter(
                Q(last_checked__isnull=True) | Q(last_checked__lt=threshold)
            )

        wikidata_ids = list(active_persons.values_list('wikidata_id', flat=True))
        self.stdout.write(f'Persone da controllare: {len(wikidata_ids)}')
        if not wikidata_ids:
            return

        # Unione dei decessi rilevati per ogni anno
        dead_ids = set()
        for year in years:
            try:
                dead_ids.update(client.check_deaths_batch(wikidata_ids, year))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Errore SPARQL ({year}): {e}'))

        self.stdout.write(f'Decessi rilevati: {len(dead_ids)}')

        for qid in dead_ids:
            try:
                person = WikipediaPerson.objects.get(wikidata_id=qid)
            except WikipediaPerson.DoesNotExist:
                continue
            try:
                entity = client.get_entity(qid)
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'Errore fetch {qid}: {e}'))
                continue

            death_date = entity.get('death_date')
            death_year = entity.get('death_year')
            if not death_date and not death_year:
                continue

            if dry_run:
                self.stdout.write(f'[DRY] {person.name_it} ({qid}) † {death_date or death_year}')
                continue

            person.death_date = death_date
            person.death_year = death_year
            person.is_dead = True
            person.claims_cache = entity.get('claims_cache', {})
            person.last_checked = timezone.now()
            person.save()

            # Trova / crea una Season corrispondente all'anno (per indicizzare la Death)
            year_for_death = (death_date or date_cls(death_year, 1, 1)).year
            season, _ = Season.objects.get_or_create(
                year=year_for_death,
                defaults={
                    'is_active': False,
                    'registration_opens': date_cls(year_for_death, 1, 1),
                    'registration_closes': date_cls(year_for_death, 12, 31),
                },
            )

            death, created = Death.objects.get_or_create(
                person=person,
                defaults={
                    'season': season,
                    'death_date': death_date or date_cls(year_for_death, 12, 31),
                    'death_age': person.get_age_at_death(),
                    'source': Death.SOURCE_WIKIDATA,
                    'is_confirmed': False,
                },
            )

            if created:
                # Auto-rileva bonus
                for bt in client.detect_bonuses(qid, person.claims_cache, bonus_types):
                    DeathBonus.objects.get_or_create(
                        death=death, bonus_type=bt,
                        defaults={'points_awarded': bt.points, 'is_auto_detected': True},
                    )
                age = person.get_age_at_death()
                if age is not None:
                    for bt in bonus_types.filter(detection_method='age'):
                        if client.detect_age_bonus(age, bt):
                            DeathBonus.objects.get_or_create(
                                death=death, bonus_type=bt,
                                defaults={'points_awarded': bt.points, 'is_auto_detected': True},
                            )

            self.stdout.write(self.style.SUCCESS(f'Decesso: {person.name_it} ({qid}) † {death_date or death_year}'))

        if not dry_run:
            active_persons.exclude(wikidata_id__in=dead_ids).update(last_checked=timezone.now())
        self.stdout.write(self.style.SUCCESS('Controllo completato.'))
