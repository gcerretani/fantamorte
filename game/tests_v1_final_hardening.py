from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
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


@override_settings(TIME_ZONE='Europe/Rome', USE_TZ=True)
class LeaguePhasePolicyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('phase-owner', password='pw')

    def _league(self, day):
        return League.objects.create(
            name='Phase league', slug='phase-league', owner=self.owner,
            start_date=day, end_date=day,
            registration_opens=day, registration_closes=day,
        )

    def test_phase_uses_rome_calendar_date_not_utc_date(self):
        # 22:30 UTC on 13 September is already 00:30 on 14 September in Rome.
        instant = datetime(2026, 9, 13, 22, 30, tzinfo=dt_timezone.utc)
        league = self._league(datetime(2026, 9, 14).date())
        with patch('django.utils.timezone.now', return_value=instant):
            self.assertTrue(league.has_started())
            self.assertTrue(league.is_registration_open())
            self.assertFalse(league.is_finished())

    def test_end_date_remains_active_until_next_local_day_across_dst(self):
        # After the autumn DST switch Rome is UTC+1: 23:30 UTC is 00:30 next day.
        instant = datetime(2026, 10, 25, 23, 30, tzinfo=dt_timezone.utc)
        league = self._league(datetime(2026, 10, 25).date())
        with patch('django.utils.timezone.now', return_value=instant):
            self.assertTrue(league.is_finished())
