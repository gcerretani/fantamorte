from datetime import date
from unittest.mock import Mock

from django.test import TestCase

from .models import Death, WikipediaPerson
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
