"""Targeted v1 hardening views kept separate from the legacy UI module."""

import json
import re

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from . import hardened_views, views
from .models import League, LeagueMembership, Team


def _claim_has_qid(claims, property_id, qid):
    for claim in (claims or {}).get(property_id, []):
        try:
            value = claim['mainsnak']['datavalue']['value']
        except (KeyError, TypeError):
            continue
        if isinstance(value, dict) and value.get('id') == qid:
            return True
    return False


def _validate_roster_person(person, league):
    """Validate fresh claim data while retaining legacy cached people."""
    claims = person.claims_cache or {}
    # Rows cached before P31 was persisted are grandfathered: forcing a
    # network refresh here would reintroduce I/O inside established roster
    # workflows. New/fresh Wikidata payloads carrying P31 must explicitly be
    # human (Q5); nonexistent QIDs already fail in _get_or_refresh_person().
    if claims.get('P31') and not _claim_has_qid(claims, 'P31', 'Q5'):
        return 'L’entità Wikidata selezionata non risulta essere una persona umana.'
    if league and league.max_total_age and not (person.birth_date or person.birth_year):
        return 'La data di nascita è necessaria per verificare il limite di età della lega.'
    return None


class LeagueLeaveView(LoginRequiredMixin, View):
    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if league.is_owner(request.user):
            messages.error(request, 'Il proprietario non può lasciare la lega. Trasferisci prima la proprietà.')
            return redirect('league_detail', slug=slug)
        membership = LeagueMembership.objects.filter(league=league, user=request.user).first()
        if membership is None:
            messages.info(request, 'Non sei iscritto a questa lega.')
            return redirect('home')
        team = Team.objects.filter(league=league, manager=request.user).first()
        membership.delete()
        if league.has_started():
            messages.success(request, f'Hai lasciato la lega "{league.name}". La tua squadra storica è stata conservata.')
        else:
            if team is not None:
                team.delete()
            messages.success(request, f'Hai lasciato la lega "{league.name}".')
        return redirect('home')


class LeagueAdminView(views.LeagueAdminView):
    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        if request.POST.get('action', '') != 'remove_member':
            return super().post(request, slug)
        try:
            membership_id = int(request.POST.get('membership_id', ''))
            member = league.memberships.select_related('user').get(pk=membership_id)
        except (ValueError, TypeError, LeagueMembership.DoesNotExist):
            messages.error(request, 'Iscrizione non trovata.')
            return redirect('league_admin', slug=slug)
        if member.role == LeagueMembership.ROLE_OWNER:
            messages.error(request, 'Non puoi rimuovere il proprietario.')
            return redirect('league_admin', slug=slug)
        if member.role == LeagueMembership.ROLE_ADMIN and not (
            league.is_owner(request.user) or request.user.is_staff
        ):
            return HttpResponseForbidden('Solo il proprietario può rimuovere un amministratore.')
        team = Team.objects.filter(league=league, manager=member.user).first()
        member.delete()
        if team is not None and not league.has_started():
            team.delete()
        messages.success(request, 'Membro rimosso.')
        return redirect('league_admin', slug=slug)


class ProfilePreferencesView(views.ProfilePreferencesView):
    def post(self, request):
        try:
            data = json.loads(request.body.decode('utf-8')) if request.body else {}
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'status': 'error', 'error': 'JSON non valido'}, status=400)
        if not isinstance(data, dict):
            return JsonResponse({'status': 'error', 'error': 'JSON non valido'}, status=400)
        theme = data.get('theme_preference')
        if theme is not None and not isinstance(theme, str):
            return JsonResponse({'status': 'error', 'error': 'Tema non valido'}, status=400)
        prefs = data.get('prefs')
        if prefs is not None:
            if not isinstance(prefs, dict):
                return JsonResponse({'status': 'error', 'error': 'prefs non valido'}, status=400)
            for category, channels in prefs.items():
                if not isinstance(category, str) or not isinstance(channels, dict):
                    return JsonResponse({'status': 'error', 'error': 'prefs non valido'}, status=400)
                if any(
                    not isinstance(channel, str) or not isinstance(value, bool)
                    for channel, value in channels.items()
                ):
                    return JsonResponse({'status': 'error', 'error': 'Valore preferenza non valido'}, status=400)
        return super().post(request)


class PushUnsubscribeView(views.PushUnsubscribeView):
    def post(self, request):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'JSON non valido'}, status=400)
        if not isinstance(data, dict) or not isinstance(data.get('endpoint'), str):
            return JsonResponse({'error': 'endpoint non valido'}, status=400)
        return super().post(request)


class TeamEditView(views.TeamEditView):
    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            return redirect('team_detail', pk=pk)
        if not views._can_edit_team(team, request.user):
            messages.error(request, 'Non è più possibile modificare la squadra.')
            return redirect('team_edit', pk=pk)
        league = team.league
        name = request.POST.get('name', '').strip()
        jolly_month = request.POST.get('jolly_month', '')
        captain_id = request.POST.get('captain_id', '').strip()
        captain = None
        if captain_id:
            try:
                captain_pk = int(captain_id)
            except (TypeError, ValueError):
                captain_pk = None
            if captain_pk is not None:
                captain = team.members.filter(pk=captain_pk, replaced_by=None).first()
            if captain is None:
                messages.error(request, 'Capitano non valido: nessuna modifica applicata.')
                return redirect('team_edit', pk=pk)
            max_captains = league.max_captains if league else 1
            if max_captains > 1 and not captain.is_captain:
                if team.members.filter(is_captain=True, replaced_by=None).count() >= max_captains:
                    messages.error(request, f'La squadra ha già {max_captains} capitani.')
                    return redirect('team_edit', pk=pk)
        if name:
            team.name = name
        if jolly_month and (not league or league.jolly_enabled):
            try:
                parsed_month = int(jolly_month)
            except (TypeError, ValueError):
                parsed_month = None
            if parsed_month is None or not 1 <= parsed_month <= 12:
                messages.error(request, 'Mese jolly non valido.')
                return redirect('team_edit', pk=pk)
            team.jolly_month = parsed_month
        team.save()
        if captain is not None:
            max_captains = league.max_captains if league else 1
            if max_captains == 1:
                team.members.filter(replaced_by=None).exclude(pk=captain.pk).update(is_captain=False)
            if not captain.is_captain:
                captain.is_captain = True
                captain.save(update_fields=['is_captain'])
        messages.success(request, 'Squadra aggiornata.')
        return redirect('team_edit', pk=pk)


class AddPersonView(hardened_views.AddPersonView):
    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        wikidata_id = request.POST.get('wikidata_id', '').strip()
        if not re.fullmatch(r'Q\d+', wikidata_id):
            return super().post(request, pk)
        person, err = views._get_or_refresh_person(wikidata_id)
        if err:
            return JsonResponse({'error': err}, status=502)
        eligibility_error = _validate_roster_person(person, team.league)
        if eligibility_error:
            return JsonResponse({'error': eligibility_error}, status=400)
        return super().post(request, pk)


class SubstituteMemberView(hardened_views.SubstituteMemberView):
    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        wikidata_id = request.POST.get('wikidata_id', '').strip()
        if not re.fullmatch(r'Q\d+', wikidata_id):
            return super().post(request, pk, member_pk)
        person, err = views._get_or_refresh_person(wikidata_id)
        if err:
            messages.error(request, err)
            return redirect('substitute_member', pk=pk, member_pk=member_pk)
        eligibility_error = _validate_roster_person(person, team.league)
        if eligibility_error:
            messages.error(request, eligibility_error)
            return redirect('substitute_member', pk=pk, member_pk=member_pk)
        return super().post(request, pk, member_pk)
