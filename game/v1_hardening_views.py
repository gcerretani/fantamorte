"""Targeted v1 hardening views kept separate from the legacy UI module."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View

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
            # Historical team/roster/scoring data must survive a withdrawal.
            membership.delete()
            messages.success(request, f'Hai lasciato la lega "{league.name}". La tua squadra storica è stata conservata.')
        else:
            membership.delete()
            if team is not None:
                team.delete()
            messages.success(request, f'Hai lasciato la lega "{league.name}".')
        return redirect('home')
