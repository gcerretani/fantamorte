"""Controlla su Wikidata i decessi dei morituri presenti in una qualsiasi rosa."""
import math

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from game.models import League, SiteSettings, WikipediaPerson
from game.person_sync import sync_person_from_entity
from wikidata_api.client import WikidataClient


class Command(BaseCommand):
    help = 'Controlla Wikidata per decessi dei morituri presenti nelle rose'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Non salvare nulla')
        parser.add_argument('--league', type=str,
                            help='Restringi ai giocatori di una lega (default: tutte)')
        parser.add_argument('--force', action='store_true', help='Ignora la rotazione (batch) e data_frozen: controlla tutti subito')
        parser.add_argument('--limit', type=int, help='Forza la dimensione della fetta di giocatori per questo run (override della rotazione automatica)')
        parser.add_argument(
            '--no-autoconfirm', action='store_true',
            help='Crea i decessi come non confermati (default: i decessi da Wikidata '
                 'con data valida vengono confermati subito, con punti e notifiche)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        slug = options.get('league')
        autoconfirm = not options['no_autoconfirm']
        client = WikidataClient()

        active_persons = WikipediaPerson.objects.filter(
            team_members__replaced_by__isnull=True,
            is_dead=False,
        ).distinct()
        if slug:
            league = League.objects.filter(slug=slug).first()
            if league is None:
                raise CommandError(f'Lega "{slug}" inesistente.')
            self.stdout.write(f'Lega: {league.name}')
            active_persons = active_persons.filter(team_members__team__league=league)

        force = options.get('force')
        if not force:
            active_persons = active_persons.exclude(data_frozen=True)

        total_active = active_persons.count()
        if force:
            batch = total_active
        elif options.get('limit') is not None:
            batch = max(0, options['limit'])
        else:
            settings = SiteSettings.get()
            interval = max(1, settings.wikidata_check_interval_hours)
            schedule = max(1, settings.wikidata_check_schedule_hours)
            batch = max(1, math.ceil(total_active * schedule / interval))

        selected = list(
            active_persons.order_by('last_checked')[:batch]
            .values_list('pk', 'wikidata_id')
        )
        selected_pks = [pk for pk, _ in selected]
        wikidata_ids = [qid for _, qid in selected]
        self.stdout.write(
            f'Persone attive: {total_active} · controllate in questo run: {len(wikidata_ids)}'
        )
        if not wikidata_ids:
            return

        # `last_checked` means last successful check. A failed batch must fail
        # the command before any selected row is advanced, otherwise scheduler
        # telemetry says the check passed and those people are deprioritized.
        try:
            dead_ids = set(client.check_deaths_batch(wikidata_ids))
        except Exception as e:
            raise CommandError(f'Errore SPARQL: {e}') from e

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

            death, _created = sync_person_from_entity(
                person, entity, client=client, autoconfirm=autoconfirm, force=force,
            )
            status = 'confermato' if death and death.is_confirmed else 'da confermare'
            self.stdout.write(self.style.SUCCESS(
                f'Decesso ({status}): {person.name_it} ({qid}) † {death_date or death_year}'
            ))

        if not dry_run:
            # Non-dead rows are only advanced after the batch completed
            # successfully. Dead rows that failed the detail fetch stay old
            # because they are excluded by dead_ids and will be retried.
            WikipediaPerson.objects.filter(pk__in=selected_pks).exclude(
                wikidata_id__in=dead_ids
            ).update(last_checked=timezone.now())
        self.stdout.write(self.style.SUCCESS('Controllo completato.'))
