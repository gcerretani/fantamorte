"""Test delle admin actions del Django admin (game/admin.py).

Le azioni vengono invocate direttamente sulle classi ModelAdmin con una
request costruita ad hoc (RequestFactory + storage messaggi), senza passare
dal client HTTP: è il pattern consigliato dalla documentazione Django per
testare le actions in isolamento. Il client Wikidata è sempre mockato alla
fonte (`wikidata_api.client.WikidataClient`), da cui gli import locali
dentro le actions lo risolvono.
"""
from datetime import date, timedelta
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
    """Request POST con supporto messages, come la vede una admin action."""
    request = RequestFactory().post('/admin/')
    request.user = user
    request.session = 'session'
    request._messages = FallbackStorage(request)
    return request


class AdminActionsBaseTestCase(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.request = _admin_request(self.staff)
        self.site = AdminSite()
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q1339', name_it='Johann Sebastian Bach',
            birth_date=date(1950, 3, 21), is_dead=False,
        )


class ConfirmDeathsActionTest(AdminActionsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.death = Death.objects.create(
            person=self.person, death_date=timezone.now().date(), is_confirmed=False,
        )
        self.admin = DeathAdmin(Death, self.site)

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    def test_conferma_imposta_flag_e_scatena_le_notifiche(self, mock_email, mock_push):
        self.admin.confirm_deaths(self.request, Death.objects.all())

        self.death.refresh_from_db()
        self.person.refresh_from_db()
        self.assertTrue(self.death.is_confirmed)
        self.assertEqual(self.death.confirmed_by, self.staff)
        self.assertIsNotNone(self.death.confirmed_at)
        self.assertTrue(self.person.is_dead)
        # La transizione False → True passa dal save(), quindi dal signal.
        mock_push.assert_called_once()
        mock_email.assert_called_once()

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    def test_death_gia_confermata_non_viene_rinotificata(self, mock_email, mock_push):
        other_user = User.objects.create_user('altro', password='x', is_staff=True)
        self.death.is_confirmed = True
        self.death.confirmed_by = other_user
        self.death.confirmed_at = timezone.now()
        self.death.save()
        mock_push.reset_mock()
        mock_email.reset_mock()

        self.admin.confirm_deaths(self.request, Death.objects.all())

        self.death.refresh_from_db()
        # Il filtro is_confirmed=False esclude la death già confermata:
        # confirmed_by resta quello originale e non partono nuove notifiche.
        self.assertEqual(self.death.confirmed_by, other_user)
        mock_push.assert_not_called()
        mock_email.assert_not_called()


class UnconfirmDeathsActionTest(AdminActionsBaseTestCase):

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
    def test_revoca_azzera_i_campi_di_conferma_senza_notifiche(self, mock_email, mock_push):
        self.admin.unconfirm_deaths(self.request, Death.objects.all())

        self.death.refresh_from_db()
        self.assertFalse(self.death.is_confirmed)
        self.assertIsNone(self.death.confirmed_at)
        self.assertIsNone(self.death.confirmed_by)
        # La revoca usa update(): nessun signal, nessuna notifica.
        mock_push.assert_not_called()
        mock_email.assert_not_called()

    def test_death_non_confermata_resta_invariata(self):
        self.death.is_confirmed = False
        Death.objects.filter(pk=self.death.pk).update(is_confirmed=False)
        self.admin.unconfirm_deaths(self.request, Death.objects.all())
        self.death.refresh_from_db()
        self.assertFalse(self.death.is_confirmed)


class DetectBonusesActionTest(AdminActionsBaseTestCase):

    def setUp(self):
        super().setUp()
        with patch('game.push.broadcast_death_notification'), \
                patch('game.email.broadcast_death_email'):
            self.death = Death.objects.create(
                person=self.person,
                death_date=date(2025, 3, 20),  # un giorno prima del compleanno: 74 anni
                is_confirmed=True, confirmed_at=timezone.now(),
            )
        self.person.death_date = self.death.death_date
        self.person.is_dead = True
        self.person.save()
        self.bonus_wikidata = BonusType.objects.create(
            name='Premio Nobel', points=100,
            detection_method=BonusType.DETECTION_WIKIDATA,
            wikidata_property='P166', wikidata_value='Q7191',
        )
        self.bonus_age = BonusType.objects.create(
            name='Morto giovane', points=50,
            detection_method=BonusType.DETECTION_AGE,
            age_formula='age < 80',
        )
        self.admin = DeathAdmin(Death, self.site)

    @patch('wikidata_api.client.WikidataClient')
    def test_crea_deathbonus_per_bonus_wikidata_ed_eta(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.detect_bonuses.return_value = [self.bonus_wikidata]
        instance.detect_age_bonus.side_effect = (
            lambda age, bt: bt.detection_method == BonusType.DETECTION_AGE
        )

        self.admin.detect_bonuses_action(self.request, Death.objects.all())

        awarded = {db.bonus_type_id: db for db in self.death.bonuses.all()}
        self.assertIn(self.bonus_wikidata.pk, awarded)
        self.assertIn(self.bonus_age.pk, awarded)
        self.assertTrue(all(db.is_auto_detected for db in awarded.values()))
        self.assertEqual(awarded[self.bonus_wikidata.pk].points_awarded, 100)

    @patch('wikidata_api.client.WikidataClient')
    def test_seconda_esecuzione_non_duplica_i_bonus(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.detect_bonuses.return_value = [self.bonus_wikidata]
        instance.detect_age_bonus.return_value = False

        self.admin.detect_bonuses_action(self.request, Death.objects.all())
        self.admin.detect_bonuses_action(self.request, Death.objects.all())

        self.assertEqual(
            DeathBonus.objects.filter(death=self.death, bonus_type=self.bonus_wikidata).count(),
            1,
        )

    @patch('wikidata_api.client.WikidataClient')
    def test_nessun_bonus_rilevato_non_crea_nulla(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.detect_bonuses.return_value = []
        instance.detect_age_bonus.return_value = False

        self.admin.detect_bonuses_action(self.request, Death.objects.all())

        self.assertEqual(self.death.bonuses.count(), 0)


class RefreshFromWikidataActionTest(AdminActionsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.admin = WikidataPersonAdmin(WikipediaPerson, self.site)

    @patch('wikidata_api.client.WikidataClient')
    def test_aggiorna_i_campi_dalla_entity(self, mock_client_class):
        death_date = date(2025, 6, 1)
        mock_client_class.return_value.get_entity.return_value = {
            'name_it': 'Nome Aggiornato',
            'name_en': 'Updated Name',
            'birth_date': date(1950, 3, 21),
            'birth_year': 1950,
            'death_date': death_date,
            'death_year': 2025,
            'claims_cache': {'P166': ['Q7191']},
        }

        self.admin.refresh_from_wikidata(self.request, WikipediaPerson.objects.all())

        self.person.refresh_from_db()
        self.assertEqual(self.person.name_it, 'Nome Aggiornato')
        self.assertEqual(self.person.death_date, death_date)
        self.assertTrue(self.person.is_dead)
        self.assertEqual(self.person.claims_cache, {'P166': ['Q7191']})
        self.assertIsNotNone(self.person.last_checked)

    @patch('wikidata_api.client.WikidataClient')
    def test_errore_wikidata_non_tocca_la_persona(self, mock_client_class):
        mock_client_class.return_value.get_entity.side_effect = RuntimeError('timeout')

        self.admin.refresh_from_wikidata(self.request, WikipediaPerson.objects.all())

        self.person.refresh_from_db()
        self.assertEqual(self.person.name_it, 'Johann Sebastian Bach')
        self.assertFalse(self.person.is_dead)
