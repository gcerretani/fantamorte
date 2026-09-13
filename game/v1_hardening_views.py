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

        # Keep the same historical-integrity rule as voluntary leave: after the
        # game starts, removing access must not erase an already-scored team.
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
