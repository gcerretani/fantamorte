"""Test di integrazione su view e permessi (leghe private, IDOR, CSRF)."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    League, LeagueMembership, Team, TeamMember, WikipediaPerson,
)

User = get_user_model()


class ViewsBaseTestCase(TestCase):
    """Fixture condivisa: una lega privata con membro, un estraneo, una lega pubblica."""

    def setUp(self):
        self.owner = User.objects.create_user('owner', password='x')
        self.member = User.objects.create_user('member', password='x')
        self.outsider = User.objects.create_user('outsider', password='x')

        self.private_league = League.objects.create(
            name='Lega Privata', slug='lega-privata', owner=self.owner,
            visibility=League.VISIBILITY_PRIVATE, invite_code='segretissimo',
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2019, 12, 1), registration_closes=date(2030, 12, 31),
        )
        LeagueMembership.objects.create(
            league=self.private_league, user=self.owner, role=LeagueMembership.ROLE_OWNER,
        )
        LeagueMembership.objects.create(
            league=self.private_league, user=self.member, role=LeagueMembership.ROLE_MEMBER,
        )

        self.public_league = League.objects.create(
            name='Lega Pubblica', slug='lega-pubblica', owner=self.owner,
            visibility=League.VISIBILITY_PUBLIC,
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2019, 12, 1), registration_closes=date(2030, 12, 31),
        )
        LeagueMembership.objects.create(
            league=self.public_league, user=self.owner, role=LeagueMembership.ROLE_OWNER,
        )

        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q11860', name_it='Silvio Berlusconi', is_dead=False,
        )
        self.private_team = Team.objects.create(
            name='Squadra Privata', manager=self.member, league=self.private_league,
        )
        TeamMember.objects.create(team=self.private_team, person=self.person)


class LeghePrivateTest(ViewsBaseTestCase):

    def test_non_membro_vede_teaser_con_form_invito(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('league_detail', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'game/league_join.html')
        self.assertNotContains(resp, 'Squadra Privata')

    def test_membro_vede_il_dettaglio_completo(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_detail', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'game/league_detail.html')

    def test_join_con_codice_sbagliato_rifiutato(self):
        self.client.login(username='outsider', password='x')
        self.client.post(reverse('league_join', args=['lega-privata']),
                         {'invite_code': 'sbagliato'})
        self.assertFalse(self.private_league.is_member(self.outsider))

    def test_join_con_codice_corretto_iscrive(self):
        self.client.login(username='outsider', password='x')
        self.client.post(reverse('league_join', args=['lega-privata']),
                         {'invite_code': 'segretissimo'})
        self.assertTrue(self.private_league.is_member(self.outsider))

    def test_link_invito_get_precompila_codice(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(
            reverse('league_join', args=['lega-privata']) + '?code=segretissimo',
            follow=True,
        )
        self.assertContains(resp, 'value="segretissimo"')


class IdorDetailViewTest(ViewsBaseTestCase):

    def test_team_di_lega_privata_404_per_non_membro(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_team_di_lega_privata_visibile_al_membro(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_person_detail_nasconde_team_di_leghe_private(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('person_detail', args=[self.person.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Squadra Privata')

    def test_person_detail_mostra_team_al_membro(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('person_detail', args=[self.person.pk]))
        self.assertContains(resp, 'Squadra Privata')


class LeagueAdminPermessiTest(ViewsBaseTestCase):

    def test_non_admin_non_accede_al_pannello(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_admin', args=['lega-privata']))
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_non_modifica_le_regole(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(reverse('league_admin', args=['lega-privata']),
                                {'action': 'update_rules', 'name': 'Hackerata'})
        self.assertEqual(resp.status_code, 403)
        self.private_league.refresh_from_db()
        self.assertEqual(self.private_league.name, 'Lega Privata')

    def test_owner_accede_al_pannello(self):
        self.client.login(username='owner', password='x')
        resp = self.client.get(reverse('league_admin', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)

    def test_date_incoerenti_rifiutate(self):
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']), {
            'action': 'update_rules',
            'start_date': '2030-01-01', 'end_date': '2020-01-01',
        })
        self.private_league.refresh_from_db()
        self.assertEqual(self.private_league.start_date, date(2020, 1, 1))


class BulkApplyServerSideTest(ViewsBaseTestCase):
    """L'apply non deve mai fidarsi dei valori inviati dal client."""

    def test_non_admin_rifiutato(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(
            reverse('league_wikidata_apply', args=['lega-privata']),
            data='{"updates": []}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    @patch('wikidata_api.client.WikidataClient.get_entity')
    def test_valore_client_ignorato_si_usa_wikidata(self, mock_entity):
        mock_entity.return_value = {
            'name_it': 'Nome Da Wikidata', 'name_en': '', 'description_it': '',
            'birth_date': None, 'birth_year': None,
            'death_date': None, 'death_year': None,
            'image_url': '', 'occupation': '', 'nationality': '',
            'claims_cache': {}, 'wikipedia_url_it': '', 'wiki_title_it': '',
        }
        self.client.login(username='owner', password='x')
        resp = self.client.post(
            reverse('league_wikidata_apply', args=['lega-privata']),
            data=('{"updates": [{"person_pk": %d, "field": "name_it",'
                  ' "new_value": "<script>hacked</script>"}]}' % self.person.pk),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual(self.person.name_it, 'Nome Da Wikidata')

    def test_campo_non_applicabile_rifiutato(self):
        self.client.login(username='owner', password='x')
        resp = self.client.post(
            reverse('league_wikidata_apply', args=['lega-privata']),
            data=('{"updates": [{"person_pk": %d, "field": "data_frozen",'
                  ' "new_value": true}]}' % self.person.pk),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Campo non modificabile', resp.json()['errors'][0])


class PushCsrfTest(ViewsBaseTestCase):

    def test_subscribe_senza_csrf_rifiutato(self):
        client = Client(enforce_csrf_checks=True)
        client.login(username='member', password='x')
        resp = client.post(
            reverse('push_subscribe'),
            data='{"endpoint": "https://evil.example/x", "keys": {"p256dh": "a", "auth": "b"}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)


class TeamEditValidazioneTest(ViewsBaseTestCase):

    def test_jolly_month_non_numerico_non_va_in_500(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(
            reverse('team_edit', args=[self.private_team.pk]),
            {'jolly_month': 'boom', 'name': 'Squadra Privata'},
        )
        self.assertIn(resp.status_code, (200, 302))
        self.private_team.refresh_from_db()
        self.assertIsNone(self.private_team.jolly_month)

    def test_jolly_month_fuori_range_rifiutato(self):
        self.client.login(username='member', password='x')
        self.client.post(
            reverse('team_edit', args=[self.private_team.pk]),
            {'jolly_month': '13', 'name': 'Squadra Privata'},
        )
        self.private_team.refresh_from_db()
        self.assertIsNone(self.private_team.jolly_month)

    def test_non_owner_non_modifica_la_squadra(self):
        self.client.login(username='outsider', password='x')
        self.client.post(
            reverse('team_edit', args=[self.private_team.pk]),
            {'name': 'Rubata'},
        )
        self.private_team.refresh_from_db()
        self.assertEqual(self.private_team.name, 'Squadra Privata')


class TeamCreateFlowTest(ViewsBaseTestCase):
    """Il form di creazione è unico: team_create crea subito la squadra
    (senza chiedere nome/jolly a parte) e porta dritti a team_edit, dove
    nome, mese jolly e rosa si salvano insieme."""

    def test_get_crea_squadra_e_va_dritto_a_team_edit(self):
        self.client.login(username='outsider', password='x')
        LeagueMembership.objects.create(
            league=self.private_league, user=self.outsider, role=LeagueMembership.ROLE_MEMBER,
        )
        resp = self.client.get(reverse('team_create', args=['lega-privata']))
        team = Team.objects.get(manager=self.outsider, league=self.private_league)
        self.assertRedirects(resp, reverse('team_edit', args=[team.pk]))

    def test_get_ripetuto_non_duplica_la_squadra(self):
        self.client.login(username='member', password='x')
        self.client.get(reverse('team_create', args=['lega-privata']))
        self.assertEqual(
            Team.objects.filter(manager=self.member, league=self.private_league).count(), 1,
        )

    def test_salvataggio_propaga_nome_e_jolly(self):
        self.client.login(username='outsider', password='x')
        LeagueMembership.objects.create(
            league=self.private_league, user=self.outsider, role=LeagueMembership.ROLE_MEMBER,
        )
        self.client.get(reverse('team_create', args=['lega-privata']))
        team = Team.objects.get(manager=self.outsider, league=self.private_league)
        self.client.post(reverse('team_edit', args=[team.pk]),
                          {'name': 'I Falciatori', 'jolly_month': '5'})
        team.refresh_from_db()
        self.assertEqual(team.name, 'I Falciatori')
        self.assertEqual(team.jolly_month, 5)


class TeamDeleteTest(ViewsBaseTestCase):

    def test_manager_elimina_la_propria_squadra(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(reverse('team_delete', args=[self.private_team.pk]))
        self.assertRedirects(resp, reverse('league_detail', args=['lega-privata']))
        self.assertFalse(Team.objects.filter(pk=self.private_team.pk).exists())

    def test_estraneo_non_puo_eliminare(self):
        self.client.login(username='outsider', password='x')
        self.client.post(reverse('team_delete', args=[self.private_team.pk]))
        self.assertTrue(Team.objects.filter(pk=self.private_team.pk).exists())

    def test_squadra_bloccata_non_eliminabile(self):
        self.private_team.is_locked = True
        self.private_team.save()
        self.client.login(username='member', password='x')
        self.client.post(reverse('team_delete', args=[self.private_team.pk]))
        self.assertTrue(Team.objects.filter(pk=self.private_team.pk).exists())


class PagineGeneraliTest(ViewsBaseTestCase):

    def test_statistiche_accessibili(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('stats'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Leaderboard all-time')

    def test_healthz_pubblico(self):
        resp = Client().get(reverse('healthz'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')


class CustomBonusTest(ViewsBaseTestCase):
    """Bonus personalizzati per lega definiti con proprietà Wikidata."""

    def _create_payload(self, **overrides):
        data = {
            'action': 'create_custom_bonus',
            'bonus_name': 'Grammy Award',
            'bonus_points': '25',
            'bonus_wikidata_property': 'P166',
            'bonus_wikidata_value': 'Q41254',
            'bonus_description': 'Vincitore di un Grammy',
        }
        data.update(overrides)
        return data

    def test_admin_crea_bonus_personalizzato(self):
        from .models import BonusType, LeagueBonus
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']), self._create_payload())
        bt = BonusType.objects.get(name='Grammy Award')
        self.assertEqual(bt.league, self.private_league)
        self.assertEqual(bt.points, 25)
        self.assertEqual(bt.detection_method, 'wikidata')
        self.assertTrue(LeagueBonus.objects.filter(league=self.private_league, bonus_type=bt).exists())

    def test_proprieta_invalida_rifiutata(self):
        from .models import BonusType
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']),
                         self._create_payload(bonus_wikidata_property='P166} UNION {evil'))
        self.assertFalse(BonusType.objects.filter(name='Grammy Award').exists())

    def test_valore_invalido_rifiutato(self):
        from .models import BonusType
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']),
                         self._create_payload(bonus_wikidata_value='Q41254 . ?x ?y ?z'))
        self.assertFalse(BonusType.objects.filter(name='Grammy Award').exists())

    def test_non_admin_non_crea(self):
        from .models import BonusType
        self.client.login(username='member', password='x')
        resp = self.client.post(reverse('league_admin', args=['lega-privata']), self._create_payload())
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(BonusType.objects.filter(name='Grammy Award').exists())

    def test_bonus_custom_non_aggiungibile_in_altra_lega(self):
        from .models import BonusType, LeagueBonus
        custom = BonusType.objects.create(
            name='Solo Privata', league=self.private_league, points=10,
            detection_method='wikidata', wikidata_property='P166',
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-pubblica']),
                         {'action': 'set_bonus', 'add_bonus': str(custom.pk)})
        self.assertFalse(LeagueBonus.objects.filter(
            league=self.public_league, bonus_type=custom).exists())

    def test_admin_crea_bonus_manuale_senza_proprieta(self):
        from .models import BonusType, LeagueBonus
        self.client.login(username='owner', password='x')
        self.client.post(
            reverse('league_admin', args=['lega-privata']),
            self._create_payload(bonus_name='Cattiveria', bonus_wikidata_property='',
                                 bonus_wikidata_value=''),
        )
        bt = BonusType.objects.get(name='Cattiveria')
        self.assertEqual(bt.detection_method, 'manual')
        self.assertEqual(bt.wikidata_property, '')
        self.assertTrue(LeagueBonus.objects.filter(league=self.private_league, bonus_type=bt).exists())

    def test_valore_senza_proprieta_rifiutato(self):
        from .models import BonusType
        self.client.login(username='owner', password='x')
        self.client.post(
            reverse('league_admin', args=['lega-privata']),
            self._create_payload(bonus_wikidata_property='', bonus_wikidata_value='Q41254'),
        )
        self.assertFalse(BonusType.objects.filter(name='Grammy Award').exists())

    def test_delete_custom_bonus_rimuove_anche_i_death_bonus(self):
        from .models import BonusType, Death, DeathBonus
        custom = BonusType.objects.create(
            name='Da Eliminare', league=self.private_league, points=10,
            detection_method='wikidata', wikidata_property='P166',
        )
        dead = WikipediaPerson.objects.create(wikidata_id='Q90001', name_it='Morto Test', is_dead=True)
        death = Death.objects.create(person=dead, death_date=date(2021, 3, 1),
                                     death_age=70, is_confirmed=True)
        DeathBonus.objects.create(death=death, bonus_type=custom, points_awarded=10)
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']),
                         {'action': 'delete_custom_bonus', 'bonus_type_id': str(custom.pk)})
        self.assertFalse(BonusType.objects.filter(pk=custom.pk).exists())
        self.assertFalse(DeathBonus.objects.filter(death=death).exists())


class MaxTotalAgeTest(ViewsBaseTestCase):
    """Vincolo per lega sulla somma delle età dei membri attivi."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone
        today = timezone.now().date()
        # Persona già in rosa: ~80 anni. Candidato: ~50 anni. Entrambi in
        # cache Wikidata fresca, così AddPersonView non fa richieste di rete.
        self.person.birth_date = date(today.year - 80, 1, 1)
        self.person.last_checked = timezone.now()
        self.person.save()
        self.candidate = WikipediaPerson.objects.create(
            wikidata_id='Q90100', name_it='Candidato Giovane',
            birth_date=date(today.year - 50, 1, 1), is_dead=False,
            last_checked=timezone.now(),
        )
        self.age_in_team = self.person.get_current_age()
        self.age_candidate = self.candidate.get_current_age()

    def _add(self):
        return self.client.post(
            reverse('add_person', args=[self.private_team.pk]),
            {'wikidata_id': self.candidate.wikidata_id},
        )

    def test_aggiunta_oltre_il_limite_rifiutata(self):
        self.private_league.max_total_age = self.age_in_team + self.age_candidate - 1
        self.private_league.save()
        self.client.login(username='member', password='x')
        resp = self._add()
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Limite età', resp.json()['error'])
        self.assertEqual(self.private_team.get_active_members().count(), 1)

    def test_aggiunta_entro_il_limite_accettata(self):
        self.private_league.max_total_age = self.age_in_team + self.age_candidate
        self.private_league.save()
        self.client.login(username='member', password='x')
        resp = self._add()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.private_team.get_active_members().count(), 2)

    def test_zero_significa_nessun_limite(self):
        self.private_league.max_total_age = 0
        self.private_league.save()
        self.client.login(username='member', password='x')
        self.assertEqual(self._add().status_code, 200)

    def test_sostituzione_oltre_il_limite_rifiutata(self):
        from django.utils import timezone
        from .models import Death, TeamMember
        # Il membro in rosa muore: il sostituto porterebbe la somma oltre il limite.
        self.person.is_dead = True
        self.person.death_date = timezone.now().date()
        self.person.save()
        Death.objects.create(person=self.person, death_date=timezone.now().date(),
                             death_age=80, is_confirmed=True, confirmed_at=timezone.now())
        member = self.private_team.members.get(person=self.person)
        old = WikipediaPerson.objects.create(
            wikidata_id='Q90200', name_it='Sostituto Vecchio',
            birth_date=date(1930, 1, 1), is_dead=False, last_checked=timezone.now(),
        )
        self.private_league.max_total_age = (old.get_current_age() or 0) - 1
        self.private_league.save()
        self.client.login(username='member', password='x')
        self.client.post(
            reverse('substitute_member', args=[self.private_team.pk, member.pk]),
            {'wikidata_id': old.wikidata_id},
        )
        member.refresh_from_db()
        self.assertIsNone(member.replaced_by)
        self.assertFalse(TeamMember.objects.filter(team=self.private_team, person=old).exists())


class PersonRefreshTest(ViewsBaseTestCase):
    """_get_or_refresh_person: freshness short-circuit e riconciliazione is_dead."""

    def _kill_member_and_get_substitution_url(self):
        from django.utils import timezone
        self.person.is_dead = True
        self.person.death_date = timezone.now().date()
        self.person.save()
        member = self.private_team.members.get(person=self.person)
        return reverse('substitute_member', args=[self.private_team.pk, member.pk])

    def test_sostituzione_con_persona_fresca_non_tocca_la_rete(self):
        from django.utils import timezone
        url = self._kill_member_and_get_substitution_url()
        fresh = WikipediaPerson.objects.create(
            wikidata_id='Q90300', name_it='Sostituto Fresco',
            is_dead=False, last_checked=timezone.now(),
        )
        self.client.login(username='member', password='x')
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.side_effect = AssertionError('rete non attesa')
            self.client.post(url, {'wikidata_id': fresh.wikidata_id})
        self.assertTrue(
            self.private_team.members.filter(person=fresh, replaced_by=None).exists())

    def test_sostituzione_qid_non_valido_rifiutato_senza_rete(self):
        url = self._kill_member_and_get_substitution_url()
        self.client.login(username='member', password='x')
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.side_effect = AssertionError('rete non attesa')
            self.client.post(url, {'wikidata_id': "Q1'; DROP--"})
        self.assertEqual(self.private_team.members.filter(replaced_by=None).count(), 1)

    def test_is_dead_da_solo_death_year(self):
        """Una persona con solo l'anno di morte (senza data) è comunque morta."""
        from game.views import _get_or_refresh_person
        entity = {
            'name_it': 'Solo Anno', 'name_en': '', 'description_it': '',
            'birth_date': None, 'birth_year': 1900,
            'death_date': None, 'death_year': 1980,
            'image_url': '', 'occupation': '', 'nationality': '',
            'claims_cache': {}, 'wikipedia_url_it': '',
        }
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.return_value.get_entity.return_value = entity
            person, err = _get_or_refresh_person('Q90400')
        self.assertIsNone(err)
        self.assertTrue(person.is_dead)

    def test_errore_wikidata_ritorna_messaggio(self):
        from game.views import _get_or_refresh_person
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.return_value.get_entity.side_effect = RuntimeError('boom')
            person, err = _get_or_refresh_person('Q90500')
        self.assertIsNone(person)
        self.assertIn('Errore Wikidata', err)


class AllauthBootstrapFormsTest(TestCase):
    """I form allauth devono arrivare già stilati dal server (ACCOUNT_FORMS)."""

    def test_login_ha_classi_bootstrap(self):
        resp = self.client.get(reverse('account_login'))
        self.assertContains(resp, 'form-control')

    def test_signup_ha_classi_bootstrap(self):
        resp = self.client.get(reverse('account_signup'))
        self.assertContains(resp, 'form-control')

    def test_errori_di_campo_marcati_is_invalid(self):
        # Campi obbligatori vuoti → errori per-campo → classe is-invalid.
        resp = self.client.post(reverse('account_login'), {'login': '', 'password': ''})
        self.assertContains(resp, 'is-invalid', status_code=200)


class TeamIsLockedTest(ViewsBaseTestCase):
    """Team.is_locked blocca l'editing della rosa per il manager, non per lo staff."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone
        self.private_team.is_locked = True
        self.private_team.save()
        self.candidate = WikipediaPerson.objects.create(
            wikidata_id='Q90600', name_it='Candidato', is_dead=False,
            last_checked=timezone.now(),
        )

    def test_manager_non_aggiunge_a_squadra_bloccata(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(
            reverse('add_person', args=[self.private_team.pk]),
            {'wikidata_id': self.candidate.wikidata_id},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.private_team.get_active_members().count(), 1)

    def test_manager_non_salva_modifiche_a_squadra_bloccata(self):
        self.client.login(username='member', password='x')
        resp = self.client.post(
            reverse('team_edit', args=[self.private_team.pk]),
            {'name': 'Nuovo Nome'},
        )
        self.private_team.refresh_from_db()
        self.assertEqual(self.private_team.name, 'Squadra Privata')

    def test_staff_puo_ancora_modificare(self):
        staff = User.objects.create_user('staff', password='x', is_staff=True)
        self.client.login(username='staff', password='x')
        resp = self.client.post(
            reverse('add_person', args=[self.private_team.pk]),
            {'wikidata_id': self.candidate.wikidata_id},
        )
        self.assertEqual(resp.status_code, 200)


class BulkDiffBatchLimitTest(ViewsBaseTestCase):
    """LeagueBulkDiffView richiede un blocco esplicito di persone (max 10)."""

    def _post(self, payload):
        self.client.login(username='owner', password='x')
        return self.client.post(
            reverse('league_wikidata_diff', args=['lega-privata']),
            payload, content_type='application/json',
        )

    def test_person_pks_mancante_rifiutato(self):
        resp = self._post('{}')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('person_pks', resp.json()['error'])

    def test_blocco_troppo_grande_rifiutato(self):
        import json as jsonlib
        resp = self._post(jsonlib.dumps({'person_pks': list(range(1, 13))}))
        self.assertEqual(resp.status_code, 400)

    def test_blocco_valido_processato(self):
        import json as jsonlib
        entity = {
            'name_it': 'Silvio Berlusconi', 'name_en': '', 'description_it': '',
            'birth_date': None, 'birth_year': None,
            'death_date': None, 'death_year': None,
            'image_url': '', 'occupation': '', 'nationality': '',
            'claims_cache': {}, 'wikipedia_url_it': '',
        }
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.return_value.get_entity.return_value = entity
            resp = self._post(jsonlib.dumps({'person_pks': [self.person.pk]}))
        self.assertEqual(resp.status_code, 200)
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['person_pk'], self.person.pk)

    def test_apply_troppe_persone_rifiutato(self):
        import json as jsonlib
        updates = [{'person_pk': pk, 'field': 'name_it'} for pk in range(1, 13)]
        self.client.login(username='owner', password='x')
        # Serve che i pk appartengano alla lega: creiamo 12 persone in rosa.
        team = Team.objects.create(name='T2', manager=self.owner, league=self.private_league)
        pks = []
        for i in range(12):
            p = WikipediaPerson.objects.create(wikidata_id=f'Q77{i}', name_it=f'P{i}')
            TeamMember.objects.create(team=team, person=p)
            pks.append(p.pk)
        updates = [{'person_pk': pk, 'field': 'name_it'} for pk in pks]
        resp = self.client.post(
            reverse('league_wikidata_apply', args=['lega-privata']),
            jsonlib.dumps({'updates': updates}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('blocchi', resp.json()['error'])


class LazySummaryTest(ViewsBaseTestCase):
    """Il modal persona apre subito; la biografia arriva da un endpoint dedicato."""

    def setUp(self):
        super().setUp()
        self.person.wikipedia_url_it = 'https://it.wikipedia.org/wiki/Silvio_Berlusconi'
        self.person.save()
        self.client.login(username='member', password='x')

    def test_person_info_non_chiama_mai_wikipedia(self):
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.side_effect = AssertionError('rete non attesa')
            resp = self.client.get(reverse('person_info', args=[self.person.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['summary_stale'])

    def test_person_info_summary_fresco_non_stale(self):
        from django.utils import timezone
        self.person.summary_it = 'Bio.'
        self.person.summary_fetched_at = timezone.now()
        self.person.save()
        resp = self.client.get(reverse('person_info', args=[self.person.pk]))
        self.assertFalse(resp.json()['summary_stale'])
        self.assertEqual(resp.json()['summary_it'], 'Bio.')

    def test_person_summary_esegue_e_persiste_il_refresh(self):
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.return_value.get_summary.return_value = 'Biografia nuova.'
            resp = self.client.get(reverse('person_summary', args=[self.person.pk]))
        data = resp.json()
        self.assertEqual(data['summary_it'], 'Biografia nuova.')
        self.assertFalse(data['summary_stale'])
        self.person.refresh_from_db()
        self.assertEqual(self.person.summary_it, 'Biografia nuova.')
        self.assertIsNotNone(self.person.summary_fetched_at)

    def test_person_summary_fallito_resta_stale(self):
        with patch('game.views.WikidataClient') as mock_client:
            mock_client.return_value.get_summary.side_effect = RuntimeError('timeout')
            resp = self.client.get(reverse('person_summary', args=[self.person.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['summary_stale'])


class LeagueScoringPageTest(ViewsBaseTestCase):
    """Riepilogo regolamento/punteggi per lega, visibile a tutti i membri."""

    def setUp(self):
        super().setUp()
        from .models import BonusType, LeagueBonus
        self.bt = BonusType.objects.create(
            name='Bonus Wikidata Test', points=20, detection_method='wikidata',
            wikidata_property='P166', wikidata_value='Q7191',
        )
        LeagueBonus.objects.create(
            league=self.private_league, bonus_type=self.bt, override_points=35,
        )

    def test_membro_non_admin_vede_il_regolamento_con_i_punti(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_scoring', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'game/league_scoring.html')
        self.assertContains(resp, 'Bonus Wikidata Test')
        self.assertContains(resp, '+35')                # override della lega, non il default
        self.assertContains(resp, 'P166=Q7191')         # la logica reale della detection

    def test_estraneo_di_lega_privata_rediretto(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('league_scoring', args=['lega-privata']))
        self.assertRedirects(resp, reverse('league_list'))

    def test_bonus_disattivato_non_compare(self):
        from .models import LeagueBonus
        LeagueBonus.objects.filter(league=self.private_league).update(is_active=False)
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_scoring', args=['lega-privata']))
        self.assertNotContains(resp, 'Bonus Wikidata Test')


class ManualBonusAssignTest(ViewsBaseTestCase):
    """Assegnazione/rimozione manuale dei bonus dalla pagina decessi della lega."""

    def setUp(self):
        super().setUp()
        from .models import BonusType, Death, LeagueBonus
        self.manual_bt = BonusType.objects.create(
            name='Bonus Manuale Test', points=25, detection_method='manual',
        )
        self.lb = LeagueBonus.objects.create(
            league=self.private_league, bonus_type=self.manual_bt,
        )
        self.dead = WikipediaPerson.objects.create(
            wikidata_id='Q90300', name_it='Defunto Test', is_dead=True,
        )
        self.death = Death.objects.create(
            person=self.dead, death_date=date(2021, 5, 1), death_age=70, is_confirmed=True,
        )

    def _assign(self, **overrides):
        data = {
            'action': 'assign_bonus',
            'death_id': str(self.death.pk),
            'bonus_type_id': str(self.manual_bt.pk),
        }
        data.update(overrides)
        return self.client.post(reverse('league_deaths', args=['lega-privata']), data)

    def test_admin_assegna_bonus_manuale(self):
        from .models import DeathBonus
        self.client.login(username='owner', password='x')
        self._assign()
        db = DeathBonus.objects.get(death=self.death, bonus_type=self.manual_bt)
        self.assertFalse(db.is_auto_detected)
        self.assertEqual(db.points_awarded, 25)

    def test_non_admin_non_assegna(self):
        from .models import DeathBonus
        self.client.login(username='member', password='x')
        resp = self._assign()
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(DeathBonus.objects.filter(death=self.death).exists())

    def test_bonus_non_attivo_nella_lega_rifiutato(self):
        from .models import DeathBonus
        self.lb.is_active = False
        self.lb.save()
        self.client.login(username='owner', password='x')
        self._assign()
        self.assertFalse(DeathBonus.objects.filter(death=self.death).exists())

    def test_bonus_speciale_non_assegnabile_a_mano(self):
        from .models import BonusType, DeathBonus, LeagueBonus
        special = BonusType.objects.create(
            name='Speciale Test', points=50, detection_method='first_death',
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=special)
        self.client.login(username='owner', password='x')
        self._assign(bonus_type_id=str(special.pk))
        self.assertFalse(DeathBonus.objects.filter(death=self.death).exists())

    def test_decesso_fuori_periodo_rifiutato(self):
        from .models import Death, DeathBonus
        fuori = WikipediaPerson.objects.create(
            wikidata_id='Q90301', name_it='Fuori Periodo', is_dead=True,
        )
        death_fuori = Death.objects.create(
            person=fuori, death_date=date(2010, 1, 1), death_age=90, is_confirmed=True,
        )
        self.client.login(username='owner', password='x')
        self._assign(death_id=str(death_fuori.pk))
        self.assertFalse(DeathBonus.objects.filter(death=death_fuori).exists())

    def test_admin_rimuove_bonus_assegnato_a_mano(self):
        from .models import DeathBonus
        db = DeathBonus.objects.create(
            death=self.death, bonus_type=self.manual_bt, points_awarded=25,
            is_auto_detected=False,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=['lega-privata']),
                         {'action': 'remove_bonus', 'death_bonus_id': str(db.pk)})
        self.assertFalse(DeathBonus.objects.filter(pk=db.pk).exists())

    def test_bonus_auto_di_sistema_non_rimovibile(self):
        from .models import DeathBonus
        db = DeathBonus.objects.create(
            death=self.death, bonus_type=self.manual_bt, points_awarded=25,
            is_auto_detected=True,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=['lega-privata']),
                         {'action': 'remove_bonus', 'death_bonus_id': str(db.pk)})
        self.assertTrue(DeathBonus.objects.filter(pk=db.pk).exists())

    def test_bonus_custom_auto_rimovibile_dalla_propria_lega(self):
        from .models import BonusType, DeathBonus, LeagueBonus
        custom = BonusType.objects.create(
            name='Custom Auto', league=self.private_league, points=10,
            detection_method='wikidata', wikidata_property='P166',
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=custom)
        db = DeathBonus.objects.create(
            death=self.death, bonus_type=custom, points_awarded=10, is_auto_detected=True,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=['lega-privata']),
                         {'action': 'remove_bonus', 'death_bonus_id': str(db.pk)})
        self.assertFalse(DeathBonus.objects.filter(pk=db.pk).exists())

    def test_pagina_decessi_mostra_punti_effettivi_della_lega(self):
        from .models import DeathBonus
        self.lb.override_points = 99
        self.lb.save()
        DeathBonus.objects.create(
            death=self.death, bonus_type=self.manual_bt, points_awarded=25,
            is_auto_detected=False,
        )
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_deaths', args=['lega-privata']))
        self.assertContains(resp, 'Bonus Manuale Test +99')
