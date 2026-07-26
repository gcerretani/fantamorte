"""Controlla Wikidata per decessi dei morituri delle leghe non ancora concluse.

Sono comprese le leghe **da iniziare** (`end_date >= oggi`): un decesso
pre-stagione scoperto mentre le iscrizioni sono ancora aperte si risolve
togliendo la persona dalla rosa, senza consumare una sostituzione.

Una sola query SPARQL per fetta di giocatori — «di questi, a chi è comparsa
una data di morte?» — senza filtro sull'anno: il periodo di gioco lo applica
lo scoring, non la rilevazione.
"""
import math

from django.core.management.base import BaseCommand
from django.utils import timezone

from game.models import League, SiteSettings, WikipediaPerson
from game.person_sync import sync_person_from_entity
from wikidata_api.client import WikidataClient


class Command(BaseCommand):
    help = 'Controlla Wikidata per decessi dei morituri nelle leghe non concluse'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Non salvare nulla')
        parser.add_argument('--league', type=str, help='Slug di una lega specifica')
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

        leagues = League.objects.all()
        if slug:
            leagues = leagues.filter(slug=slug)
        else:
            # Anche le leghe da iniziare: un decesso in fase di composizione va
            # scoperto quando c'è ancora tempo per cambiare rosa.
            leagues = leagues.filter(end_date__gte=timezone.now().date())

        leagues = list(leagues)
        if not leagues:
            self.stdout.write(self.style.WARNING('Nessuna lega da controllare.'))
            return

        self.stdout.write(f'Leghe da controllare: {[l.name for l in leagues]}')

        client = WikidataClient()

        # Persone candidate: membri attivi di queste leghe, non già morti
        active_persons = WikipediaPerson.objects.filter(
            team_members__team__league__in=leagues,
            team_members__replaced_by__isnull=True,
            is_dead=False,
        ).distinct()

        force = options.get('force')
        if not force:
            active_persons = active_persons.exclude(data_frozen=True)

        # Rotazione: invece di controllare tutti in un colpo (burst ogni
        # `interval` ore, poi scheduler a vuoto), ogni run controlla solo la
        # fetta più "vecchia" di giocatori, dimensionata per coprire l'intero
        # pool nell'arco dell'intervallo. I mai-controllati (last_checked NULL)
        # hanno priorità (in MySQL/MariaDB i NULL ordinano per primi in ASC).
        total_active = active_persons.count()
        if force:
            batch = total_active  # --force: tutto subito (comportamento storico)
        elif options.get('limit') is not None:
            batch = max(0, options['limit'])
        else:
            settings = SiteSettings.get()
            interval = max(1, settings.wikidata_check_interval_hours)
            schedule = max(1, settings.wikidata_check_schedule_hours)
            # Copri `total_active` persone in `interval/schedule` run:
            batch = max(1, math.ceil(total_active * schedule / interval))

        selected = active_persons.order_by('last_checked')[:batch]
        # Materializza prima dello slice-update (non si può .update() un
        # queryset già affettato): tengo pk (per l'update) e wikidata_id.
        selected = list(selected.values_list('pk', 'wikidata_id'))
        selected_pks = [pk for pk, _ in selected]
        wikidata_ids = [qid for _, qid in selected]
        self.stdout.write(
            f'Persone attive: {total_active} · controllate in questo run: {len(wikidata_ids)}'
        )
        if not wikidata_ids:
            return

        dead_ids = set()
        try:
            dead_ids.update(client.check_deaths_batch(wikidata_ids))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Errore SPARQL: {e}'))

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

            # L'applicazione dei dati (campi, claims, cache, Death + bonus)
            # è la stessa di ogni altro percorso: core condiviso.
            death, _created = sync_person_from_entity(
                person, entity, client=client, autoconfirm=autoconfirm,
            )

            status = 'confermato' if death and death.is_confirmed else 'da confermare'
            self.stdout.write(self.style.SUCCESS(
                f'Decesso ({status}): {person.name_it} ({qid}) † {death_date or death_year}'
            ))

        if not dry_run:
            # Segna controllati solo i giocatori di questo run (la fetta),
            # esclusi quelli appena rilevati morti (sync_person_from_entity ha
            # già aggiornato il loro last_checked). Così al run successivo la
            # rotazione passa alla fetta successiva (last_checked più vecchio).
            WikipediaPerson.objects.filter(pk__in=selected_pks).exclude(
                wikidata_id__in=dead_ids
            ).update(last_checked=timezone.now())
        self.stdout.write(self.style.SUCCESS('Controllo completato.'))
