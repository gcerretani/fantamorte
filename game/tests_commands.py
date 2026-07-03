"""Test del management command `check_deaths` con WikidataClient mockato.

Il client Wikidata viene sempre patchato a livello di classe nel modulo del
command (`game.management.commands.check_deaths.WikidataClient`), così nessuna
richiesta di rete viene mai effettuata. I broadcast di push ed email vengono
patchati alla fonte (`game.push.broadcast_death_notification` e
`game.email.broadcast_death_email`) per verificare che il signal `Death`
scatti correttamente sulla transizione is_confirmed False → True.
"""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import Death, League, Team, TeamMember, WikipediaPerson

User = get_user_model()


def _entity_payload(death_date):
    """Riproduce la forma di ritorno reale di WikidataClient.get_entity()."""
    return {
        'name_it': 'Persona Test',
        'name_en': '',
        'description_it': '',
        'birth_date': None,
        'birth_year': None,
        'death_date': death_date,
        'death_year': death_date.year,
        'wikipedia_url_it': '',
        'wiki_title_it': '',
        'image_url': '',
        'occupation': None,
        'nationality': None,
        'claims_cache': {},
    }


class CheckDeathsTestCase(TestCase):
    """Fixture condivisa: una lega in corso con una persona viva in rosa."""

    def setUp(self):
        self.owner = User.objects.create_user('manager', password='x')
        today = timezone.now().date()
        self.league = League.objects.create(
            name='Lega Check', slug='lega-check', owner=self.owner,
            start_date=today - timedelta(days=365),
            end_date=today + timedelta(days=365),
            registration_opens=today - timedelta(days=400),
            registration_closes=today - timedelta(days=370),
        )
        self.team = Team.objects.create(name='Squadra', manager=self.owner, league=self.league)
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q123456', name_it='Persona Test', is_dead=False,
        )
        TeamMember.objects.create(team=self.team, person=self.person)
        self.death_date = today - timedelta(days=2)

    def _mock_client(self, mock_class, death_date=None):
        """Configura il mock istanza restituita da WikidataClient()."""
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = [self.person.wikidata_id]
        instance.get_entity.return_value = _entity_payload(death_date or self.death_date)
        instance.detect_bonuses.return_value = []
        return instance


class CheckDeathsAutoconfirmTest(CheckDeathsTestCase):
    """Caso 1: decesso rilevato con data valida → auto-conferma di default."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_decesso_rilevato_crea_death_confermata_e_scatena_i_broadcast(
        self, mock_client_class, mock_email, mock_push,
    ):
        self._mock_client(mock_client_class)

        call_command('check_deaths')

        self.person.refresh_from_db()
        self.assertTrue(self.person.is_dead)

        death = Death.objects.get(person=self.person)
        self.assertTrue(death.is_confirmed)
        self.assertEqual(death.death_date, self.death_date)

        mock_push.assert_called_once_with(death)
        mock_email.assert_called_once_with(death)


class CheckDeathsNoAutoconfirmTest(CheckDeathsTestCase):
    """Caso 2: `--no-autoconfirm` crea il decesso non confermato, niente broadcast."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_no_autoconfirm_crea_death_non_confermata_senza_broadcast(
        self, mock_client_class, mock_email, mock_push,
    ):
        self._mock_client(mock_client_class)

        call_command('check_deaths', no_autoconfirm=True)

        self.person.refresh_from_db()
        self.assertTrue(self.person.is_dead)

        death = Death.objects.get(person=self.person)
        self.assertFalse(death.is_confirmed)

        mock_push.assert_not_called()
        mock_email.assert_not_called()


class CheckDeathsPromotionTest(CheckDeathsTestCase):
    """Caso 3: un Death preesistente non confermato viene promosso a confermato."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_death_preesistente_non_confermata_viene_promossa(
        self, mock_client_class, mock_email, mock_push,
    ):
        existing = Death.objects.create(
            person=self.person,
            death_date=self.death_date, death_age=80, is_confirmed=False,
        )
        self._mock_client(mock_client_class)

        call_command('check_deaths')

        existing.refresh_from_db()
        self.assertTrue(existing.is_confirmed)
        self.assertEqual(Death.objects.filter(person=self.person).count(), 1)

        mock_push.assert_called_once_with(existing)
        mock_email.assert_called_once_with(existing)


class CheckDeathsIdempotencyTest(CheckDeathsTestCase):
    """Caso 4: rieseguire il command con lo stesso esito non duplica nulla."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_run_ripetuto_non_duplica_death_ne_broadcast(
        self, mock_client_class, mock_email, mock_push,
    ):
        self._mock_client(mock_client_class)

        call_command('check_deaths')
        self.assertEqual(Death.objects.filter(person=self.person).count(), 1)
        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_email.call_count, 1)

        # Secondo run: la persona è ormai is_dead=True, quindi non è più
        # candidata (`active_persons` filtra `is_dead=False`) e il command
        # deve terminare senza toccare nulla.
        call_command('check_deaths')

        self.assertEqual(Death.objects.filter(person=self.person).count(), 1)
        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_email.call_count, 1)


class CheckDeathsFrozenPersonTest(CheckDeathsTestCase):
    """Caso 5: `data_frozen=True` esclude la persona dai controlli automatici."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_persona_data_frozen_non_viene_controllata(
        self, mock_client_class, mock_email, mock_push,
    ):
        self.person.data_frozen = True
        self.person.save()
        instance = self._mock_client(mock_client_class)

        call_command('check_deaths')

        instance.check_deaths_batch.assert_not_called()
        self.assertFalse(Death.objects.filter(person=self.person).exists())
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_dead)
        mock_push.assert_not_called()
        mock_email.assert_not_called()


class CheckDeathsDryRunTest(CheckDeathsTestCase):
    """Caso 6: `--dry-run` non scrive nulla sul database."""

    @patch('game.push.broadcast_death_notification')
    @patch('game.email.broadcast_death_email')
    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_dry_run_non_scrive_nulla(self, mock_client_class, mock_email, mock_push):
        self._mock_client(mock_client_class)

        call_command('check_deaths', dry_run=True)

        self.person.refresh_from_db()
        self.assertFalse(self.person.is_dead)
        self.assertFalse(Death.objects.filter(person=self.person).exists())
        mock_push.assert_not_called()
        mock_email.assert_not_called()


class MarkOriginalsTest(TestCase):
    """Test del management command `mark_originals`.

    Una giocata è originale se la persona compare nella rosa iniziale di un
    solo manager della lega. I membri creati come sostituti (che hanno un
    `replaces`) non contano né vengono marcati.
    """

    def setUp(self):
        self.manager_a = User.objects.create_user('manager_a', password='x')
        self.manager_b = User.objects.create_user('manager_b', password='x')
        today = timezone.now().date()
        self.league = League.objects.create(
            name='Lega Originali', slug='lega-originali', owner=self.manager_a,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=355),
            registration_opens=today - timedelta(days=40),
            registration_closes=today - timedelta(days=11),
        )
        self.team_a = Team.objects.create(name='Team A', manager=self.manager_a, league=self.league)
        self.team_b = Team.objects.create(name='Team B', manager=self.manager_b, league=self.league)

        self.unica = WikipediaPerson.objects.create(wikidata_id='Q100', name_it='Scelta Unica')
        self.condivisa = WikipediaPerson.objects.create(wikidata_id='Q200', name_it='Scelta Condivisa')
        self.subentrata = WikipediaPerson.objects.create(wikidata_id='Q300', name_it='Subentrata Unica')

        self.member_unica = TeamMember.objects.create(team=self.team_a, person=self.unica)
        self.member_condivisa_a = TeamMember.objects.create(team=self.team_a, person=self.condivisa)
        self.member_condivisa_b = TeamMember.objects.create(team=self.team_b, person=self.condivisa)

    def test_scelta_unica_marcata_condivisa_no(self):
        call_command('mark_originals', league='lega-originali')

        self.member_unica.refresh_from_db()
        self.member_condivisa_a.refresh_from_db()
        self.member_condivisa_b.refresh_from_db()
        self.assertTrue(self.member_unica.is_original)
        self.assertFalse(self.member_condivisa_a.is_original)
        self.assertFalse(self.member_condivisa_b.is_original)

    def test_sostituto_escluso_dal_conteggio_e_non_marcato(self):
        # member_condivisa_b viene sostituito con `subentrata`, scelta da nessun altro:
        # il nuovo membro non fa parte della rosa iniziale e non va marcato.
        new_member = TeamMember.objects.create(team=self.team_b, person=self.subentrata)
        self.member_condivisa_b.replaced_by = new_member
        self.member_condivisa_b.save()

        call_command('mark_originals', league='lega-originali')

        new_member.refresh_from_db()
        self.member_unica.refresh_from_db()
        self.assertFalse(new_member.is_original)
        self.assertTrue(self.member_unica.is_original)

    def test_reset_azzera_i_flag_stantii(self):
        # Flag impostato a mano su una scelta condivisa: senza --reset resterebbe.
        TeamMember.objects.filter(pk=self.member_condivisa_a.pk).update(is_original=True)

        call_command('mark_originals', league='lega-originali', reset=True)

        self.member_condivisa_a.refresh_from_db()
        self.member_unica.refresh_from_db()
        self.assertFalse(self.member_condivisa_a.is_original)
        self.assertTrue(self.member_unica.is_original)

    def test_senza_slug_processa_solo_le_leghe_in_corso(self):
        today = timezone.now().date()
        finished = League.objects.create(
            name='Lega Finita', slug='lega-finita', owner=self.manager_a,
            start_date=today - timedelta(days=400), end_date=today - timedelta(days=30),
            registration_opens=today - timedelta(days=430),
            registration_closes=today - timedelta(days=401),
        )
        finished_team = Team.objects.create(name='Vecchia', manager=self.manager_a, league=finished)
        finished_member = TeamMember.objects.create(team=finished_team, person=self.subentrata)

        call_command('mark_originals')

        self.member_unica.refresh_from_db()
        finished_member.refresh_from_db()
        self.assertTrue(self.member_unica.is_original)
        self.assertFalse(finished_member.is_original)

    def test_slug_inesistente_segnala_nessuna_lega(self):
        from io import StringIO
        err = StringIO()
        call_command('mark_originals', league='non-esiste', stderr=err)
        self.assertIn('Nessuna lega trovata', err.getvalue())
        self.member_unica.refresh_from_db()
        self.assertFalse(self.member_unica.is_original)


class BonusTypeComputePointsTest(TestCase):
    """Test di `BonusType.compute_points`, la whitelist di eval usata per le formule."""

    def _bonus(self, points=10, points_formula=''):
        from .models import BonusType
        return BonusType(name='X', points=points, points_formula=points_formula)

    def test_formula_valida_calcola_i_punti(self):
        bonus = self._bonus(points=0, points_formula='2*(90-age)')
        self.assertEqual(bonus.compute_points(age=70), 40)

    def test_formula_con_doppio_asterisco_viene_rifiutata(self):
        bonus = self._bonus(points=5, points_formula='age**2')
        self.assertEqual(bonus.compute_points(age=10), 5)

    def test_formula_con_caratteri_illegali_torna_ai_punti_fissi(self):
        bonus = self._bonus(points=7, points_formula='__import__("os")')
        self.assertEqual(bonus.compute_points(age=10), 7)

    def test_senza_formula_restituisce_i_punti_fissi(self):
        bonus = self._bonus(points=15, points_formula='')
        self.assertEqual(bonus.compute_points(age=99), 15)
