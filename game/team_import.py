"""Importazione della rosa di una propria squadra in un'altra lega (#100).

Le "stagioni" sono leghe come le altre: chi si iscrive alla lega nuova può
ripartire dalla rosa di una sua squadra qualsiasi. L'importazione è
**selettiva**: passa solo chi rispetta le regole della lega di destinazione,
che possono essere diverse da quelle di origine (lingue wiki ammesse, limiti
di rosa e di età). Il piano si calcola sempre lato server, sia per
l'anteprima (GET) sia per l'applicazione (POST), così l'anteprima non è una
promessa: al POST si ricalcola sotto lock e si importa quello che passa.

Nessuna chiamata a Wikidata per i dati anagrafici: le persone sono già in DB
e `check_deaths` controlla tutte le rose attive a prescindere dalla lega.
L'unica verifica di rete è quella delle wiki, in una sola SPARQL (cachata).
"""
import logging
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from wikidata_api.client import WikidataClient

from . import views
from .models import Team, TeamMember
from .v1_hardening_views import _validate_roster_person

logger = logging.getLogger(__name__)

WIKI_CACHE_SECONDS = 15 * 60


class WikiCheckUnavailable(Exception):
    """La verifica delle pagine Wikipedia non è riuscita: non si importa
    nessuno "sulla fiducia" in una lega che filtra per lingua."""


@dataclass
class ImportRow:
    member: TeamMember
    importable: bool
    as_captain: bool = False
    reason: str = ''
    note: str = ''

    @property
    def person(self):
        return self.member.person


def league_wikis(league):
    if not league or not league.search_wikipedia_langs:
        return []
    return [w for w in league.search_wikipedia_langs.split(',') if w in views._VALID_WIKIS]


def wiki_labels(wikis):
    labels = dict(views.WIKIPEDIA_LANGS)
    return ', '.join(labels.get(w, w) for w in wikis)


def people_on_wikis(people, wikis):
    """QID delle persone con una pagina in almeno una delle `wikis`.

    Chi ha già `wikipedia_url_it` è promosso senza rete se `itwiki` è tra le
    ammesse; gli altri passano da una SPARQL, cachata per qid+wiki.
    """
    ok = set()
    pending = []
    for p in people:
        if 'itwiki' in wikis and p.wikipedia_url_it:
            ok.add(p.wikidata_id)
            continue
        cached = cache.get(_wiki_cache_key(p.wikidata_id, wikis))
        if cached is None:
            pending.append(p.wikidata_id)
        elif cached:
            ok.add(p.wikidata_id)
    if pending:
        client = WikidataClient()
        client.delay = 0         # richiesta interattiva: niente throttle
        client.sparql_timeout = 8  # fail-fast, come le altre viste interattive
        try:
            found = client.qids_with_sitelinks(pending, wikis)
        except Exception as exc:
            logger.warning('Verifica sitelink fallita per %s', pending, exc_info=True)
            raise WikiCheckUnavailable() from exc
        for qid in pending:
            cache.set(_wiki_cache_key(qid, wikis), qid in found, WIKI_CACHE_SECONDS)
        ok |= found
    return ok


def _wiki_cache_key(qid, wikis):
    return f'wd_sitelink:{qid}:{",".join(sorted(wikis))}'


def plan_import(source, target):
    """Cosa succederebbe importando le persone attive di `source` in `target`.

    Ritorna una lista di ImportRow nell'ordine in cui vengono valutate
    (capitani prima, poi per nome): i limiti di rosa ed età si riempiono in
    quest'ordine. Solleva WikiCheckUnavailable se la lega filtra per wiki e
    la verifica non è possibile.
    """
    league = target.league
    max_captains = league.max_captains if league else 1
    max_non_captains = league.max_non_captains if league else 11
    max_age = league.max_total_age if league else 0

    current = list(target.members.filter(replaced_by=None).select_related('person'))
    in_roster = {m.person_id for m in current}
    captains = sum(1 for m in current if m.is_captain)
    non_captains = len(current) - captains
    total_age = sum(m.person.get_current_age() or 0 for m in current)

    candidates = sorted(
        source.members.filter(replaced_by=None).select_related('person'),
        key=lambda m: (not m.is_captain, m.person.name_it.lower()),
    )

    wikis = league_wikis(league)
    wiki_ok = None
    if wikis:
        to_check = [m.person for m in candidates
                    if m.person_id not in in_roster and not m.person.is_dead]
        wiki_ok = people_on_wikis(to_check, wikis) if to_check else set()

    rows = []
    for m in candidates:
        p = m.person
        if p.pk in in_roster:
            rows.append(ImportRow(m, False, reason='Già in rosa'))
            continue
        if p.is_dead:
            rows.append(ImportRow(m, False, reason='Deceduto/a'))
            continue
        error = _validate_roster_person(p, league)
        if error:
            rows.append(ImportRow(m, False, reason=error))
            continue
        if wiki_ok is not None and p.wikidata_id not in wiki_ok:
            rows.append(ImportRow(
                m, False, reason=f'Nessuna pagina Wikipedia ammessa ({wiki_labels(wikis)})'))
            continue
        age = p.get_current_age() or 0
        if max_age and total_age + age > max_age:
            rows.append(ImportRow(
                m, False,
                reason=f"Supererebbe il limite d'età ({total_age + age} su {max_age})"))
            continue
        as_captain = m.is_captain and captains < max_captains
        if not as_captain and non_captains >= max_non_captains:
            rows.append(ImportRow(m, False, reason='Rosa al completo'))
            continue
        note = ''
        if m.is_captain and not as_captain:
            note = 'senza fascia: i capitani sono già al completo'
        if as_captain:
            captains += 1
        else:
            non_captains += 1
        total_age += age
        in_roster.add(p.pk)
        rows.append(ImportRow(m, True, as_captain=as_captain, note=note))
    return rows


def import_sources(team, user):
    """Le altre squadre dell'utente da cui si può importare."""
    return (Team.objects.filter(manager=user).exclude(pk=team.pk)
            .select_related('league').order_by('-league__start_date', 'name'))


class TeamImportView(LoginRequiredMixin, View):
    template_name = 'game/team_import.html'

    def _load(self, request, pk):
        team = get_object_or_404(Team.objects.select_related('league'), pk=pk)
        # Stessa finestra di AddPersonView: solo il manager, a iscrizioni aperte.
        if not views._can_edit_team(team, request.user):
            messages.error(request, 'La rosa non è modificabile.')
            return team, None
        return team, True

    def _source(self, request, team, raw):
        try:
            source_pk = int(raw)
        except (TypeError, ValueError):
            return None
        # Solo squadre proprie: una squadra altrui è un 404, non un 403
        # (non si conferma nemmeno che esista).
        source = Team.objects.filter(
            pk=source_pk, manager=request.user).exclude(pk=team.pk).select_related('league').first()
        if source is None:
            raise Http404('Squadra di origine non trovata.')
        return source

    def get(self, request, pk):
        team, ok = self._load(request, pk)
        if not ok:
            return redirect('team_edit', pk=pk)
        sources = list(import_sources(team, request.user))
        source = self._source(request, team, request.GET.get('da'))
        rows, wiki_error = [], False
        if source is not None:
            try:
                rows = plan_import(source, team)
            except WikiCheckUnavailable:
                wiki_error = True
        league = team.league
        return render(request, self.template_name, {
            'team': team,
            'league': league,
            'sources': sources,
            'source': source,
            'rows': rows,
            'importable_count': sum(1 for r in rows if r.importable),
            'skipped_count': sum(1 for r in rows if not r.importable),
            'wiki_error': wiki_error,
            'wikis_label': wiki_labels(league_wikis(league)),
            'can_copy_jolly': bool(
                source and source.jolly_month and (not league or league.jolly_enabled)),
        })

    def post(self, request, pk):
        team, ok = self._load(request, pk)
        if not ok:
            return redirect('team_edit', pk=pk)
        source = self._source(request, team, request.POST.get('da'))
        if source is None:
            return redirect('team_import', pk=pk)
        try:
            with transaction.atomic():
                # Stesso lock di AddPersonView (hardened_views): due import o
                # un import e un'aggiunta in parallelo non sforano i limiti.
                team = Team.objects.select_for_update().select_related('league').get(pk=team.pk)
                rows = plan_import(source, team)
                imported = [r for r in rows if r.importable]
                # create() e non bulk_create(): il post_save di TeamMember
                # invalida la cache delle classifiche (signals.py).
                for r in imported:
                    TeamMember.objects.create(team=team, person=r.person, is_captain=r.as_captain)
                if (request.POST.get('copy_jolly') == '1' and source.jolly_month
                        and (not team.league or team.league.jolly_enabled)):
                    team.jolly_month = source.jolly_month
                    team.save(update_fields=['jolly_month'])
        except WikiCheckUnavailable:
            messages.error(request, 'Verifica delle pagine Wikipedia non disponibile: riprova tra poco.')
            return redirect(f"{request.path}?da={source.pk}")
        skipped = len(rows) - len(imported)
        n = len(imported)
        msg = (f'Importat{"o" if n == 1 else "i"} {n} personagg{"io" if n == 1 else "i"} '
               f'da «{source.name}».')
        if skipped:
            msg += (f' {skipped} scartat{"o" if skipped == 1 else "i"}: '
                    'non rispettano le regole della lega o sono già in rosa.')
        messages.success(request, msg)
        return redirect('team_edit', pk=team.pk)
