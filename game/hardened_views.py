"""View hardening that keeps security policy separate from the legacy UI code."""
import csv
import json

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect

from . import scoring, views
from .models import BonusType, Death, DeathBonus, League, LeagueBonus, Team
from .push_security import UnsafePushEndpoint, validate_push_endpoint


def _csv_safe_text(value):
    """Return spreadsheet-safe text while preserving its visible value."""
    text = '' if value is None else str(value)
    probe = text.lstrip(' \t\r\n')
    if probe.startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')):
        return "'" + text
    return text


def _bonus_is_local_or_exclusive_system(league, bonus_type):
    if bonus_type.league_id is not None:
        return bonus_type.league_id == league.pk
    return not LeagueBonus.objects.filter(
        bonus_type_id=bonus_type.pk,
        is_active=True,
    ).exclude(league=league).exists()


class LeagueDeathsView(views.LeagueDeathsView):
    def _death_info(self, league, is_admin):
        info, assignable = super()._death_info(league, is_admin)
        if not is_admin:
            return info, assignable
        shared_system_ids = set(
            LeagueBonus.objects.filter(
                is_active=True,
                bonus_type__league__isnull=True,
            ).exclude(league=league).values_list('bonus_type_id', flat=True)
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
                bonus_type = BonusType.objects.get(pk=int(request.POST.get('bonus_type_id', '')))
            except (BonusType.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Decesso o bonus non valido.')
                return redirect('league_deaths', slug=slug)
            if not _bonus_is_local_or_exclusive_system(league, bonus_type):
                messages.error(request, 'Questo bonus di sistema è condiviso tra più leghe e può essere modificato solo dal Django admin.')
                return redirect('league_deaths', slug=slug)
        elif action == 'remove_bonus':
            try:
                death_bonus = DeathBonus.objects.select_related('death__person', 'bonus_type').get(
                    pk=int(request.POST.get('death_bonus_id', ''))
                )
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
                messages.error(request, 'Questo bonus è condiviso con un altra lega e non può essere modificato da qui.')
                return redirect('league_deaths', slug=slug)
        return super().post(request, slug)


class AddPersonView(views.AddPersonView):
    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager_id != request.user.pk:
            return super().post(request, pk)
        with transaction.atomic():
            Team.objects.select_for_update().get(pk=pk)
            return super().post(request, pk)


class SubstituteMemberView(views.SubstituteMemberView):
    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager_id != request.user.pk:
            return super().post(request, pk, member_pk)
        with transaction.atomic():
            Team.objects.select_for_update().get(pk=pk)
            return super().post(request, pk, member_pk)


def _endpoint_from_json(request, *, rotate=False):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    if rotate:
        subscription = data.get('subscription') or {}
        return subscription.get('endpoint')
    return data.get('endpoint')


class PushSubscribeView(views.PushSubscribeView):
    def post(self, request):
        endpoint = _endpoint_from_json(request)
        if endpoint:
            try:
                validate_push_endpoint(endpoint, resolve=False)
            except UnsafePushEndpoint as exc:
                return JsonResponse({'error': str(exc)}, status=400)
        return super().post(request)


class PushRotateView(views.PushRotateView):
    def post(self, request):
        endpoint = _endpoint_from_json(request, rotate=True)
        if endpoint:
            try:
                validate_push_endpoint(endpoint, resolve=False)
            except UnsafePushEndpoint as exc:
                return JsonResponse({'status': 'error', 'error': str(exc)}, status=400)
        return super().post(request)


class LeagueRankingsCSVView(views.LeagueRankingsCSVView):
    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return HttpResponseForbidden('Non hai accesso a questa lega.')
        rankings = scoring.compute_league_rankings(league)
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="classifica-{league.slug}.csv"'
        writer = csv.writer(resp)
        writer.writerow(['posizione', 'squadra', 'manager', 'punteggio', 'decessi'])
        for pos, row in enumerate(rankings, 1):
            team = row['team']
            writer.writerow([
                pos,
                _csv_safe_text(team.name),
                _csv_safe_text(team.manager.username),
                row['score'],
                len(row['deaths']),
            ])
        return resp


class LeagueDeathsCSVView(views.LeagueDeathsCSVView):
    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return HttpResponseForbidden('Non hai accesso a questa lega.')
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
            .prefetch_related('bonuses__bonus_type')
            .order_by('death_date')
        )
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="decessi-{league.slug}.csv"'
        writer = csv.writer(resp)
        writer.writerow(['data', 'nome', 'eta', 'wikidata_id', 'bonus'])
        for death in deaths:
            bonus_names = ', '.join(
                award.bonus_type.name
                for award in death.bonuses.all()
                if award.bonus_type.league_id in (None, league.pk)
            )
            writer.writerow([
                death.death_date.isoformat(),
                _csv_safe_text(death.person.name_it),
                death.death_age if death.death_age is not None else '',
                _csv_safe_text(death.person.wikidata_id),
                _csv_safe_text(bonus_names),
            ])
        return resp
