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

from .models import Death, League, SiteSettings, Team, TeamMember, WikipediaPerson

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


class CheckDeathsRotationTest(TestCase):
    """La rotazione distribuisce i controlli: ogni run prende solo la fetta
    più vecchia, dimensionata per coprire il pool nell'arco dell'intervallo."""

    def setUp(self):
        self.owner = User.objects.create_user('manager', password='x')
        today = timezone.now().date()
        self.league = League.objects.create(
            name='Lega Rot', slug='lega-rot', owner=self.owner,
            start_date=today - timedelta(days=365),
            end_date=today + timedelta(days=365),
            registration_opens=today - timedelta(days=400),
            registration_closes=today - timedelta(days=370),
        )
        self.team = Team.objects.create(name='Squadra', manager=self.owner, league=self.league)
        self.persons = []
        for i in range(6):
            p = WikipediaPerson.objects.create(
                wikidata_id=f'Q{1000 + i}', name_it=f'P{i}', is_dead=False,
            )
            TeamMember.objects.create(team=self.team, person=p)
            self.persons.append(p)

    def _settings(self, interval, schedule):
        s = SiteSettings.get()
        s.wikidata_check_interval_hours = interval
        s.wikidata_check_schedule_hours = schedule
        s.save()

    def _mock(self, mock_class):
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = []  # nessun decesso rilevato
        return instance

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_batch_dimensionato_su_interval_e_schedule(self, mock_class):
        # 6 persone, interval 2h, schedule 1h → ceil(6*1/2) = 3 per run.
        self._settings(interval=2, schedule=1)
        instance = self._mock(mock_class)
        call_command('check_deaths')
        checked_ids = instance.check_deaths_batch.call_args[0][0]
        self.assertEqual(len(checked_ids), 3)

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_stalest_first(self, mock_class):
        # Metà già controllate di recente, metà vecchie: il run prende le vecchie.
        now = timezone.now()
        recent = self.persons[:3]
        stale = self.persons[3:]
        for p in recent:
            p.last_checked = now
            p.save()
        for p in stale:
            p.last_checked = now - timedelta(days=10)
            p.save()
        self._settings(interval=2, schedule=1)  # batch 3
        instance = self._mock(mock_class)
        call_command('check_deaths')
        checked = set(instance.check_deaths_batch.call_args[0][0])
        self.assertEqual(checked, {p.wikidata_id for p in stale})

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_copertura_completa_in_un_ciclo(self, mock_class):
        # batch 3 su 6 persone → 2 run coprono tutti (last_checked valorizzato).
        self._settings(interval=2, schedule=1)
        self._mock(mock_class)
        call_command('check_deaths')
        call_command('check_deaths')
        self.assertEqual(
            WikipediaPerson.objects.filter(last_checked__isnull=True).count(), 0,
        )

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_force_controlla_tutti_in_un_colpo(self, mock_class):
        self._settings(interval=2, schedule=1)  # batch 3 senza --force
        instance = self._mock(mock_class)
        call_command('check_deaths', force=True)
        checked_ids = instance.check_deaths_batch.call_args[0][0]
        self.assertEqual(len(checked_ids), 6)

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_limit_override(self, mock_class):
        self._settings(interval=2, schedule=1)  # batch 3 di default
        instance = self._mock(mock_class)
        call_command('check_deaths', limit=1)
        checked_ids = instance.check_deaths_batch.call_args[0][0]
        self.assertEqual(len(checked_ids), 1)


class CheckDeathsSelezioneLegheTest(TestCase):
    """Quali leghe entrano nel controllo, e con quale query."""

    def setUp(self):
        self.owner = User.objects.create_user('manager', password='x')
        self.today = timezone.now().date()

    def _league_with_person(self, slug, start_offset, qid):
        start = self.today + timedelta(days=start_offset)
        league = League.objects.create(
            name=slug, slug=slug, owner=self.owner,
            start_date=start, end_date=start + timedelta(days=365),
            registration_opens=start - timedelta(days=60),
            registration_closes=start,
        )
        team = Team.objects.create(name=f'Squadra {slug}', manager=self.owner, league=league)
        person = WikipediaPerson.objects.create(
            wikidata_id=qid, name_it=f'Persona {qid}', is_dead=False,
        )
        TeamMember.objects.create(team=team, person=person)
        return league, person

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_include_le_leghe_da_iniziare(self, mock_class):
        """Il decesso in fase di composizione va scoperto finché si può rimediare."""
        _, futura = self._league_with_person('lega-futura', 30, 'Q1')
        _, in_corso = self._league_with_person('lega-in-corso', -30, 'Q2')
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = []
        # --force per scavalcare la rotazione a fette, che qui prenderebbe una
        # persona sola e renderebbe il test dipendente dall'ordinamento.
        call_command('check_deaths', force=True)
        checked = set(instance.check_deaths_batch.call_args[0][0])
        self.assertEqual(checked, {futura.wikidata_id, in_corso.wikidata_id})

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_esclude_le_leghe_concluse(self, mock_class):
        conclusa = League.objects.create(
            name='Lega Conclusa', slug='lega-conclusa', owner=self.owner,
            start_date=self.today - timedelta(days=400),
            end_date=self.today - timedelta(days=10),
            registration_opens=self.today - timedelta(days=430),
            registration_closes=self.today - timedelta(days=400),
        )
        team = Team.objects.create(name='Vecchia', manager=self.owner, league=conclusa)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q9', name_it='Persona Q9', is_dead=False,
        )
        TeamMember.objects.create(team=team, person=person)
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = []
        call_command('check_deaths')
        instance.check_deaths_batch.assert_not_called()

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_una_sola_query_senza_anno(self, mock_class):
        """Una lega a cavallo di due anni non moltiplica le richieste."""
        start = date(self.today.year - 1, 7, 1)
        league = League.objects.create(
            name='Lega Biennale', slug='lega-biennale', owner=self.owner,
            start_date=start, end_date=start + timedelta(days=700),
            registration_opens=start - timedelta(days=60),
            registration_closes=start,
        )
        team = Team.objects.create(name='Squadra', manager=self.owner, league=league)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q7', name_it='Persona Q7', is_dead=False,
        )
        TeamMember.objects.create(team=team, person=person)
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = []
        call_command('check_deaths')
        self.assertEqual(instance.check_deaths_batch.call_count, 1)
        # Nessun anno tra gli argomenti: la finestra la applica lo scoring.
        args, kwargs = instance.check_deaths_batch.call_args
        self.assertEqual(len(args), 1)
        self.assertEqual(kwargs, {})

    @patch('game.management.commands.check_deaths.WikidataClient')
    def test_decesso_dell_anno_precedente_all_inizio(self, mock_class):
        """Il punto cieco di prima: morto a dicembre, lega che parte a gennaio."""
        league, person = self._league_with_person('lega-gennaio', 30, 'Q5')
        instance = mock_class.return_value
        instance.check_deaths_batch.return_value = [person.wikidata_id]
        instance.get_entity.return_value = _entity_payload(league.start_date - timedelta(days=40))
        instance.detect_bonuses.return_value = []
        call_command('check_deaths')
        person.refresh_from_db()
        self.assertTrue(person.is_dead)
        self.assertTrue(Death.objects.filter(person=person, is_confirmed=True).exists())


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
