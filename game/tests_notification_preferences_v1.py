from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import notifications as notif
from .models import League, LeagueMembership, Notification, PushSubscription, Team, TeamMember, WikipediaPerson

User = get_user_model()


class PerEventPreferenceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('prefs-v1', email='prefs@example.com', password='x')

    def test_matrix_has_exactly_one_row_per_notification_kind(self):
        keys = [row['key'] for row in notif.NOTIFICATION_CATEGORIES]
        self.assertEqual(len(keys), 8)
        self.assertEqual(set(keys), {choice[0] for choice in Notification.KIND_CHOICES})

    def test_new_profile_has_expanded_event_preferences(self):
        prefs = self.user.profile.notification_prefs
        for kind in {choice[0] for choice in Notification.KIND_CHOICES}:
            self.assertIn(kind, prefs)
            self.assertIn('push', prefs[kind])
            self.assertIn('email', prefs[kind])

    def test_legacy_preferences_expand_without_losing_user_choices(self):
        legacy = {
            'death': {'push': False, 'email': True},
            'substitution': {'push': True, 'email': False},
            'league_joined': {'push': True, 'email': False},
            'league_events': {'push': False, 'email': True},
        }
        expanded = notif.expand_legacy_notification_prefs(legacy)
        self.assertEqual(expanded[Notification.KIND_DEATH_TEAM], legacy['death'])
        self.assertEqual(expanded[Notification.KIND_SUBSTITUTION], legacy['substitution'])
        self.assertEqual(expanded[Notification.KIND_PRESEASON_REMOVED], legacy['substitution'])
        self.assertEqual(expanded[Notification.KIND_LEAGUE_STARTED], legacy['league_events'])
        self.assertEqual(expanded[Notification.KIND_LEAGUE_ENDED], legacy['league_events'])
        self.assertEqual(expanded[Notification.KIND_TEAM_LOCKED], legacy['league_events'])

    def test_death_and_death_team_are_independent(self):
        prefs = dict(self.user.profile.notification_prefs)
        prefs[Notification.KIND_DEATH] = {'push': False, 'email': False}
        prefs[Notification.KIND_DEATH_TEAM] = {'push': True, 'email': True}
        self.user.profile.notification_prefs = prefs
        self.user.profile.save(update_fields=['notification_prefs'])
        self.assertFalse(notif.wants(self.user, Notification.KIND_DEATH, 'push'))
        self.assertTrue(notif.wants(self.user, Notification.KIND_DEATH_TEAM, 'push'))

    def test_profile_endpoint_accepts_event_key_and_rejects_legacy_group(self):
        self.client.force_login(self.user)
        url = reverse('profile_preferences')
        ok = self.client.post(
            url,
            data='{"prefs":{"death_team":{"push":false}}}',
            content_type='application/json',
        )
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post(
            url,
            data='{"prefs":{"league_events":{"push":true}}}',
            content_type='application/json',
        )
        self.assertEqual(bad.status_code, 400)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Fantamorte <noreply@example.com>',
    SITE_BASE_URL='https://fantamorte.test',
)
class EventChannelParityTest(TestCase):
    def setUp(self):
        today = timezone.now().date()
        self.owner = User.objects.create_user('owner-v1', email='owner@example.com', password='x')
        self.member = User.objects.create_user('member-v1', email='member@example.com', password='x')
        self.league = League.objects.create(
            name='Lega V1', slug='lega-v1', owner=self.owner,
            start_date=today - timedelta(days=2), end_date=today + timedelta(days=30),
            registration_opens=today - timedelta(days=30), registration_closes=today - timedelta(days=1),
        )
        LeagueMembership.objects.create(league=self.league, user=self.owner, role='owner')
        LeagueMembership.objects.create(league=self.league, user=self.member, role='member')
        self.team = Team.objects.create(name='Team V1', manager=self.owner, league=self.league)
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q900001', name_it='Persona V1', birth_date=date(1940, 1, 1),
        )
        TeamMember.objects.create(team=self.team, person=self.person)

    def _set(self, user, kind, push=False, email=False):
        prefs = dict(user.profile.notification_prefs)
        prefs[kind] = {'push': push, 'email': email}
        user.profile.notification_prefs = prefs
        user.profile.save(update_fields=['notification_prefs'])

    @patch('game.push.send_push', return_value=True)
    def test_death_generic_and_team_push_can_be_configured_separately(self, mock_send):
        PushSubscription.objects.create(
            user=self.owner, endpoint='https://push.example.com/owner', p256dh='x', auth='y'
        )
        PushSubscription.objects.create(
            user=self.member, endpoint='https://push.example.com/member', p256dh='x', auth='y'
        )
        self._set(self.owner, Notification.KIND_DEATH_TEAM, push=False)
        self._set(self.member, Notification.KIND_DEATH, push=True)
        self.person.is_dead = True
        self.person.save(update_fields=['is_dead'])
        from .models import Death
        Death.objects.create(
            person=self.person, death_date=timezone.now().date(), is_confirmed=True,
        )
        called_users = {call.args[0].user_id for call in mock_send.call_args_list}
        self.assertNotIn(self.owner.pk, called_users)
        self.assertIn(self.member.pk, called_users)

    def test_joined_event_email_is_independent(self):
        self._set(self.owner, Notification.KIND_LEAGUE_JOINED, email=True)
        mail.outbox.clear()
        newcomer = User.objects.create_user('new-v1', password='x')
        LeagueMembership.objects.create(league=self.league, user=newcomer, role='member')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['owner@example.com'])

    def test_team_locked_email_respects_opt_in(self):
        self._set(self.owner, Notification.KIND_TEAM_LOCKED, email=True)
        mail.outbox.clear()
        self.team.is_locked = True
        self.team.save(update_fields=['is_locked'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('bloccata', mail.outbox[0].subject)

    @patch('game.push.send_league_lifecycle_push')
    @patch('game.email.send_event_email')
    def test_lifecycle_channels_are_emitted_once_with_feed(self, mock_email, mock_push):
        Notification.objects.filter(kind=Notification.KIND_LEAGUE_STARTED).delete()
        created = notif.emit_league_lifecycle_notifications(
            self.league, Notification.KIND_LEAGUE_STARTED
        )
        self.assertEqual(created, 2)
        self.assertEqual(mock_push.call_count, 2)
        self.assertEqual(mock_email.call_count, 2)
        created_again = notif.emit_league_lifecycle_notifications(
            self.league, Notification.KIND_LEAGUE_STARTED
        )
        self.assertEqual(created_again, 0)
        self.assertEqual(mock_push.call_count, 2)
        self.assertEqual(mock_email.call_count, 2)
