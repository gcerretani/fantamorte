from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    Death, League, LeagueMembership, Notification, Team, TeamMember,
    WikipediaPerson,
)
from .notifications import leagues_for_death
from .push import broadcast_death_notification

User = get_user_model()


class DeathNotificationLeagueScopeTest(TestCase):
    """Un decesso riguarda solo le leghe in cui il morto era davvero in rosa."""

    def setUp(self):
        self.user_a = User.objects.create_user('user-a', password='x')
        self.user_b = User.objects.create_user('user-b', password='x')
        self.owner_a = User.objects.create_user('owner-a', password='x')
        self.owner_b = User.objects.create_user('owner-b', password='x')

        common = dict(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            registration_opens=date(2024, 12, 1),
            registration_closes=date(2025, 1, 1),
        )
        self.league_a = League.objects.create(
            name='Lega A', slug='lega-a-scope', owner=self.owner_a, **common,
        )
        self.league_b = League.objects.create(
            name='Lega B', slug='lega-b-scope', owner=self.owner_b, **common,
        )
        LeagueMembership.objects.create(
            league=self.league_a, user=self.owner_a, role=LeagueMembership.ROLE_OWNER,
        )
        LeagueMembership.objects.create(
            league=self.league_a, user=self.user_a, role=LeagueMembership.ROLE_MEMBER,
        )
        LeagueMembership.objects.create(
            league=self.league_b, user=self.owner_b, role=LeagueMembership.ROLE_OWNER,
        )
        LeagueMembership.objects.create(
            league=self.league_b, user=self.user_b, role=LeagueMembership.ROLE_MEMBER,
        )

        self.team_a = Team.objects.create(
            name='Team A', manager=self.owner_a, league=self.league_a,
        )
        self.team_b = Team.objects.create(
            name='Team B', manager=self.owner_b, league=self.league_b,
        )
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q-SCOPE', name_it='Persona Scope',
            birth_date=date(1940, 1, 1), is_dead=False,
        )

    def _death(self):
        self.person.is_dead = True
        self.person.save(update_fields=['is_dead'])
        return Death.objects.create(
            person=self.person,
            death_date=date(2025, 6, 1),
            death_age=85,
            is_confirmed=True,
        )

    def test_other_overlapping_league_does_not_receive_death(self):
        """La contemporaneita' temporale da sola non rende una lega interessata."""
        TeamMember.objects.create(team=self.team_b, person=self.person)

        death = self._death()

        self.assertEqual(leagues_for_death(death), [self.league_b])
        self.assertFalse(
            Notification.objects.filter(user=self.user_a, death=death).exists()
        )
        self.assertFalse(
            Notification.objects.filter(user=self.owner_a, death=death).exists()
        )
        self.assertTrue(
            Notification.objects.filter(user=self.user_b, death=death).exists()
        )
        owner_b_notification = Notification.objects.get(user=self.owner_b, death=death)
        self.assertEqual(owner_b_notification.kind, Notification.KIND_DEATH_TEAM)
        self.assertTrue(owner_b_notification.is_urgent)

    def test_all_members_of_involved_league_receive_event(self):
        """Se il morto e' in una rosa della lega, l'evento riguarda tutta la lega."""
        TeamMember.objects.create(team=self.team_a, person=self.person)

        death = self._death()

        self.assertEqual(leagues_for_death(death), [self.league_a])
        owner_notification = Notification.objects.get(user=self.owner_a, death=death)
        member_notification = Notification.objects.get(user=self.user_a, death=death)
        self.assertEqual(owner_notification.kind, Notification.KIND_DEATH_TEAM)
        self.assertEqual(member_notification.kind, Notification.KIND_DEATH)
        self.assertFalse(
            Notification.objects.filter(user__in=[self.owner_b, self.user_b], death=death).exists()
        )

    def test_replaced_member_does_not_make_league_involved(self):
        """Un personaggio gia' sostituito non deve riattivare notifiche di lega."""
        replacement = WikipediaPerson.objects.create(
            wikidata_id='Q-REPLACEMENT', name_it='Sostituto',
            birth_date=date(1950, 1, 1), is_dead=False,
        )
        old_member = TeamMember.objects.create(team=self.team_a, person=self.person)
        new_member = TeamMember.objects.create(team=self.team_a, person=replacement)
        old_member.replaced_by = new_member
        old_member.save(update_fields=['replaced_by'])
        TeamMember.objects.create(team=self.team_b, person=self.person)

        death = self._death()

        self.assertEqual(leagues_for_death(death), [self.league_b])
        self.assertFalse(
            Notification.objects.filter(user__in=[self.owner_a, self.user_a], death=death).exists()
        )

    @patch('game.push.send_push', return_value=True)
    def test_push_broadcast_uses_same_scoped_leagues(self, send_push):
        """Il push non deve allargare di nuovo il perimetro oltre il feed."""
        from .models import PushSubscription

        TeamMember.objects.create(team=self.team_b, person=self.person)
        for user, suffix in ((self.user_a, 'a'), (self.user_b, 'b')):
            PushSubscription.objects.create(
                user=user,
                endpoint=f'https://push.example.com/{suffix}',
                p256dh='p256dh', auth='auth',
            )

        death = self._death()
        send_push.reset_mock()

        sent = broadcast_death_notification(death)

        self.assertEqual(sent, 1)
        self.assertEqual(send_push.call_count, 1)
        called_subscription = send_push.call_args.args[0]
        self.assertEqual(called_subscription.user_id, self.user_b.pk)
