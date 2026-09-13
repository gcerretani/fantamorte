from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import League, LeagueMembership, Team


class LeagueLeaveHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('manager', password='pw')
        self.owner = User.objects.create_user('owner', password='pw')
        self.client.force_login(self.user)

    def _league(self, *, started):
        today = timezone.localdate()
        start = today - timedelta(days=1) if started else today + timedelta(days=1)
        return League.objects.create(
            name=f'Lega {"started" if started else "future"}',
            slug=f'lega-{"started" if started else "future"}',
            owner=self.owner,
            start_date=start,
            end_date=today + timedelta(days=30),
            registration_opens=today - timedelta(days=30),
            registration_closes=start,
        )

    def test_leave_after_start_preserves_team_history(self):
        league = self._league(started=True)
        LeagueMembership.objects.create(league=league, user=self.user)
        team = Team.objects.create(league=league, manager=self.user, name='Storica')

        response = self.client.post(reverse('league_leave', args=[league.slug]))

        self.assertRedirects(response, reverse('home'))
        self.assertFalse(LeagueMembership.objects.filter(league=league, user=self.user).exists())
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())

    def test_leave_before_start_removes_unplayed_team(self):
        league = self._league(started=False)
        LeagueMembership.objects.create(league=league, user=self.user)
        team = Team.objects.create(league=league, manager=self.user, name='Bozza')

        self.client.post(reverse('league_leave', args=[league.slug]))

        self.assertFalse(Team.objects.filter(pk=team.pk).exists())
