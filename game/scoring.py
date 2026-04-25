from .models import Season, Team, TeamMember, Death, DeathBonus

BASE_POINTS = 50


def _bonus_points(bonus):
    """Punti effettivi di un DeathBonus, supportando formule dinamiche."""
    bt = bonus.bonus_type
    if bt.points_formula:
        age = bonus.death.death_age
        return bt.compute_points(age=age)
    # Se i punti sono salvati esplicitamente sul DeathBonus, prevalgono.
    if bonus.points_awarded is not None:
        return bonus.points_awarded
    return bt.points


def compute_death_bonus_points(death):
    return sum(_bonus_points(b) for b in death.bonuses.all())


def compute_death_raw_points(death):
    return BASE_POINTS + compute_death_bonus_points(death)


def compute_team_points_for_death(team, death):
    member = team.members.filter(person=death.person).first()
    if member is None:
        return 0
    raw = compute_death_raw_points(death)
    # Bonus "giocata originale" applicabile solo se il member era originale
    if member.is_original:
        from .models import BonusType
        original_bonuses = BonusType.objects.filter(
            is_active=True, detection_method=BonusType.DETECTION_ORIGINAL
        )
        for bt in original_bonuses:
            raw += bt.compute_points(age=death.death_age)
    multiplier = 1
    if member.is_captain:
        multiplier *= 2
    if team.jolly_month and death.death_date.month == team.jolly_month:
        multiplier *= 2
    return raw * multiplier


def compute_team_death_details(team):
    confirmed_deaths = (
        Death.objects.filter(season=team.season, is_confirmed=True)
        .select_related('person')
        .prefetch_related('bonuses__bonus_type')
    )
    details = []
    for death in confirmed_deaths:
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
            'base': BASE_POINTS,
            'bonuses': list(death.bonuses.all()),
            'is_captain': member.is_captain,
            'is_original': member.is_original,
            'jolly': team.jolly_month == death.death_date.month,
            'multiplier': (2 if member.is_captain else 1) * (2 if team.jolly_month == death.death_date.month else 1),
        })
    return details


def compute_team_total_score(team):
    confirmed_deaths = (
        Death.objects.filter(season=team.season, is_confirmed=True)
        .select_related('person')
        .prefetch_related('bonuses')
    )
    return sum(compute_team_points_for_death(team, d) for d in confirmed_deaths)


def compute_season_rankings(season):
    teams = season.teams.select_related('manager').prefetch_related(
        'members__person'
    )
    confirmed_deaths = (
        Death.objects.filter(season=season, is_confirmed=True)
        .select_related('person')
        .prefetch_related('bonuses__bonus_type')
    )
    confirmed_deaths_list = list(confirmed_deaths)

    rankings = []
    for team in teams:
        death_details = []
        score = 0
        for death in confirmed_deaths_list:
            member = None
            for m in team.members.all():
                if m.person_id == death.person_id:
                    member = m
                    break
            if member is None:
                continue
            pts = compute_team_points_for_death(team, death)
            if pts == 0:
                continue
            score += pts
            death_details.append({
                'death': death,
                'member': member,
                'points': pts,
                'is_captain': member.is_captain,
                'is_original': member.is_original,
                'jolly': team.jolly_month == death.death_date.month,
            })
        rankings.append({'team': team, 'score': score, 'deaths': death_details})

    rankings.sort(key=lambda x: -x['score'])
    return rankings
