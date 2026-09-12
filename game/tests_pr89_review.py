from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import WikidataPersonAdmin
from .league_bonus_decisions import LeagueDeathBonus
from .models import BonusType, Death, League, Team, TeamMember, WikipediaPerson
from .push_security import UnsafePushEndpoint, validate_push_endpoint
from .tests_views import ViewsBaseTestCase

User = get_user_model()


class PushEndpointBoundaryTest(TestCase):
    def test_arbitrary_public_hostname_is_rejected_before_dns(self):
        with patch('game.push_security.socket.getaddrinfo') as resolver:
            with self.assertRaises(UnsafePushEndpoint):
                validate_push_endpoint('https://attacker.example/push', resolve=True)
        resolver.assert_not_called()

    def test_known_provider_hosts_are_accepted_structurally(self):
        for endpoint in (
            'https://fcm.googleapis.com/fcm/send/token',
            'https://updates.push.services.mozilla.com/wpush/v2/token',
            'https://web.push.apple.com/token',
            'https://foo.push.apple.com/token',
        ):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(validate_push_endpoint(endpoint), endpoint)

    @override_settings(WEBPUSH_ALLOWED_HOSTS=['push.example'])
    def test_operator_can_add_an_explicit_provider(self):
        endpoint = 'https://push.example/token'
        self.assertEqual(validate_push_endpoint(endpoint), endpoint)


class FrozenAdminRefreshTest(TestCase):
    def test_admin_refresh_forces_frozen_person(self):
        person = WikipediaPerson.objects.create(
            wikidata_id='Q990100', name_it='Frozen', data_frozen=True,
        )
        request = RequestFactory().post('/admin/game/wikipediaperson/')
        request.user = User.objects.create_superuser(
            'root-review', 'root-review@example.com', 'x',
        )
        model_admin = WikidataPersonAdmin(WikipediaPerson, admin.site)
        model_admin.message_user = MagicMock()
        entity = {'name_it': 'Frozen aggiornato', 'claims_cache': {}}

        with patch('wikidata_api.client.WikidataClient.get_entity', return_value=entity), \
             patch('game.admin.person_sync.sync_person_from_entity') as sync:
            model_admin.refresh_from_wikidata(
                request, WikipediaPerson.objects.filter(pk=person.pk),
            )

        self.assertEqual(sync.call_count, 1)
        self.assertTrue(sync.call_args.kwargs['force'])
        model_admin.message_user.assert_called()


class LeagueDeathBonusValidationTest(TestCase):
    def test_cross_league_custom_bonus_is_rejected_on_save(self):
        owner = User.objects.create_user('owner-review', password='x')
        league_a = League.objects.create(
            name='Review A', slug='review-a', owner=owner,
            start_date=date(2020, 1, 1), end_date=date(2030, 1, 1),
            registration_opens=date(2019, 1, 1), registration_closes=date(2020, 1, 1),
        )
        league_b = League.objects.create(
            name='Review B', slug='review-b', owner=owner,
            start_date=date(2020, 1, 1), end_date=date(2030, 1, 1),
            registration_opens=date(2019, 1, 1), registration_closes=date(2020, 1, 1),
        )
        person = WikipediaPerson.objects.create(
            wikidata_id='Q990101', name_it='Dead', is_dead=True,
        )
        death = Death.objects.create(
            person=person, death_date=date(2025, 1, 1), is_confirmed=True,
        )
        custom = BonusType.objects.create(
            name='Only A', league=league_a, points=5,
            detection_method=BonusType.DETECTION_MANUAL,
        )
        award = LeagueDeathBonus(
            league=league_b, death=death, bonus_type=custom,
        )
        with self.assertRaises(ValidationError):
            award.save()
        self.assertFalse(LeagueDeathBonus.objects.exists())


class RosterLockReviewTest(ViewsBaseTestCase):
    def _prepare_dead_member(self):
        self.person.is_dead = True
        self.person.death_date = timezone.now().date()
        self.person.save()
        Death.objects.create(
            person=self.person,
            death_date=timezone.now().date(),
            is_confirmed=True,
            confirmed_at=timezone.now(),
        )
        return self.private_team.members.get(person=self.person)

    def test_staff_substitution_uses_team_row_lock(self):
        member = self._prepare_dead_member()
        candidate = WikipediaPerson.objects.create(
            wikidata_id='Q990102', name_it='Fresh candidate',
            is_dead=False, last_checked=timezone.now(),
        )
        staff = User.objects.create_user(
            'staff-review', password='x', is_staff=True,
        )
        self.client.login(username='staff-review', password='x')

        original = Team.objects.select_for_update
        with patch.object(Team.objects, 'select_for_update', wraps=original) as locked:
            response = self.client.post(
                reverse('substitute_member', args=[self.private_team.pk, member.pk]),
                {'wikidata_id': candidate.wikidata_id},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(locked.called)
        member.refresh_from_db()
        self.assertIsNotNone(member.replaced_by_id)

    def test_wikidata_network_refresh_happens_before_team_transaction(self):
        # Composition is open in the shared fixture. Use a stale candidate so
        # the hardening wrapper must refresh it before acquiring select_for_update.
        candidate = WikipediaPerson.objects.create(
            wikidata_id='Q990103', name_it='Stale candidate',
            is_dead=False, last_checked=None,
        )
        self.client.login(username='member', password='x')

        entity = {
            'name_it': 'Stale candidate', 'name_en': '', 'description_it': '',
            'birth_date': None, 'birth_year': None,
            'death_date': None, 'death_year': None,
            'image_url': '', 'occupation': '', 'nationality': '',
            'wikipedia_url_it': '', 'claims_cache': {},
        }

        def get_entity(_qid):
            self.assertFalse(
                connection.in_atomic_block,
                'Wikidata network I/O must happen before the roster row lock',
            )
            return entity

        with patch('game.views.WikidataClient.get_entity', side_effect=get_entity) as fetch:
            response = self.client.post(
                reverse('add_person', args=[self.private_team.pk]),
                {'wikidata_id': candidate.wikidata_id},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(
            self.private_team.members.filter(person=candidate, replaced_by=None).exists()
        )
