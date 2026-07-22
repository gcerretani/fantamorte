"""Logica punteggio. La League è la sorgente di verità per le regole.

Ogni team ha (o avrà) un puntatore alla league. Le morti che contano sono
quelle confermate avvenute tra `league.start_date` e `league.end_date`.

Le leghe condividono solo il database degli eventi (Death/DeathBonus come
proprietà del decesso): tutto ciò che è relativo alla lega — bonus primo e
ultimo morto inclusi — viene calcolato qui, senza righe persistite condivise.
"""
import time

from django.core.cache import cache
from django.db.models import Prefetch
from django.utils import timezone

from .models import Death, BonusType, League, LeagueBonus, TeamMember


# ---------- helpers ----------

def _league_of(team):
    return team.league if team.league_id else None


def _base_points(league):
    return league.base_points if league else 50


def _captain_multiplier(league):
    return league.captain_multiplier if league else 2


def _jolly_multiplier(league):
    if league and not league.jolly_enabled:
        return 1
    return league.jolly_multiplier if league else 2


def _league_bonus_map(league):
    """Dizionario bonus_type_id → LeagueBonus attivo per la lega (o {} se senza lega)."""
    if league is None:
        return {}
    return {
        lb.bonus_type_id: lb
        for lb in LeagueBonus.objects.filter(league=league, is_active=True).select_related('bonus_type')
    }


def _bonus_points_in_league(bonus, league, lb_map=None):
    """Punti effettivi di un DeathBonus all'interno di una lega.

    Tiene conto degli override (LeagueBonus.override_points / override_formula)
    se presenti, altrimenti dei valori del BonusType. Se passato `lb_map`
    (dict bonus_type_id -> LeagueBonus) lo usa per evitare query.
    """
    bt = bonus.bonus_type
    # Primo/ultimo morto sono relativi alla lega e calcolati dinamicamente
    # (_first_last_death_pks): eventuali righe DeathBonus legacy vanno ignorate.
    if bt.detection_method in (BonusType.DETECTION_FIRST_DEATH, BonusType.DETECTION_LAST_DEATH):
        return 0
    age = bonus.death.death_age
    if league is not None:
        if lb_map is None:
            lb_map = _league_bonus_map(league)
        lb = lb_map.get(bt.id)
        if lb is None:
            # La lega esiste ma il bonus non è tra quelli configurati: escluso
            # (regola: una lega usa solo i bonus che si è scelta).
            return 0
        return lb.compute_points(age=age)
    # Fallback legacy: usa i punti del BonusType
    if bt.points_formula:
        return bt.compute_points(age=age)
    if bonus.points_awarded is not None:
        return bonus.points_awarded
    return bt.points


# ---------- cache invalidazione ----------

_RANKINGS_VERSION_KEY = 'league_rankings_version:{league_id}'
_RANKINGS_DATA_KEY = 'league_rankings:{league_id}:v{version}'
_RANKINGS_TTL = 300  # 5 minuti, più che sufficienti come safety net


def _rankings_version(league_id):
    key = _RANKINGS_VERSION_KEY.format(league_id=league_id)
    v = cache.get(key)
    if v is None:
        v = int(time.time())
        cache.set(key, v, None)
    return v


def invalidate_league_rankings(league_id):
    """Bumpa la versione della cache dei rankings per una lega.

    Pensata per essere chiamata da signal quando cambiano dati che influenzano
    il punteggio (Death, DeathBonus, LeagueBonus, TeamMember, Team, League).
    """
    if league_id is None:
        return
    cache.set(_RANKINGS_VERSION_KEY.format(league_id=league_id), int(time.time() * 1000), None)


def league_cache_version(league_id):
    """Versione corrente della cache di lega (vedi invalidate_league_rankings).

    Usata anche come componente di chiavi derivate (es. i bonus potenziali del
    modal persona): ogni invalidazione dei rankings — regole o bonus della
    lega cambiati — le fa scadere in blocco.
    """
    return _rankings_version(league_id)


def invalidate_person_bonus_caches(person):
    """Invalida le cache bonus derivate dai claim di una persona.

    - ``fm_potential:<league>:<ver>:<person>``: bonus "se morisse oggi" del
      modal, per tutte le leghe in cui la persona è in una rosa attiva;
    - ``wd_bonus:<qid>:<prop>:<value>``: esito (7 giorni) dei check
      gerarchici SPARQL, per tutti i bonus Wikidata attivi.

    Da chiamare ogni volta che ``claims_cache`` viene rinfrescato (diff/apply
    della pagina admin giocatori, ``check_deaths``): un esito negativo
    cachato prima del refresh non deve sopravvivere ai claim nuovi.
    """
    league_pks = League.objects.filter(
        teams__members__person=person,
        teams__members__replaced_by__isnull=True,
    ).distinct().values_list('pk', flat=True)
    cache.delete_many([
        f'fm_potential:{lpk}:{_rankings_version(lpk)}:{person.pk}'
        for lpk in league_pks
    ])
    cache.delete_many([
        f'wd_bonus:{person.wikidata_id}:{bt.wikidata_property}:{bt.wikidata_value}'
        for bt in BonusType.objects.filter(
            detection_method=BonusType.DETECTION_WIKIDATA, is_active=True,
        ).exclude(wikidata_property='').exclude(wikidata_value='')
    ])


def _confirmed_deaths_for_league(league):
    """Decessi confermati che riguardano la lega: nel periodo di gioco e di
    persone presenti in almeno una rosa della lega. Il database dei decessi è
    condiviso tra leghe, ma un morto che nessuno gioca qui non conta — nemmeno
    come "primo/ultimo morto" della lega (vedi `_first_last_death_pks`)."""
    # `claims_cache` è un blob JSON enorme (media ~100 KB/persona) che lo
    # scoring non usa mai: deferirlo evita la deserializzazione JSON che
    # domina il costo CPU del calcolo classifiche.
    qs = (
        Death.objects.filter(is_confirmed=True)
        .select_related('person')
        .defer('person__claims_cache')
        .prefetch_related('bonuses__bonus_type')
    )
    if league is not None:
        qs = qs.filter(
            death_date__gte=league.start_date,
            death_date__lte=league.end_date,
            person__team_members__team__league=league,
        ).distinct()
    return qs


def _find_member(team, person_id):
    """Cerca un TeamMember per `person_id` usando la cache prefetchata se disponibile."""
    if 'members' in getattr(team, '_prefetched_objects_cache', {}):
        return next((m for m in team.members.all() if m.person_id == person_id), None)
    return team.members.filter(person_id=person_id).first()


def _first_last_death_pks(league, deaths):
    """pk del primo e dell'ultimo decesso confermato DELLA LEGA.

    `deaths` deve essere già filtrato sul periodo della lega e ordinato per
    death_date (è l'output di `_confirmed_deaths_for_league`). L'ultimo morto
    è definitivo solo a lega conclusa: prima restituisce None.
    """
    if league is None:
        return None, None
    deaths = list(deaths)
    if not deaths:
        return None, None
    first_pk = deaths[0].pk
    last_pk = deaths[-1].pk if league.end_date < timezone.now().date() else None
    return first_pk, last_pk


# ---------- API pubblica ----------

def league_first_last_death_pks(league):
    """pk del primo e dell'ultimo decesso confermato della lega (vedi _first_last_death_pks)."""
    return _first_last_death_pks(league, _confirmed_deaths_for_league(league))



def _points_for_member_death(member, team, death, league, lb_map, first_pk=None, last_pk=None):
    """Calcola i punti per un singolo (member, death) con la mappa bonus precaricata.

    `first_pk`/`last_pk` identificano il primo e l'ultimo decesso della lega
    (vedi `_first_last_death_pks`): i relativi bonus sono per-lega e non
    dipendono da righe DeathBonus condivise tra leghe.
    """
    raw = _base_points(league) + sum(
        _bonus_points_in_league(b, league, lb_map) for b in death.bonuses.all()
    )
    for lb in lb_map.values():
        dm = lb.bonus_type.detection_method
        if dm == BonusType.DETECTION_ORIGINAL and member.is_original:
            raw += lb.compute_points(age=death.death_age)
        elif dm == BonusType.DETECTION_FIRST_DEATH and death.pk == first_pk:
            raw += lb.compute_points(age=death.death_age)
        elif dm == BonusType.DETECTION_LAST_DEATH and last_pk is not None and death.pk == last_pk:
            raw += lb.compute_points(age=death.death_age)

    multiplier = 1
    if member.is_captain:
        multiplier *= _captain_multiplier(league)
    if team.jolly_month and death.death_date.month == team.jolly_month:
        multiplier *= _jolly_multiplier(league)
    return raw * multiplier


def compute_team_points_for_death(team, death):
    """Punti che `team` guadagna da `death`, o 0 se il decesso non conta per
    questa squadra: persona non in rosa, decesso non confermato, o fuori dalla
    finestra `start_date`/`end_date` della lega (stesso filtro applicato da
    `_confirmed_deaths_for_league` a classifiche e dettaglio squadra — senza
    questa guardia la pagina decesso mostrerebbe punti che il punteggio reale
    non assegna)."""
    if not death.is_confirmed:
        return 0
    league = _league_of(team)
    if league is not None and not (league.start_date <= death.death_date <= league.end_date):
        return 0
    member = _find_member(team, death.person_id)
    if member is None:
        return 0
    first_pk, last_pk = _first_last_death_pks(league, _confirmed_deaths_for_league(league))
    return _points_for_member_death(
        member, team, death, league, _league_bonus_map(league),
        first_pk=first_pk, last_pk=last_pk,
    )


def _bonus_lines_for_death(member, death, league, lb_map, first_pk, last_pk):
    """Righe {name, points} dei bonus grezzi (pre-moltiplicatore) applicati a
    (member, death) in questa lega: sia i `DeathBonus` persistiti attivi nella
    lega, sia i bonus dinamici (originale/primo/ultimo morto) che non hanno una
    riga DeathBonus propria. Pensata per il breakdown mostrato in team_detail:
    senza queste righe i punti "in più" rispetto alla base risultano
    ingiustificati in GUI (es. bonus originalità) o il bonus condiviso tra più
    leghe (es. Covid-19) sembra assente pur contando."""
    lines = []
    for b in death.bonuses.all():
        if b.bonus_type.detection_method in (BonusType.DETECTION_FIRST_DEATH, BonusType.DETECTION_LAST_DEATH):
            # Righe DeathBonus legacy: ignorate dallo scoring (vedi
            # _bonus_points_in_league), i bonus dinamici sotto le sostituiscono.
            continue
        if league is None:
            # Fallback legacy (squadra senza lega): stessa priorità di
            # _bonus_points_in_league quando lb_map è vuota.
            bt = b.bonus_type
            pts = bt.compute_points(age=death.death_age) if bt.points_formula else (
                b.points_awarded if b.points_awarded is not None else bt.points
            )
            lines.append({'name': bt.name, 'points': pts})
            continue
        lb = lb_map.get(b.bonus_type_id)
        if lb is None:
            # Bonus non configurato in questa lega: non conta e non si mostra
            # (coerente con _bonus_points_in_league).
            continue
        lines.append({'name': b.bonus_type.name, 'points': lb.compute_points(age=death.death_age)})
    for lb in lb_map.values():
        dm = lb.bonus_type.detection_method
        if dm == BonusType.DETECTION_ORIGINAL and member.is_original:
            lines.append({'name': lb.bonus_type.name, 'points': lb.compute_points(age=death.death_age)})
        elif dm == BonusType.DETECTION_FIRST_DEATH and death.pk == first_pk:
            lines.append({'name': lb.bonus_type.name, 'points': lb.compute_points(age=death.death_age)})
        elif dm == BonusType.DETECTION_LAST_DEATH and last_pk is not None and death.pk == last_pk:
            lines.append({'name': lb.bonus_type.name, 'points': lb.compute_points(age=death.death_age)})
    return lines


def compute_team_death_details(team):
    league = _league_of(team)
    lb_map = _league_bonus_map(league)
    deaths = list(_confirmed_deaths_for_league(league))
    first_pk, last_pk = _first_last_death_pks(league, deaths)
    details = []
    base = _base_points(league)
    for death in deaths:
        member = _find_member(team, death.person_id)
        if member is None:
            continue
        pts = _points_for_member_death(
            member, team, death, league, lb_map, first_pk=first_pk, last_pk=last_pk,
        )
        if pts == 0:
            continue
        details.append({
            'death': death,
            'member': member,
            'points': pts,
            'base': base,
            'bonuses': list(death.bonuses.all()),
            'bonus_lines': _bonus_lines_for_death(member, death, league, lb_map, first_pk, last_pk),
            'is_captain': member.is_captain,
            'is_original': member.is_original,
            'is_first_death': death.pk == first_pk,
            'is_last_death': last_pk is not None and death.pk == last_pk,
            'jolly': team.jolly_month == death.death_date.month,
            'multiplier': (
                (_captain_multiplier(league) if member.is_captain else 1)
                * (_jolly_multiplier(league) if team.jolly_month == death.death_date.month else 1)
            ),
        })
    return details


def compute_team_total_score(team):
    league = _league_of(team)
    lb_map = _league_bonus_map(league)
    deaths = list(_confirmed_deaths_for_league(league))
    first_pk, last_pk = _first_last_death_pks(league, deaths)
    total = 0
    for death in deaths:
        member = _find_member(team, death.person_id)
        if member is None:
            continue
        total += _points_for_member_death(
            member, team, death, league, lb_map, first_pk=first_pk, last_pk=last_pk,
        )
    return total + team.score_adjustment


def _compute_league_rankings_uncached(league):
    # Come sopra: defer di `claims_cache` sulle persone in rosa (vedi
    # `_confirmed_deaths_for_league`).
    teams = league.teams.select_related('manager').prefetch_related(
        Prefetch(
            'members',
            queryset=TeamMember.objects.select_related('person').defer('person__claims_cache'),
        )
    )
    deaths_list = list(_confirmed_deaths_for_league(league))
    first_pk, last_pk = _first_last_death_pks(league, deaths_list)
    lb_map = _league_bonus_map(league)

    rankings = []
    for team in teams:
        score = 0
        items = []
        members_by_person = {m.person_id: m for m in team.members.all()}
        for death in deaths_list:
            member = members_by_person.get(death.person_id)
            if member is None:
                continue
            pts = _points_for_member_death(
                member, team, death, league, lb_map, first_pk=first_pk, last_pk=last_pk,
            )
            if pts == 0:
                continue
            score += pts
            items.append({
                'death': death,
                'member': member,
                'points': pts,
                'is_captain': member.is_captain,
                'is_original': member.is_original,
                'jolly': team.jolly_month == death.death_date.month,
            })
        score += team.score_adjustment
        rankings.append({'team': team, 'score': score, 'deaths': items})
    rankings.sort(key=lambda x: -x['score'])
    return rankings


def simulate_team_points_for_person(team, person, death_age, death_month=None,
                                    extra_bonus_points=0):
    """Simula i punti che `team` farebbe se `person` morisse oggi con l'età data.

    Pensata per il simulatore "what-if". Non persiste nulla. Se `person` non
    è in squadra ritorna 0.

    death_month (1-12) abilita il moltiplicatore jolly se coincide col mese
    jolly del team. Se None, non considera il jolly.

    extra_bonus_points somma al punteggio grezzo (prima dei moltiplicatori)
    i bonus automatici della lega rilevati dal chiamante, ad es. con
    `_potential_league_bonuses` (Wikidata/età, punti già calcolati per lega).
    """
    league = _league_of(team)
    member = team.members.filter(person=person, replaced_by__isnull=True).first()
    if member is None:
        return 0

    raw = _base_points(league) + extra_bonus_points
    if member.is_original and league is not None:
        for lb in _league_bonus_map(league).values():
            if lb.is_active and lb.bonus_type.detection_method == BonusType.DETECTION_ORIGINAL:
                raw += lb.compute_points(age=death_age)

    multiplier = 1
    if member.is_captain:
        multiplier *= _captain_multiplier(league)
    if death_month is not None and team.jolly_month and death_month == team.jolly_month:
        multiplier *= _jolly_multiplier(league)
    return raw * multiplier


def compute_league_rankings(league, use_cache=True):
    """Classifica completa di una lega.

    Cachata per `_RANKINGS_TTL` secondi e invalidata via versioning quando
    cambiano Death/DeathBonus/LeagueBonus/TeamMember/Team (vedi signals).
    """
    if not use_cache:
        return _compute_league_rankings_uncached(league)
    version = _rankings_version(league.id)
    key = _RANKINGS_DATA_KEY.format(league_id=league.id, version=version)
    cached = cache.get(key)
    if cached is not None:
        return cached
    rankings = _compute_league_rankings_uncached(league)
    cache.set(key, rankings, _RANKINGS_TTL)
    return rankings
