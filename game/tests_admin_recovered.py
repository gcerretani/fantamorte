"""Regression tests recovered from the historical coverage branch.

These tests focus on Django admin actions that remain part of the v1 surface.
External side effects are mocked so the suite never touches the network.
"""
from datetime import date
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .admin import DeathAdmin, WikidataPersonAdmin
from .models import BonusType, Death, DeathBonus, WikipediaPerson

User = get_user_model()


def _admin_request(user):
    request = RequestFactory().post('/admin/')
    request.user = user
    request.session = 'session'
    request._messages = FallbackStorage(request)
    return request


class AdminActionsBaseTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staff-recovered', password='x', is_staff=True)
        self.request = _admin_request(self.staff)
        self.site = AdminSite()
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q1339001', name_it='Johann Sebastian Bach',
            birth_date=date(1950, 3, 21), is_dead=False,
        )


class ConfirmDeathsActionRecoveredTest(AdminActionsBaseTestCase):
    def setUp(self):
        super().setUp()
        self.death = Death.objects.create(
            person=self.person, death_date=timezone.now().date(), is_confirmed=False,
        )
        self.admin = DeathAdmin(Death, self.site)

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    def test_confirm_sets_metadata_and_notifies_once(self, mock_email, mock_push):
        self.admin.confirm_deaths(self.request, Death.objects.all())

        self.death.refresh_from_db()
        self.person.refresh_from_db()
        self.assertTrue(self.death.is_confirmed)
        self.assertEqual(self.death.confirmed_by, self.staff)
        self.assertIsNotNone(self.death.confirmed_at)
        self.assertTrue(self.person.is_dead)
        mock_push.assert_called_once()
        mock_email.assert_called_once()

        # A second execution must not notify again.
        self.admin.confirm_deaths(self.request, Death.objects.all())
        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_email.call_count, 1)


class UnconfirmDeathsActionRecoveredTest(AdminActionsBaseTestCase):
    def setUp(self):
        super().setUp()
        with patch('game.push.broadcast_death_notification'), \
                patch('game.email.broadcast_death_email'):
            self.death = Death.objects.create(
                person=self.person, death_date=timezone.now().date(),
                is_confirmed=True, confirmed_at=timezone.now(), confirmed_by=self.staff,
            )
        self.admin = DeathAdmin(Death, self.site)

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    def test_unconfirm_clears_confirmation_without_notifications(self, mock_email, mock_push):
        self.admin.unconfirm_deaths(self.request, Death.objects.all())

        self.death.refresh_from_db()
        self.assertFalse(self.death.is_confirmed)
        self.assertIsNone(self.death.confirmed_at)
        self.assertIsNone(self.death.confirmed_by)
        mock_push.assert_not_called()
        mock_email.assert_not_called()


class DetectBonusesActionRecoveredTest(AdminActionsBaseTestCase):
    def setUp(self):
        super().setUp()
        with patch('game.push.broadcast_death_notification'), \
                patch('game.email.broadcast_death_email'):
            self.death = Death.objects.create(
                person=self.person,
                death_date=date(2025, 3, 20),
                is_confirmed=True, confirmed_at=timezone.now(),
            )
        self.person.death_date = self.death.death_date
        self.person.is_dead = True
        self.person.save()
        self.bonus_wikidata = BonusType.objects.create(
            name='Premio Nobel recovered', points=100,
            detection_method=BonusType.DETECTION_WIKIDATA,
            wikidata_property='P166', wikidata_value='Q7191',
        )
        self.bonus_age = BonusType.objects.create(
            name='Morto giovane recovered', points=50,
            detection_method=BonusType.DETECTION_AGE,
            age_formula='age < 80',
        )
        self.admin = DeathAdmin(Death, self.site)

    @patch('wikidata_api.client.WikidataClient')
    def test_detects_wikidata_and_age_bonuses_idempotently(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.detect_bonuses.return_value = [self.bonus_wikidata]
        instance.detect_age_bonus.side_effect = (
            lambda age, bt: bt.detection_method == BonusType.DETECTION_AGE
        )

        self.admin.detect_bonuses_action(self.request, Death.objects.all())
        self.admin.detect_bonuses_action(self.request, Death.objects.all())

        awarded = {db.bonus_type_id: db for db in self.death.bonuses.all()}
        self.assertIn(self.bonus_wikidata.pk, awarded)
        self.assertIn(self.bonus_age.pk, awarded)
        self.assertEqual(len(awarded), 2)
        self.assertTrue(all(db.is_auto_detected for db in awarded.values()))


class FrozenAdminRefreshRecoveredTest(AdminActionsBaseTestCase):
    @patch('wikidata_api.client.WikidataClient')
    def test_manual_admin_refresh_overrides_data_frozen(self, mock_client_class):
        self.person.data_frozen = True
        self.person.save(update_fields=['data_frozen'])
        mock_client_class.return_value.get_entity.return_value = {
            'name_it': 'Nome Aggiornato',
            'name_en': '',
            'description_it': '',
            'birth_date': self.person.birth_date,
            'birth_year': self.person.birth_date.year,
            'death_date': None,
            'death_year': None,
            'image_url': '',
            'occupation': '',
            'nationality': '',
            'wikipedia_url_it': '',
            'claims_cache': {},
        }

        admin = WikidataPersonAdmin(WikipediaPerson, self.site)
        admin.refresh_from_wikidata(self.request, WikipediaPerson.objects.filter(pk=self.person.pk))

        self.person.refresh_from_db()
        self.assertEqual(self.person.name_it, 'Nome Aggiornato')
        self.assertTrue(self.person.data_frozen)
        self.assertIsNotNone(self.person.last_checked)
