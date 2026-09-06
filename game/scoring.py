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
    if league is None:
        return {}
    return {
        lb.bonus_type_id: lb
        for lb in LeagueBonus.objects.filter(league=league, is_active=True).select_related('bonus_type')
    }


def _bonus_points_in_league(bonus, league, lb_map=None):
    bt = bonus.bonus_type
    if bt.detection_method in (BonusType.DETECTION_FIRST_DEATH, BonusType.DETECTION_LAST_DEATH):
        return 0
    age = bonus.death.death_age
    if league is not None:
        if lb_map is None:
            lb_map = _league_bonus_map(league)
        lb = lb_map.get(bt.id)
        if lb is None:
            return 0
        return lb.compute_points(age=age)
    if bt.points_formula:
        return bt.compute_points(age=age)
    if bonus.points_awarded is not None:
        return bonus.points_awarded
    return bt.points


_RANKINGS_VERSION_KEY = 'league_rankings_version:{league_id}'
_RANKINGS_DATA_KEY = 'league_rankings:{league_id}:v{version}'
_RANKINGS_TTL = 300


def _rankings_version(league_id):
    key = _RANKINGS_VERSION_KEY.format(league_id=league_id)
    v = cache.get(key)
    if v is None:
        v = int(time.time())
        cache.set(key, v, None)
    return v


def invalidate_league_rankings(league_id):
    if league_id is None:
        return
    cache.set(_RANKINGS_VERSION_KEY.format(league_id=league_id), int(time.time() * 1000), None)


def league_cache_version(league_id):
    return _rankings_version(league_id)


def invalidate_person_bonus_caches(person):
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
    if 'members' in getattr(team, '_prefetched_objects_cache', {}):
        return next((m for m in team.members.all() if m.person_id == person_id), None)
    return team.members.filter(person_id=person_id).first()


def _first_last_death_pks(league, deaths):
    if league is None:
        return None, None
    deaths = list(deaths)
    if not deaths:
        return None, None
    first_pk = deaths[0].pk
    last_pk = deaths[-1].pk if league.end_date < timezone.now().date() else None
    return first_pk, last_pk


def league_first_last_death_pks(league):
    return _first_last_death_pks(league, _confirmed_deaths_for_league(league))


def _member_was_eligible_on_death(member, death, league):
    """Return whether the person was already on this roster on death day.

    Death currently has day precision only, therefore an addition on the same
    calendar day remains eligible: without an event time we cannot establish
    that the pick happened after the death.

    Historical/imported rosters may have ``added_at`` set when they were
    imported, even years after the league ended. Such a timestamp cannot be
    treated as the original pick time, so dates after ``league.end_date`` are
    deliberately grandfathered. Current-season additions always fall inside
    the league window and are checked normally.
    """
    if league is None or not member.added_at:
        return True
    picked_on = timezone.localtime(member.added_at).date()
    if picked_on > league.end_date:
        return True
    return picked_on <= death.death_date


def _points_for_member_death(member, team, death, league, lb_map, first_pk=None, last_pk=None):
    if not _member_was_eligible_on_death(member, death, league):
        return 0
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
    lines = []
    for b in death.bonuses.all():
        if b.bonus_type.detection_method in (BonusType.DETECTION_FIRST_DEATH, BonusType.DETECTION_LAST_DEATH):
            continue
        if league is None:
            bt = b.bonus_type
            pts = bt.compute_points(age=death.death_age) if bt.points_formula else (
                b.points_awarded if b.points_awarded is not None else bt.points
            )
            lines.append({'name': bt.name, 'points': pts})
            continue
        lb = lb_map.get(b.bonus_type_id)
        if lb is None:
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
