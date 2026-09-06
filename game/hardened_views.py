"""View hardening that keeps security policy separate from the legacy UI code.

The base views still contain the product flows.  Subclasses here only add
cross-cutting security/integrity guards and are wired explicitly from urls.py.
Keeping the guards small makes them easier to review and test in isolation.
"""
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect

from . import views
from .models import BonusType, Death, DeathBonus, League, LeagueBonus


def _bonus_is_local_or_exclusive_system(league, bonus_type):
    """Whether a league admin may mutate awards for ``bonus_type``.

    Custom bonus types are intrinsically scoped by ``BonusType.league``.  A
    system bonus has no such scope, so mutating its single ``DeathBonus`` row
    would affect every league using that bonus.  For backwards compatibility
    we still allow a system bonus configured in exactly this league; once the
    same type is active elsewhere, only the global Django admin may change the
    shared award.
    """
    if bonus_type.league_id is not None:
        return bonus_type.league_id == league.pk
    return not LeagueBonus.objects.filter(
        bonus_type_id=bonus_type.pk,
        is_active=True,
    ).exclude(league=league).exists()


class LeagueDeathsView(views.LeagueDeathsView):
    """League death timeline with strict cross-league bonus boundaries."""

    def _death_info(self, league, is_admin):
        info, assignable = super()._death_info(league, is_admin)
        if not is_admin:
            return info, assignable

        # Do the shared-system lookup once for the whole page instead of once
        # per badge/row.
        shared_system_ids = set(
            LeagueBonus.objects.filter(
                is_active=True,
                bonus_type__league__isnull=True,
            )
            .exclude(league=league)
            .values_list('bonus_type_id', flat=True)
        )
        assignable = [
            lb for lb in assignable
            if lb.bonus_type.league_id == league.pk
            or (lb.bonus_type.league_id is None and lb.bonus_type_id not in shared_system_ids)
        ]
        for death_info in info.values():
            for item in death_info.get('bonus_items', []):
                bt = item['bonus'].bonus_type
                if bt.league_id not in (None, league.pk):
                    item['removable'] = False
                elif bt.league_id is None and bt.pk in shared_system_ids:
                    item['removable'] = False
        return info, assignable

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        action = request.POST.get('action', '')

        if action == 'assign_bonus':
            try:
                bonus_type = BonusType.objects.get(
                    pk=int(request.POST.get('bonus_type_id', '')),
                )
            except (BonusType.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Decesso o bonus non valido.')
                return redirect('league_deaths', slug=slug)
            if not _bonus_is_local_or_exclusive_system(league, bonus_type):
                messages.error(
                    request,
                    'Questo bonus di sistema è condiviso tra più leghe e può '
                    'essere modificato solo dal Django admin.',
                )
                return redirect('league_deaths', slug=slug)

        elif action == 'remove_bonus':
            try:
                death_bonus = DeathBonus.objects.select_related(
                    'death__person', 'bonus_type',
                ).get(pk=int(request.POST.get('death_bonus_id', '')))
            except (DeathBonus.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Bonus non trovato.')
                return redirect('league_deaths', slug=slug)

            # Removal must be scoped to exactly the same death set rendered by
            # this league.  Checking only the date range is insufficient when
            # two leagues overlap.
            death_belongs_to_league = Death.objects.filter(
                pk=death_bonus.death_id,
                is_confirmed=True,
                death_date__gte=league.start_date,
                death_date__lte=league.end_date,
                person__team_members__team__league=league,
            ).exists()
            if not death_belongs_to_league:
                messages.error(request, 'Bonus non trovato.')
                return redirect('league_deaths', slug=slug)
            if not _bonus_is_local_or_exclusive_system(league, death_bonus.bonus_type):
                messages.error(
                    request,
                    'Questo bonus è condiviso con un\'altra lega e non può '
                    'essere modificato da qui.',
                )
                return redirect('league_deaths', slug=slug)

        return super().post(request, slug)
