"""View hardening that keeps security policy separate from the legacy UI code.

The base views still contain the product flows. Subclasses here only add
cross-cutting security/integrity guards and are wired explicitly from urls.py.
Keeping the guards small makes them easier to review and test in isolation.
"""
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect

from . import views
from .models import BonusType, Death, DeathBonus, League, LeagueBonus, Team


def _bonus_is_local_or_exclusive_system(league, bonus_type):
    """Whether a league admin may mutate awards for ``bonus_type``."""
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


class AddPersonView(views.AddPersonView):
    """Serialize roster additions for a team and make the mutation atomic."""

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        # Reject unauthorized callers before taking a database row lock.
        if team.manager_id != request.user.pk:
            return super().post(request, pk)
        with transaction.atomic():
            # Locking the Team row serializes all concurrent additions for the
            # same roster. super().post re-fetches and re-validates counts,
            # duplicates and age while this lock is held.
            Team.objects.select_for_update().get(pk=pk)
            return super().post(request, pk)


class SubstituteMemberView(views.SubstituteMemberView):
    """Make replacement creation + predecessor update one serialized write."""

    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager_id != request.user.pk:
            return super().post(request, pk, member_pk)
        with transaction.atomic():
            # The team lock serializes replacements and other roster writes.
            # The base view then re-fetches the member and rechecks whether it
            # is still active before creating the replacement.
            Team.objects.select_for_update().get(pk=pk)
            return super().post(request, pk, member_pk)
