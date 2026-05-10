"""Logica punteggio. La League è la sorgente di verità per le regole.

Ogni team ha (o avrà) un puntatore alla league. Le morti che contano sono
quelle confermate avvenute tra `league.start_date` e `league.end_date`.
"""
from .models import Death, BonusType, LeagueBonus


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
    se presenti, altrimenti dei valori del BonusType.
    """
    bt = bonus.bonus_type
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


def _confirmed_deaths_for_league(league):
    qs = Death.objects.filter(is_confirmed=True).select_related('person').prefetch_related('bonuses__bonus_type')
    if league is not None:
        qs = qs.filter(death_date__gte=league.start_date, death_date__lte=league.end_date)
    return qs


def _find_member(team, person_id):
    """Cerca un TeamMember per `person_id` usando la cache prefetchata se disponibile."""
    if 'members' in getattr(team, '_prefetched_objects_cache', {}):
        return next((m for m in team.members.all() if m.person_id == person_id), None)
    return team.members.filter(person_id=person_id).first()


# ---------- API pubblica ----------

def _points_for_member_death(member, team, death, league, lb_map):
    """Calcola i punti per un singolo (member, death) con la mappa bonus precaricata."""
    raw = _base_points(league) + sum(
        _bonus_points_in_league(b, league, lb_map) for b in death.bonuses.all()
    )
    if member.is_original and league is not None:
        for lb in lb_map.values():
            if lb.bonus_type.detection_method == BonusType.DETECTION_ORIGINAL:
                raw += lb.compute_points(age=death.death_age)

    multiplier = 1
    if member.is_captain:
        multiplier *= _captain_multiplier(league)
    if team.jolly_month and death.death_date.month == team.jolly_month:
        multiplier *= _jolly_multiplier(league)
    return raw * multiplier


def compute_team_points_for_death(team, death):
    league = _league_of(team)
    member = _find_member(team, death.person_id)
    if member is None:
        return 0
    return _points_for_member_death(member, team, death, league, _league_bonus_map(league))


def compute_team_death_details(team):
    league = _league_of(team)
    lb_map = _league_bonus_map(league)
    deaths = _confirmed_deaths_for_league(league)
    details = []
    base = _base_points(league)
    for death in deaths:
        member = _find_member(team, death.person_id)
        if member is None:
            continue
        pts = _points_for_member_death(member, team, death, league, lb_map)
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
    lb_map = _league_bonus_map(league)
    deaths = _confirmed_deaths_for_league(league)
    total = 0
    for death in deaths:
        member = _find_member(team, death.person_id)
        if member is None:
            continue
        total += _points_for_member_death(member, team, death, league, lb_map)
    return total


def compute_league_rankings(league):
    """Classifica completa di una lega."""
    teams = league.teams.select_related('manager').prefetch_related('members__person')
    deaths_list = list(_confirmed_deaths_for_league(league))
    lb_map = _league_bonus_map(league)

    rankings = []
    for team in teams:
        score = 0
        items = []
        for death in deaths_list:
            member = _find_member(team, death.person_id)
            if member is None:
                continue
            pts = _points_for_member_death(member, team, death, league, lb_map)
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
