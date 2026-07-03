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
