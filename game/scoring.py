"""Logica punteggio. La League è la sorgente di verità per le regole.

Ogni team ha (o avrà) un puntatore alla league. Le morti che contano sono
quelle confermate avvenute tra `league.start_date` e `league.end_date`.
"""
from datetime import date
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


def _league_bonus_for(league, bonus_type):
    """Ritorna il LeagueBonus attivo per (league, bonus_type) o None."""
    if league is None:
        return None
    return LeagueBonus.objects.filter(
        league=league, bonus_type=bonus_type, is_active=True
    ).first()


def _bonus_points_in_league(bonus, league):
    """Punti effettivi di un DeathBonus all'interno di una lega.

    Tiene conto degli override (LeagueBonus.override_points / override_formula)
    se presenti, altrimenti dei valori del BonusType.
    """
    bt = bonus.bonus_type
    age = bonus.death.death_age
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


def _confirmed_deaths_for_league(league):
    qs = Death.objects.filter(is_confirmed=True).select_related('person').prefetch_related('bonuses__bonus_type')
    if league is not None:
        qs = qs.filter(death_date__gte=league.start_date, death_date__lte=league.end_date)
    return qs


# ---------- API pubblica ----------

def compute_team_points_for_death(team, death):
    league = _league_of(team)
    member = team.members.filter(person=death.person).first()
    if member is None:
        return 0
    raw = _base_points(league) + sum(_bonus_points_in_league(b, league) for b in death.bonuses.all())

    # Bonus "giocata originale" (detection_method='original')
    if member.is_original:
        original_lbs = LeagueBonus.objects.filter(
            league=league, is_active=True,
            bonus_type__detection_method=BonusType.DETECTION_ORIGINAL,
        ).select_related('bonus_type') if league else []
        for lb in original_lbs:
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
    details = []
    base = _base_points(league)
    for death in deaths:
        member = team.members.filter(person=death.person).first()
        if member is None:
            continue
        pts = compute_team_points_for_death(team, death)
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
    return sum(compute_team_points_for_death(team, d) for d in deaths)


def compute_league_rankings(league):
    """Classifica completa di una lega."""
    teams = league.teams.select_related('manager').prefetch_related('members__person')
    deaths_list = list(_confirmed_deaths_for_league(league))

    rankings = []
    for team in teams:
        score = 0
        items = []
        for death in deaths_list:
            member = next((m for m in team.members.all() if m.person_id == death.person_id), None)
            if member is None:
                continue
            pts = compute_team_points_for_death(team, death)
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


# Compat: alcune view ancora chiamano compute_season_rankings; usiamo la prima lega "Stagione X" come fallback.
def compute_season_rankings(season):
    from .models import League
    league = League.objects.filter(slug=f'stagione-{season.year}').first()
    if league is None:
        return []
    return compute_league_rankings(league)
