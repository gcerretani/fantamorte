"""View hardening that keeps security policy separate from the legacy UI code."""
import csv
import json
import re

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect

from . import scoring, views
from .league_bonus_decisions import LeagueDeathBonus
from .models import BonusType, Death, DeathBonus, League, LeagueBonus, Team
from .push_security import UnsafePushEndpoint, validate_push_endpoint


def _csv_safe_text(value):
    """Return spreadsheet-safe text while preserving its visible value."""
    text = '' if value is None else str(value)
    probe = text.lstrip(' \t\r\n')
    if probe.startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r')):
        return "'" + text
    return text


def _league_death(league, death_id):
    return Death.objects.filter(
        pk=death_id,
        is_confirmed=True,
        death_date__gte=league.start_date,
        death_date__lte=league.end_date,
        person__team_members__team__league=league,
    ).distinct().select_related('person').first()


def _prefetch_roster_person(request):
    """Resolve/refresh a roster candidate before acquiring a DB row lock.

    The wrapped legacy views call ``_get_or_refresh_person`` again, but after a
    successful refresh that second call is a fresh-cache hit and therefore does
    not perform network I/O while the team row is locked.
    """
    wikidata_id = request.POST.get('wikidata_id', '').strip()
    if not wikidata_id or not re.fullmatch(r'Q\d+', wikidata_id):
        return None, None
    return views._get_or_refresh_person(wikidata_id)


class LeagueDeathsView(views.LeagueDeathsView):
    """League timeline where league admins only mutate league-scoped awards."""

    def _death_info(self, league, is_admin):
        info, assignable = super()._death_info(league, is_admin)
        lb_map = {
            lb.bonus_type_id: lb
            for lb in league.league_bonuses.filter(is_active=True).select_related('bonus_type')
        }

        # Existing DeathBonus rows are global/legacy facts.  A league admin may
        # still remove a legacy custom row because BonusType.league proves its
        # scope, but system rows are never mutated from a league page.
        for death_info in info.values():
            for item in death_info.get('bonus_items', []):
                item['scope'] = 'global'
                bt = item['bonus'].bonus_type
                item['removable'] = bool(is_admin and bt.league_id == league.pk)

        local_awards = (
            LeagueDeathBonus.objects.filter(league=league, death_id__in=info.keys())
            .select_related('bonus_type')
        )
        for award in local_awards:
            lb = lb_map.get(award.bonus_type_id)
            info.setdefault(award.death_id, {'bonus_items': []})['bonus_items'].append({
                'bonus': award,
                'scope': 'local',
                'active': lb is not None,
                'points': lb.compute_points(age=award.death.death_age) if lb else None,
                'removable': is_admin,
            })
        return info, assignable

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        action = request.POST.get('action', '')

        if action == 'assign_bonus':
            try:
                death_id = int(request.POST.get('death_id', ''))
                bonus_type_id = int(request.POST.get('bonus_type_id', ''))
            except (ValueError, TypeError):
                messages.error(request, 'Decesso o bonus non valido.')
                return redirect('league_deaths', slug=slug)
            death = _league_death(league, death_id)
            try:
                lb = league.league_bonuses.select_related('bonus_type').get(
                    bonus_type_id=bonus_type_id,
                    is_active=True,
                    bonus_type__detection_method__in=self.ASSIGNABLE_METHODS,
                )
            except LeagueBonus.DoesNotExist:
                lb = None
            if death is None or lb is None:
                messages.error(request, 'Decesso o bonus non valido.')
                return redirect('league_deaths', slug=slug)
            bt = lb.bonus_type
            if bt.league_id not in (None, league.pk):
                messages.error(request, 'Questo bonus appartiene a un’altra lega.')
                return redirect('league_deaths', slug=slug)

            # A global fact already applies in this league.  Do not create a
            # redundant local row: this also preserves legacy scores verbatim.
            if DeathBonus.objects.filter(death=death, bonus_type=bt).exists():
                messages.info(request, f'Il bonus "{bt.name}" è già registrato globalmente sul decesso.')
                return redirect('league_deaths', slug=slug)

            award = LeagueDeathBonus.objects.filter(
                league=league, death=death, bonus_type=bt,
            ).first()
            if award is None:
                award = LeagueDeathBonus(
                    league=league,
                    death=death,
                    bonus_type=bt,
                    created_by=request.user,
                    updated_by=request.user,
                    reason='Assegnazione manuale dalla cronologia decessi della lega.',
                )
            else:
                award.updated_by = request.user
                award.reason = 'Assegnazione manuale confermata dalla cronologia decessi della lega.'
            # save() enforces full_clean(), so this path and any future ORM
            # writer share the same model-level league boundary.
            award.save()
            messages.success(request, f'Bonus "{bt.name}" assegnato solo in {league.name}.')
            return redirect('league_deaths', slug=slug)

        if action == 'remove_bonus':
            scope = request.POST.get('bonus_scope', 'global')
            try:
                award_id = int(request.POST.get('death_bonus_id', ''))
            except (ValueError, TypeError):
                messages.error(request, 'Bonus non trovato.')
                return redirect('league_deaths', slug=slug)

            if scope == 'local':
                award = LeagueDeathBonus.objects.select_related('death', 'bonus_type').filter(
                    pk=award_id, league=league,
                ).first()
                if award is None or _league_death(league, award.death_id) is None:
                    messages.error(request, 'Bonus non trovato.')
                    return redirect('league_deaths', slug=slug)
                name = award.bonus_type.name
                award.delete()
                messages.success(request, f'Assegnazione locale "{name}" rimossa.')
                return redirect('league_deaths', slug=slug)

            # Backwards compatibility: legacy DeathBonus rows for a custom
            # BonusType are provably scoped to this league and remain removable.
            legacy = DeathBonus.objects.select_related('death', 'bonus_type').filter(pk=award_id).first()
            if (
                legacy is None
                or _league_death(league, legacy.death_id) is None
                or legacy.bonus_type.league_id != league.pk
            ):
                messages.error(
                    request,
                    'I bonus globali di sistema si correggono dal Django admin; '
                    'un admin di lega non può modificarli.',
                )
                return redirect('league_deaths', slug=slug)
            name = legacy.bonus_type.name
            legacy.delete()
            messages.success(request, f'Bonus legacy "{name}" rimosso dalla lega.')
            return redirect('league_deaths', slug=slug)

        return super().post(request, slug)


class AddPersonView(views.AddPersonView):
    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        # The base view's _can_edit_team() intentionally permits only the team
        # manager. Avoid unnecessary network work for requests that cannot write.
        if team.manager_id != request.user.pk or not views._can_edit_team(team, request.user):
            return super().post(request, pk)

        _, err = _prefetch_roster_person(request)
        if err:
            return JsonResponse({'error': err}, status=500)

        with transaction.atomic():
            Team.objects.select_for_update().get(pk=pk)
            return super().post(request, pk)


class SubstituteMemberView(views.SubstituteMemberView):
    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        # Unlike composition, the base substitution flow explicitly permits
        # staff. Every authorized writer must therefore pass through the same
        # team row lock to close the TOCTOU window.
        if team.manager_id != request.user.pk and not request.user.is_staff:
            return super().post(request, pk, member_pk)

        _, err = _prefetch_roster_person(request)
        if err:
            messages.error(request, err)
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

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
        deaths = list(
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
        local_by_death = {}
        for award in LeagueDeathBonus.objects.filter(
            league=league, death_id__in=[d.pk for d in deaths],
        ).select_related('bonus_type'):
            local_by_death.setdefault(award.death_id, []).append(award)

        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="decessi-{league.slug}.csv"'
        writer = csv.writer(resp)
        writer.writerow(['data', 'nome', 'eta', 'wikidata_id', 'bonus'])
        for death in deaths:
            bonus_names = {
                award.bonus_type.name
                for award in death.bonuses.all()
                if award.bonus_type.league_id in (None, league.pk)
            }
            bonus_names.update(a.bonus_type.name for a in local_by_death.get(death.pk, []))
            writer.writerow([
                death.death_date.isoformat(),
                _csv_safe_text(death.person.name_it),
                death.death_age if death.death_age is not None else '',
                _csv_safe_text(death.person.wikidata_id),
                _csv_safe_text(', '.join(sorted(bonus_names))),
            ])
        return resp
