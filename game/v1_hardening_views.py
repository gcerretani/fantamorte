"""Targeted v1 hardening views kept separate from the legacy UI module."""

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from . import views
from .models import League, LeagueMembership, Team


class LeagueLeaveView(LoginRequiredMixin, View):
    """Leave a league without deleting competitive history after it starts."""

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
        if league.has_started():
            membership.delete()
            messages.success(request, f'Hai lasciato la lega "{league.name}". La tua squadra storica è stata conservata.')
        else:
            membership.delete()
            if team is not None:
                team.delete()
            messages.success(request, f'Hai lasciato la lega "{league.name}".')
        return redirect('home')


class LeagueAdminView(views.LeagueAdminView):
    """Apply one permission matrix to role changes and member removal."""

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')

        action = request.POST.get('action', '')
        if action != 'remove_member':
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

        actor_can_manage_roles = league.is_owner(request.user) or request.user.is_staff
        if member.role == LeagueMembership.ROLE_ADMIN and not actor_can_manage_roles:
            return HttpResponseForbidden('Solo il proprietario può rimuovere un amministratore.')

        team = Team.objects.filter(league=league, manager=member.user).first()
        member.delete()
        if team is not None and not league.has_started():
            team.delete()
        messages.success(request, 'Membro rimosso.')
        return redirect('league_admin', slug=slug)


class ProfilePreferencesView(views.ProfilePreferencesView):
    """Reject malformed JSON types instead of coercing them or raising 500s."""

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
                if any(not isinstance(channel, str) or not isinstance(value, bool)
                       for channel, value in channels.items()):
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
    """Keep captain editing consistent with League.max_captains and history."""

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
                active_captains = team.members.filter(is_captain=True, replaced_by=None).count()
                if active_captains >= max_captains:
                    messages.error(request, f'La squadra ha già {max_captains} capitani.')
                    return redirect('team_edit', pk=pk)

        if name:
            team.name = name
        if jolly_month and (not league or league.jolly_enabled):
            try:
                parsed_month = int(jolly_month)
            except (TypeError, ValueError):
                parsed_month = None
            if parsed_month is not None and 1 <= parsed_month <= 12:
                team.jolly_month = parsed_month
            else:
                messages.error(request, 'Mese jolly non valido.')
                return redirect('team_edit', pk=pk)
        team.save()

        if captain is not None:
            max_captains = league.max_captains if league else 1
            if max_captains == 1:
                # Only active rows participate in the current captain rule.
                # Historical/replaced captain flags must stay untouched because
                # scoring uses the flag stored on the member that died.
                team.members.filter(replaced_by=None).exclude(pk=captain.pk).update(is_captain=False)
            if not captain.is_captain:
                captain.is_captain = True
                captain.save(update_fields=['is_captain'])

        messages.success(request, 'Squadra aggiornata.')
        return redirect('team_edit', pk=pk)
