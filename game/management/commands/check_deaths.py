from django.core.management.base import BaseCommand
from django.utils import timezone
from game.models import Season, WikipediaPerson, Death, DeathBonus, BonusType
from wikidata_api.client import WikidataClient


class Command(BaseCommand):
    help = 'Controlla Wikidata per decessi dei morituri nella stagione attiva'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Non salvare nulla')
        parser.add_argument('--season', type=int, help='Anno stagione (default: stagione attiva)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        season_year = options.get('season')

        if season_year:
            season = Season.objects.filter(year=season_year).first()
        else:
            season = Season.objects.filter(is_active=True).first()

        if not season:
            self.stdout.write(self.style.ERROR('Nessuna stagione trovata.'))
            return

        self.stdout.write(f'Stagione: {season.year}')

        active_persons = WikipediaPerson.objects.filter(
            team_members__team__season=season,
            is_dead=False,
        ).distinct()

        wikidata_ids = list(active_persons.values_list('wikidata_id', flat=True))
        self.stdout.write(f'Persone da controllare: {len(wikidata_ids)}')

        if not wikidata_ids:
            self.stdout.write('Nessuna persona da controllare.')
            return

        client = WikidataClient()

        try:
            dead_ids = client.check_deaths_batch(wikidata_ids, season.year)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Errore SPARQL batch: {e}'))
            return

        self.stdout.write(f'Decessi rilevati: {len(dead_ids)}')

        bonus_types = BonusType.objects.filter(
            is_active=True,
            detection_method__in=['wikidata', 'age']
        )

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

            if not dry_run:
                person.death_date = death_date
                person.death_year = death_year
                person.is_dead = True
                person.claims_cache = entity.get('claims_cache', {})
                person.last_checked = timezone.now()
                person.save()

                death, created = Death.objects.get_or_create(
                    person=person,
                    defaults={
                        'season': season,
                        'death_date': death_date or person.death_date,
                        'death_age': person.get_age_at_death(),
                        'source': Death.SOURCE_WIKIDATA,
                        'is_confirmed': False,
                    }
                )

                if created:
                    detected = client.detect_bonuses(qid, person.claims_cache, bonus_types)
                    for bt in detected:
                        DeathBonus.objects.get_or_create(
                            death=death, bonus_type=bt,
                            defaults={'points_awarded': bt.points, 'is_auto_detected': True}
                        )
                    age = person.get_age_at_death()
                    if age is not None:
                        for bt in bonus_types.filter(detection_method='age'):
                            if client.detect_age_bonus(age, bt):
                                DeathBonus.objects.get_or_create(
                                    death=death, bonus_type=bt,
                                    defaults={'points_awarded': bt.points, 'is_auto_detected': True}
                                )

            self.stdout.write(
                self.style.SUCCESS(f'{"[DRY] " if dry_run else ""}Decesso: {person.name_it} ({qid}) † {death_date or death_year}')
            )

        # Also refresh last_checked for alive persons (in batches to respect rate limits)
        alive_persons = active_persons.exclude(wikidata_id__in=dead_ids)
        if not dry_run:
            alive_persons.update(last_checked=timezone.now())

        self.stdout.write(self.style.SUCCESS('Controllo completato.'))
