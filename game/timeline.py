"""Timeline degli eventi di una lega (feed "cosa sta succedendo").

Costruisce una lista cronologica unica — decessi con le squadre che hanno
fatto punti, sostituzioni, nuove squadre, iscrizioni — a partire dai dati
già esistenti, senza modelli dedicati:

- decesso   → `Death` (filtro identico a timeline/CSV: periodo della lega e
              persona in almeno una rosa della lega); i punti per squadra
              arrivano dalla classifica cachata (`compute_league_rankings`);
- sostituzione → `TeamMember` con `replaced_by` valorizzato (il timestamp è
              l'`added_at` del subentrante);
- squadra   → `Team.created_at`;
- iscrizione → `LeagueMembership.joined_at`.

Ogni evento è un dict con `kind`, `when` (datetime aware, per l'ordinamento),
`date` (data locale, per il raggruppamento giorno-per-giorno nel template) e
i campi specifici del tipo. L'output è ordinato dal più recente.
"""
from datetime import datetime, time

from django.utils import timezone

from . import scoring
from .models import Death, TeamMember


def league_timeline(league, rankings=None, limit=None):
    """Eventi della lega in ordine cronologico inverso.

    `rankings` accetta l'output di `compute_league_rankings` se il chiamante
    lo ha già (evita di ricalcolarlo); `limit` taglia ai più recenti.
    """
    if rankings is None:
        rankings = scoring.compute_league_rankings(league)
    tz = timezone.get_current_timezone()
    events = []

    # Decessi + squadre a punti (dalla classifica, già filtrata per lega).
    scorers_by_death = {}
    for row in rankings:
        for item in row['deaths']:
            scorers_by_death.setdefault(item['death'].pk, []).append(
                {'team': row['team'], 'points': item['points']}
            )
    deaths = (
        Death.objects.filter(
            is_confirmed=True,
            death_date__gte=league.start_date,
            death_date__lte=league.end_date,
            person__team_members__team__league=league,
        )
        .distinct()
        .select_related('person')
        .defer('person__claims_cache')
    )
    for death in deaths:
        # Mezzanotte locale: a parità di giorno il decesso precede le
        # reazioni (sostituzioni ecc.), che nel feed compaiono sopra.
        events.append({
            'kind': 'death',
            'when': datetime.combine(death.death_date, time.min, tzinfo=tz),
            'date': death.death_date,
            'death': death,
            'scorers': sorted(
                scorers_by_death.get(death.pk, []), key=lambda s: -s['points']
            ),
        })

    # Sostituzioni: membro uscente → subentrante.
    subs = (
        TeamMember.objects.filter(team__league=league, replaced_by__isnull=False)
        .select_related('team', 'person', 'replaced_by__person')
    )
    for out in subs:
        when = out.replaced_by.added_at
        events.append({
            'kind': 'substitution',
            'when': when,
            'date': timezone.localtime(when).date(),
            'team': out.team,
            'member_out': out,
            'member_in': out.replaced_by,
        })

    # Nuove squadre.
    for team in league.teams.select_related('manager'):
        events.append({
            'kind': 'team',
            'when': team.created_at,
            'date': timezone.localtime(team.created_at).date(),
            'team': team,
        })

    # Iscrizioni alla lega.
    for m in league.memberships.select_related('user'):
        events.append({
            'kind': 'join',
            'when': m.joined_at,
            'date': timezone.localtime(m.joined_at).date(),
            'user': m.user,
        })

    events.sort(key=lambda e: e['when'], reverse=True)
    return events[:limit] if limit else events
