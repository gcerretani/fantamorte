"""Logica punteggio. La League è la sorgente di verità per le regole.

Ogni team ha (o avrà) un puntatore alla league. Le morti che contano sono
quelle confermate avvenute tra `league.start_date` e `league.end_date`.
"""
import time

from django.core.cache import cache

from .models import Death, Team, TeamMember, BonusType, LeagueBonus


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
    """Tutti i LeagueBonus attivi di una lega indicizzati per bonus_type_id.

    Una sola query DB per lega: usato sia dalla classifica sia dal calcolo
    per singolo team per evitare il pattern N+1 di una query per bonus.
    """
    if league is None:
        return {}
    return {
        lb.bonus_type_id: lb
        for lb in LeagueBonus.objects.filter(league=league, is_active=True)
        .select_related('bonus_type')
    }


def _league_bonus_for(league, bonus_type):
    """Ritorna il LeagueBonus attivo per (league, bonus_type) o None."""
    if league is None:
        return None
    return _league_bonus_map(league).get(bonus_type.id)


def _bonus_points_in_league(bonus, league, lb_map=None):
    """Punti effettivi di un DeathBonus all'interno di una lega.

    Tiene conto degli override (LeagueBonus.override_points / override_formula)
    se presenti, altrimenti dei valori del BonusType. Se passato `lb_map`
    (dict bonus_type_id -> LeagueBonus) lo usa per evitare query.
    """
    bt = bonus.bonus_type
    age = bonus.death.death_age
    if lb_map is not None:
        lb = lb_map.get(bt.id)
    else:
        lb = _league_bonus_for(league, bt)
    if lb is None:
        # Se la lega esiste ma il bonus non è tra quelli configurati, esclude
        # il bonus (regola: una lega usa solo i bonus che si è scelta).
        if league is not None:
            return 0
        # Fallback legacy: usa i punti del BonusType
        if bt.points_formula:
            return bt.compute_points(age=age)
        if bonus.points_awarded is not None:
            return bonus.points_awarded
        return bt.points
    return lb.compute_points(age=age)


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
    il punteggio (Death, DeathBonus, LeagueBonus, TeamMember, Team).
    """
    if league_id is None:
        return
    cache.set(_RANKINGS_VERSION_KEY.format(league_id=league_id), int(time.time() * 1000), None)


def _confirmed_deaths_for_league(league):
    qs = Death.objects.filter(is_confirmed=True).select_related('person').prefetch_related('bonuses__bonus_type')
    if league is not None:
        qs = qs.filter(death_date__gte=league.start_date, death_date__lte=league.end_date)
    return qs


# ---------- API pubblica ----------

def compute_team_points_for_death(team, death, lb_map=None):
    league = _league_of(team)
    member = team.members.filter(person=death.person).first()
    if member is None:
        return 0
    if lb_map is None:
        lb_map = _league_bonus_map(league)
    raw = _base_points(league) + sum(
        _bonus_points_in_league(b, league, lb_map=lb_map) for b in death.bonuses.all()
    )

    # Bonus "giocata originale" (detection_method='original')
    if member.is_original and league is not None:
        for lb in lb_map.values():
            if lb.is_active and lb.bonus_type.detection_method == BonusType.DETECTION_ORIGINAL:
                raw += lb.compute_points(age=death.death_age)

    multiplier = 1
    if member.is_captain:
        multiplier *= _captain_multiplier(league)
    if team.jolly_month and death.death_date.month == team.jolly_month:
        multiplier *= _jolly_multiplier(league)
    return raw * multiplier


def compute_team_death_details(team):
    league = _league_of(team)
    deaths = _confirmed_deaths_for_league(league)
    lb_map = _league_bonus_map(league)
    details = []
    base = _base_points(league)
    for death in deaths:
        member = team.members.filter(person=death.person).first()
        if member is None:
            continue
        pts = compute_team_points_for_death(team, death, lb_map=lb_map)
        if pts == 0:
            continue
        details.append({
            'death': death,
            'member': member,
            'points': pts,
            'base': base,
            'bonuses': list(death.bonuses.all()),
            'is_captain': member.is_captain,
            'is_original': member.is_original,
            'jolly': team.jolly_month == death.death_date.month,
            'multiplier': (
                (_captain_multiplier(league) if member.is_captain else 1)
                * (_jolly_multiplier(league) if team.jolly_month == death.death_date.month else 1)
            ),
        })
    return details


def compute_team_total_score(team):
    league = _league_of(team)
    deaths = _confirmed_deaths_for_league(league)
    lb_map = _league_bonus_map(league)
    return sum(compute_team_points_for_death(team, d, lb_map=lb_map) for d in deaths)


def _compute_league_rankings_uncached(league):
    teams = league.teams.select_related('manager').prefetch_related('members__person')
    deaths_list = list(_confirmed_deaths_for_league(league))
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
            pts = compute_team_points_for_death(team, death, lb_map=lb_map)
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
        rankings.append({'team': team, 'score': score, 'deaths': items})
    rankings.sort(key=lambda x: -x['score'])
    return rankings


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
