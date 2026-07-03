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
