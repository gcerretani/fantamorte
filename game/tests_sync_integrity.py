from datetime import date
from unittest.mock import Mock

from django.test import TestCase

from .models import BonusType, Death, DeathBonus, WikipediaPerson
from .person_sync import sync_person_from_entity


class AutomaticDeathConfirmationTimestampTest(TestCase):
    def _client(self):
        client = Mock()
        client.detect_bonuses.return_value = []
        client.detect_age_bonus.return_value = False
        return client

    def _entity(self):
        return {
            'name_it': 'Persona Test',
            'death_date': date(2026, 9, 1),
            'death_year': 2026,
            'claims_cache': {},
        }

    def test_new_autoconfirmed_death_has_confirmed_at(self):
        person = WikipediaPerson.objects.create(
            wikidata_id='Q991001', name_it='Persona Test',
        )
        death, created = sync_person_from_entity(
            person, self._entity(), client=self._client(), autoconfirm=True,
        )
        self.assertTrue(created)
        self.assertTrue(death.is_confirmed)
        self.assertIsNotNone(death.confirmed_at)

    def test_existing_unconfirmed_death_gets_timestamp_when_promoted(self):
        person = WikipediaPerson.objects.create(
            wikidata_id='Q991002', name_it='Persona Test', is_dead=True,
            death_date=date(2026, 9, 1), death_year=2026,
        )
        death = Death.objects.create(
            person=person, death_date=date(2026, 9, 1), is_confirmed=False,
        )
        sync_person_from_entity(
            person, self._entity(), client=self._client(), autoconfirm=True,
        )
        death.refresh_from_db()
        self.assertTrue(death.is_confirmed)
        self.assertIsNotNone(death.confirmed_at)

    def test_no_autoconfirm_keeps_timestamp_empty(self):
        person = WikipediaPerson.objects.create(
            wikidata_id='Q991003', name_it='Persona Test',
        )
        death, _ = sync_person_from_entity(
            person, self._entity(), client=self._client(), autoconfirm=False,
        )
        self.assertFalse(death.is_confirmed)
        self.assertIsNone(death.confirmed_at)


class AutomaticBonusReconciliationTest(TestCase):
    def setUp(self):
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q991010', name_it='Bonus Test',
            birth_date=date(1980, 1, 1),
        )
        self.entity = {
            'name_it': 'Bonus Test',
            'birth_date': date(1980, 1, 1),
            'death_date': date(2026, 9, 1),
            'death_year': 2026,
            'claims_cache': {'P166': []},
        }
        self.wikidata_bonus = BonusType.objects.create(
            name='Auto Wikidata Test', points=12,
            detection_method=BonusType.DETECTION_WIKIDATA,
            wikidata_property='P166',
        )

    def _client(self, detected=None):
        client = Mock()
        client.detect_bonuses.return_value = list(detected or [])
        client.detect_age_bonus.return_value = False
        return client

    def test_stale_automatic_award_is_removed_after_successful_recheck(self):
        death, _ = sync_person_from_entity(
            self.person, self.entity,
            client=self._client([self.wikidata_bonus]),
        )
        self.assertTrue(DeathBonus.objects.filter(
            death=death, bonus_type=self.wikidata_bonus, is_auto_detected=True,
        ).exists())

        sync_person_from_entity(self.person, self.entity, client=self._client([]))
        self.assertFalse(DeathBonus.objects.filter(
            death=death, bonus_type=self.wikidata_bonus, is_auto_detected=True,
        ).exists())

    def test_manual_award_is_never_removed_by_reconciliation(self):
        death, _ = sync_person_from_entity(self.person, self.entity, client=self._client([]))
        DeathBonus.objects.create(
            death=death, bonus_type=self.wikidata_bonus,
            points_awarded=12, is_auto_detected=False,
        )
        sync_person_from_entity(self.person, self.entity, client=self._client([]))
        self.assertTrue(DeathBonus.objects.filter(
            death=death, bonus_type=self.wikidata_bonus, is_auto_detected=False,
        ).exists())

    def test_detection_failure_preserves_previous_automatic_award(self):
        death, _ = sync_person_from_entity(
            self.person, self.entity,
            client=self._client([self.wikidata_bonus]),
        )
        failing = self._client()
        failing.detect_bonuses.side_effect = RuntimeError('temporary failure')
        sync_person_from_entity(self.person, self.entity, client=failing)
        self.assertTrue(DeathBonus.objects.filter(
            death=death, bonus_type=self.wikidata_bonus, is_auto_detected=True,
        ).exists())
