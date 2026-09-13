from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import League, LeagueMembership, Team, TeamMember, WikipediaPerson


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
        instant = datetime(2026, 9, 13, 22, 30, tzinfo=dt_timezone.utc)
        league = self._league(datetime(2026, 9, 14).date())
        with patch('django.utils.timezone.now', return_value=instant):
            self.assertTrue(league.has_started())
            self.assertTrue(league.is_registration_open())
            self.assertFalse(league.is_finished())

    def test_end_date_remains_active_until_next_local_day_across_dst(self):
        instant = datetime(2026, 10, 25, 23, 30, tzinfo=dt_timezone.utc)
        league = self._league(datetime(2026, 10, 25).date())
        with patch('django.utils.timezone.now', return_value=instant):
            self.assertTrue(league.is_finished())


class AuthorizationAndInputHardeningTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.owner = User.objects.create_user('auth-owner', password='pw')
        self.admin = User.objects.create_user('auth-admin', password='pw')
        self.other_admin = User.objects.create_user('auth-admin-2', password='pw')
        self.member = User.objects.create_user('auth-member', password='pw')
        self.league = League.objects.create(
            name='Auth league', slug='auth-league', owner=self.owner,
            start_date=today + timedelta(days=2), end_date=today + timedelta(days=30),
            registration_opens=today - timedelta(days=2), registration_closes=today + timedelta(days=1),
        )
        LeagueMembership.objects.create(league=self.league, user=self.owner, role=LeagueMembership.ROLE_OWNER)
        self.admin_membership = LeagueMembership.objects.create(league=self.league, user=self.admin, role=LeagueMembership.ROLE_ADMIN)
        self.other_admin_membership = LeagueMembership.objects.create(league=self.league, user=self.other_admin, role=LeagueMembership.ROLE_ADMIN)
        self.member_membership = LeagueMembership.objects.create(league=self.league, user=self.member, role=LeagueMembership.ROLE_MEMBER)

    def test_league_admin_cannot_remove_peer_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('league_admin', args=[self.league.slug]), {'action': 'remove_member', 'membership_id': self.other_admin_membership.pk})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(LeagueMembership.objects.filter(pk=self.other_admin_membership.pk).exists())

    def test_owner_can_remove_admin(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('league_admin', args=[self.league.slug]), {'action': 'remove_member', 'membership_id': self.other_admin_membership.pk})
        self.assertRedirects(response, reverse('league_admin', args=[self.league.slug]))
        self.assertFalse(LeagueMembership.objects.filter(pk=self.other_admin_membership.pk).exists())

    def test_profile_preferences_rejects_non_boolean_channel_value(self):
        self.client.force_login(self.member)
        response = self.client.post(reverse('profile_preferences'), data='{"prefs":{"death":{"push":"false"}}}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_push_unsubscribe_rejects_non_string_endpoint(self):
        self.client.force_login(self.member)
        response = self.client.post(reverse('push_unsubscribe'), data='{"endpoint":123}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_social_login_requires_post(self):
        self.assertFalse(settings.SOCIALACCOUNT_LOGIN_ON_GET)


class CaptainRuleTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.user = User.objects.create_user('captain-manager', password='pw')
        self.league = League.objects.create(
            name='Captain league', slug='captain-league', owner=self.user,
            start_date=today + timedelta(days=2), end_date=today + timedelta(days=30),
            registration_opens=today - timedelta(days=2), registration_closes=today + timedelta(days=1),
            max_captains=1,
        )
        LeagueMembership.objects.create(league=self.league, user=self.user, role=LeagueMembership.ROLE_OWNER)
        self.team = Team.objects.create(league=self.league, manager=self.user, name='C')
        self.p1 = WikipediaPerson.objects.create(wikidata_id='Q900001', name_it='Uno')
        self.p2 = WikipediaPerson.objects.create(wikidata_id='Q900002', name_it='Due')
        self.m1 = TeamMember.objects.create(team=self.team, person=self.p1, is_captain=True)
        self.m2 = TeamMember.objects.create(team=self.team, person=self.p2, is_captain=False)
        self.client.force_login(self.user)

    def test_invalid_captain_id_keeps_existing_captain(self):
        self.client.post(reverse('team_edit', args=[self.team.pk]), {'captain_id': '999999'})
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertTrue(self.m1.is_captain)
        self.assertFalse(self.m2.is_captain)

    def test_single_captain_switch_happens_after_validation(self):
        self.client.post(reverse('team_edit', args=[self.team.pk]), {'captain_id': str(self.m2.pk)})
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertFalse(self.m1.is_captain)
        self.assertTrue(self.m2.is_captain)

    def test_multiple_captain_league_preserves_existing_captain(self):
        self.league.max_captains = 2
        self.league.save(update_fields=['max_captains'])
        self.client.post(reverse('team_edit', args=[self.team.pk]), {'captain_id': str(self.m2.pk)})
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertTrue(self.m1.is_captain)
        self.assertTrue(self.m2.is_captain)
