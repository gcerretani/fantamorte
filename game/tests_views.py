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


def _entity(name_it, birth_date=None, death_date=None, **overrides):
    """Payload nella forma di ritorno reale di WikidataClient.get_entity()."""
    data = {
        'name_it': name_it, 'name_en': '', 'description_it': '',
        'birth_date': birth_date, 'birth_year': birth_date.year if birth_date else None,
        'death_date': death_date, 'death_year': death_date.year if death_date else None,
        'image_url': '', 'occupation': '', 'nationality': '',
        'claims_cache': {}, 'wikipedia_url_it': '', 'wiki_title_it': '',
    }
    data.update(overrides)
    return data


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

    @patch('game.views.WikidataClient')
    def test_sostituzione_oltre_il_limite_rifiutata(self, mock_client_class):
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
        # La view rifetcha sempre l'entity da Wikidata: il mock restituisce
        # gli stessi dati della persona già in DB.
        mock_client_class.return_value.get_entity.return_value = _entity(
            'Sostituto Vecchio', birth_date=date(1930, 1, 1),
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


class LeagueCreateViewTest(TestCase):
    """Creazione lega: slug, bonus di sistema, codice invito, duplicati."""

    def setUp(self):
        from .models import BonusType
        self.user = User.objects.create_user('fondatore', password='x')
        self.client.login(username='fondatore', password='x')
        self.system_bonus = BonusType.objects.create(
            name='Bonus Sistema', points=10, is_active=True,
        )
        self.inactive_bonus = BonusType.objects.create(
            name='Bonus Spento', points=10, is_active=False,
        )

    def test_creazione_lega_pubblica(self):
        from .models import League, LeagueBonus, LeagueMembership
        resp = self.client.post(reverse('league_create'), {'name': 'Lega Nuova'})
        league = League.objects.get(name='Lega Nuova')
        self.assertRedirects(resp, reverse('league_admin', args=[league.slug]))
        self.assertEqual(league.owner, self.user)
        self.assertEqual(league.invite_code, '')
        membership = LeagueMembership.objects.get(league=league, user=self.user)
        self.assertEqual(membership.role, LeagueMembership.ROLE_OWNER)
        # I bonus di sistema attivi vengono agganciati, quelli spenti no.
        self.assertTrue(LeagueBonus.objects.filter(
            league=league, bonus_type=self.system_bonus).exists())
        self.assertFalse(LeagueBonus.objects.filter(
            league=league, bonus_type=self.inactive_bonus).exists())

    def test_creazione_lega_privata_genera_codice_invito(self):
        from .models import League
        self.client.post(reverse('league_create'),
                         {'name': 'Lega Segreta', 'visibility': 'private'})
        league = League.objects.get(name='Lega Segreta')
        self.assertEqual(league.visibility, League.VISIBILITY_PRIVATE)
        self.assertGreater(len(league.invite_code), 0)

    def test_visibilita_invalida_ripiega_su_pubblica(self):
        from .models import League
        self.client.post(reverse('league_create'),
                         {'name': 'Lega Strana', 'visibility': 'top-secret'})
        league = League.objects.get(name='Lega Strana')
        self.assertEqual(league.visibility, League.VISIBILITY_PUBLIC)

    def test_nome_duplicato_case_insensitive_rifiutato(self):
        from .models import League
        self.client.post(reverse('league_create'), {'name': 'Lega Unica'})
        resp = self.client.post(reverse('league_create'), {'name': 'lega unica'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(League.objects.filter(name__iexact='lega unica').count(), 1)

    def test_nome_vuoto_rifiutato(self):
        from .models import League
        resp = self.client.post(reverse('league_create'), {'name': '   '})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(League.objects.count(), 0)

    def test_collisione_slug_riceve_suffisso(self):
        from .models import League
        self.client.post(reverse('league_create'), {'name': 'Slug Test!'})
        self.client.post(reverse('league_create'), {'name': 'Slug Test?'})
        slugs = set(League.objects.values_list('slug', flat=True))
        self.assertEqual(slugs, {'slug-test', 'slug-test-2'})


class LeagueMembersAdminTest(ViewsBaseTestCase):
    """Azioni membri del pannello admin: promozioni, rimozioni, trasferimento."""

    def _post(self, action, membership, username='owner'):
        self.client.login(username=username, password='x')
        return self.client.post(
            reverse('league_admin', args=['lega-privata']),
            {'action': action, 'membership_id': str(membership.pk)},
        )

    def _membership(self, user):
        return self.private_league.memberships.get(user=user)

    def test_owner_promuove_membro_ad_admin(self):
        from .models import LeagueMembership
        m = self._membership(self.member)
        self._post('promote_admin', m)
        m.refresh_from_db()
        self.assertEqual(m.role, LeagueMembership.ROLE_ADMIN)

    def test_admin_non_owner_non_puo_promuovere(self):
        from .models import LeagueMembership
        # member diventa admin, outsider entra come membro semplice.
        member_ms = self._membership(self.member)
        member_ms.role = LeagueMembership.ROLE_ADMIN
        member_ms.save()
        outsider_ms = LeagueMembership.objects.create(
            league=self.private_league, user=self.outsider,
            role=LeagueMembership.ROLE_MEMBER,
        )
        self._post('promote_admin', outsider_ms, username='member')
        outsider_ms.refresh_from_db()
        self.assertEqual(outsider_ms.role, LeagueMembership.ROLE_MEMBER)

    def test_owner_retrocede_admin_a_membro(self):
        from .models import LeagueMembership
        m = self._membership(self.member)
        m.role = LeagueMembership.ROLE_ADMIN
        m.save()
        self._post('demote_admin', m)
        m.refresh_from_db()
        self.assertEqual(m.role, LeagueMembership.ROLE_MEMBER)

    def test_ruolo_owner_non_modificabile(self):
        from .models import LeagueMembership
        owner_ms = self._membership(self.owner)
        self._post('demote_admin', owner_ms)
        owner_ms.refresh_from_db()
        self.assertEqual(owner_ms.role, LeagueMembership.ROLE_OWNER)

    def test_rimozione_membro_elimina_anche_la_squadra(self):
        from .models import LeagueMembership
        m = self._membership(self.member)
        self._post('remove_member', m)
        self.assertFalse(LeagueMembership.objects.filter(pk=m.pk).exists())
        self.assertFalse(Team.objects.filter(pk=self.private_team.pk).exists())

    def test_owner_non_rimovibile(self):
        from .models import LeagueMembership
        owner_ms = self._membership(self.owner)
        self._post('remove_member', owner_ms)
        self.assertTrue(LeagueMembership.objects.filter(pk=owner_ms.pk).exists())

    def test_trasferimento_proprieta(self):
        from .models import LeagueMembership
        m = self._membership(self.member)
        self._post('transfer_ownership', m)
        self.private_league.refresh_from_db()
        m.refresh_from_db()
        old_owner_ms = self._membership(self.owner)
        self.assertEqual(self.private_league.owner, self.member)
        self.assertEqual(m.role, LeagueMembership.ROLE_OWNER)
        self.assertEqual(old_owner_ms.role, LeagueMembership.ROLE_ADMIN)

    def test_trasferimento_negato_a_non_owner(self):
        from .models import LeagueMembership
        member_ms = self._membership(self.member)
        member_ms.role = LeagueMembership.ROLE_ADMIN
        member_ms.save()
        self._post('transfer_ownership', member_ms, username='member')
        self.private_league.refresh_from_db()
        self.assertEqual(self.private_league.owner, self.owner)

    def test_rigenerazione_codice_invito(self):
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_admin', args=['lega-privata']),
                         {'action': 'rotate_invite'})
        self.private_league.refresh_from_db()
        self.assertNotEqual(self.private_league.invite_code, 'segretissimo')
        self.assertGreater(len(self.private_league.invite_code), 0)

    def test_set_bonus_toggle_e_override(self):
        from .models import BonusType, LeagueBonus
        bt = BonusType.objects.create(name='Bonus Toggle', points=10, is_active=True)
        lb = LeagueBonus.objects.create(
            league=self.private_league, bonus_type=bt, is_active=True,
        )
        self.client.login(username='owner', password='x')
        # Checkbox non spuntata = disattivato; override punti a 42.
        self.client.post(reverse('league_admin', args=['lega-privata']),
                         {'action': 'set_bonus', f'bonus_points_{lb.pk}': '42'})
        lb.refresh_from_db()
        self.assertFalse(lb.is_active)
        self.assertEqual(lb.override_points, 42)


class LeagueLeaveTest(ViewsBaseTestCase):

    def test_membro_abbandona_e_perde_la_squadra(self):
        from .models import LeagueMembership
        self.client.login(username='member', password='x')
        resp = self.client.post(reverse('league_leave', args=['lega-privata']))
        self.assertRedirects(resp, reverse('home'))
        self.assertFalse(LeagueMembership.objects.filter(
            league=self.private_league, user=self.member).exists())
        self.assertFalse(Team.objects.filter(pk=self.private_team.pk).exists())

    def test_owner_non_puo_abbandonare(self):
        from .models import LeagueMembership
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_leave', args=['lega-privata']))
        self.assertTrue(LeagueMembership.objects.filter(
            league=self.private_league, user=self.owner).exists())


class TeamCreateViewTest(ViewsBaseTestCase):

    def test_non_membro_viene_rimandato_alla_lega(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('team_create', args=['lega-pubblica']))
        self.assertRedirects(resp, reverse('league_detail', args=['lega-pubblica']))

    def test_membro_senza_squadra_vede_il_form(self):
        # L'owner della lega privata è membro ma non ha ancora una squadra.
        self.client.login(username='owner', password='x')
        resp = self.client.get(reverse('team_create', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)

    def test_post_crea_la_squadra(self):
        self.client.login(username='owner', password='x')
        resp = self.client.post(reverse('team_create', args=['lega-privata']),
                                {'name': 'Falci Riunite'})
        team = Team.objects.get(manager=self.owner, league=self.private_league)
        self.assertEqual(team.name, 'Falci Riunite')
        self.assertRedirects(resp, reverse('team_edit', args=[team.pk]))

    def test_nome_vuoto_rifiutato(self):
        self.client.login(username='owner', password='x')
        resp = self.client.post(reverse('team_create', args=['lega-privata']), {'name': ''})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Team.objects.filter(
            manager=self.owner, league=self.private_league).exists())

    def test_chi_ha_gia_una_squadra_va_in_modifica(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('team_create', args=['lega-privata']))
        self.assertRedirects(resp, reverse('team_edit', args=[self.private_team.pk]))

    def test_registrazioni_chiuse_bloccano_la_creazione(self):
        from django.utils import timezone
        from datetime import timedelta
        self.private_league.registration_closes = timezone.now().date() - timedelta(days=1)
        self.private_league.save()
        self.client.login(username='owner', password='x')
        resp = self.client.post(reverse('team_create', args=['lega-privata']),
                                {'name': 'Fuori Tempo'})
        self.assertRedirects(resp, reverse('league_detail', args=['lega-privata']))
        self.assertFalse(Team.objects.filter(
            manager=self.owner, league=self.private_league).exists())


class AddPersonViewTest(ViewsBaseTestCase):
    """Flusso di aggiunta persona alla squadra (endpoint AJAX)."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone
        # Cache Wikidata fresca: nessuna richiesta di rete durante i test.
        self.person.last_checked = timezone.now()
        self.person.save()
        self.candidate = WikipediaPerson.objects.create(
            wikidata_id='Q91000', name_it='Candidato Vivo',
            is_dead=False, last_checked=timezone.now(),
        )
        self.client.login(username='member', password='x')

    def _add(self, wikidata_id, is_captain=False):
        data = {'wikidata_id': wikidata_id}
        if is_captain:
            data['is_captain'] = '1'
        return self.client.post(reverse('add_person', args=[self.private_team.pk]), data)

    def test_aggiunta_ok(self):
        resp = self._add(self.candidate.wikidata_id)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['success'])
        self.assertEqual(body['name'], 'Candidato Vivo')
        member = self.private_team.members.get(person=self.candidate)
        self.assertFalse(member.is_captain)

    def test_aggiunta_capitano_e_limite(self):
        resp = self._add(self.candidate.wikidata_id, is_captain=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.private_team.members.get(person=self.candidate).is_captain)
        # max_captains=1 (default): il secondo capitano viene rifiutato.
        from django.utils import timezone
        second = WikipediaPerson.objects.create(
            wikidata_id='Q91001', name_it='Secondo Capitano',
            is_dead=False, last_checked=timezone.now(),
        )
        resp = self._add(second.wikidata_id, is_captain=True)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('capitano', resp.json()['error'])

    def test_limite_morituri(self):
        self.private_league.max_non_captains = 1
        self.private_league.save()
        # self.person occupa già l'unico posto da non-capitano.
        resp = self._add(self.candidate.wikidata_id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('morituri', resp.json()['error'])

    def test_persona_morta_rifiutata(self):
        self.candidate.is_dead = True
        self.candidate.save()
        resp = self._add(self.candidate.wikidata_id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('già morto', resp.json()['error'])

    def test_duplicato_rifiutato(self):
        resp = self._add(self.person.wikidata_id)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('già nella squadra', resp.json()['error'])

    def test_wikidata_id_invalido_o_mancante(self):
        self.assertEqual(self._add('DROP TABLE').status_code, 400)
        self.assertEqual(self._add('').status_code, 400)

    def test_lega_bloccata_rifiuta_le_modifiche(self):
        self.private_league.is_locked = True
        self.private_league.save()
        resp = self._add(self.candidate.wikidata_id)
        self.assertEqual(resp.status_code, 400)

    @patch('game.views.WikidataClient')
    def test_persona_nuova_viene_creata_da_wikidata(self, mock_client_class):
        mock_client_class.return_value.get_entity.return_value = _entity(
            'Persona Nuova', birth_date=date(1960, 5, 5),
        )
        resp = self._add('Q777777')
        self.assertEqual(resp.status_code, 200)
        created = WikipediaPerson.objects.get(wikidata_id='Q777777')
        self.assertEqual(created.name_it, 'Persona Nuova')
        self.assertTrue(self.private_team.members.filter(person=created).exists())

    @patch('game.views.WikidataClient')
    def test_errore_wikidata_restituisce_500_senza_creare_nulla(self, mock_client_class):
        mock_client_class.return_value.get_entity.side_effect = RuntimeError('down')
        resp = self._add('Q777778')
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(WikipediaPerson.objects.filter(wikidata_id='Q777778').exists())


class SubstituteMemberFlowTest(ViewsBaseTestCase):
    """Flusso completo di sostituzione: deadline, catena replaced_by, capitano."""

    def setUp(self):
        super().setUp()
        from django.utils import timezone
        from datetime import timedelta
        from .models import Death
        self.person.is_dead = True
        self.person.death_date = timezone.now().date()
        self.person.save()
        self.death = Death.objects.create(
            person=self.person, death_date=timezone.now().date(), death_age=80,
            is_confirmed=True, confirmed_at=timezone.now() - timedelta(days=1),
        )
        self.member = self.private_team.members.get(person=self.person)
        self.client.login(username='member', password='x')
        self.url = reverse('substitute_member',
                           args=[self.private_team.pk, self.member.pk])

    def test_get_membro_morto_entro_deadline_mostra_il_form(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_get_membro_vivo_reindirizza(self):
        alive = WikipediaPerson.objects.create(wikidata_id='Q92000', name_it='Vivo')
        alive_member = TeamMember.objects.create(team=self.private_team, person=alive)
        resp = self.client.get(reverse(
            'substitute_member', args=[self.private_team.pk, alive_member.pk]))
        self.assertRedirects(resp, reverse('team_edit', args=[self.private_team.pk]))

    def test_deadline_scaduta_blocca_get_e_post(self):
        from django.utils import timezone
        from datetime import timedelta
        from .models import Death
        # substitution_deadline_days=7 (default): conferma 8 giorni fa = scaduta.
        Death.objects.filter(pk=self.death.pk).update(
            confirmed_at=timezone.now() - timedelta(days=8))
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse('team_edit', args=[self.private_team.pk]))
        self.client.post(self.url, {'wikidata_id': 'Q92001'})
        self.member.refresh_from_db()
        self.assertIsNone(self.member.replaced_by)

    @patch('game.views.WikidataClient')
    def test_sostituzione_ok_preserva_il_ruolo_capitano(self, mock_client_class):
        self.member.is_captain = True
        self.member.save()
        mock_client_class.return_value.get_entity.return_value = _entity(
            'Erede Fresco', birth_date=date(1970, 1, 1),
        )
        resp = self.client.post(self.url, {'wikidata_id': 'Q92002'})
        self.assertRedirects(resp, reverse('team_edit', args=[self.private_team.pk]))
        self.member.refresh_from_db()
        new_member = self.member.replaced_by
        self.assertIsNotNone(new_member)
        self.assertEqual(new_member.person.name_it, 'Erede Fresco')
        self.assertTrue(new_member.is_captain)
        self.assertFalse(self.member.is_active())
        self.assertTrue(new_member.is_active())

    @patch('game.views.WikidataClient')
    def test_sostituto_gia_morto_rifiutato(self, mock_client_class):
        mock_client_class.return_value.get_entity.return_value = _entity(
            'Erede Defunto', birth_date=date(1930, 1, 1),
            death_date=date(2020, 1, 1),
        )
        self.client.post(self.url, {'wikidata_id': 'Q92003'})
        self.member.refresh_from_db()
        self.assertIsNone(self.member.replaced_by)

    def test_wikidata_id_mancante_reindirizza_al_form(self):
        resp = self.client.post(self.url, {})
        self.assertRedirects(resp, self.url)
        self.member.refresh_from_db()
        self.assertIsNone(self.member.replaced_by)


class TeamEditFlowTest(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.member_row = self.private_team.members.get(person=self.person)
        self.client.login(username='member', password='x')
        self.url = reverse('team_edit', args=[self.private_team.pk])

    def test_nomina_capitano(self):
        self.client.post(self.url, {'captain_id': str(self.member_row.pk)})
        self.member_row.refresh_from_db()
        self.assertTrue(self.member_row.is_captain)

    def test_cambio_capitano_sposta_la_fascia(self):
        self.member_row.is_captain = True
        self.member_row.save()
        other = WikipediaPerson.objects.create(wikidata_id='Q93000', name_it='Vice')
        other_member = TeamMember.objects.create(team=self.private_team, person=other)
        self.client.post(self.url, {'captain_id': str(other_member.pk)})
        self.member_row.refresh_from_db()
        other_member.refresh_from_db()
        self.assertFalse(self.member_row.is_captain)
        self.assertTrue(other_member.is_captain)

    def test_jolly_valido_impostato(self):
        self.client.post(self.url, {'jolly_month': '5'})
        self.private_team.refresh_from_db()
        self.assertEqual(self.private_team.jolly_month, 5)

    def test_jolly_disabilitato_nella_lega_viene_ignorato(self):
        self.private_league.jolly_enabled = False
        self.private_league.save()
        self.client.post(self.url, {'jolly_month': '5'})
        self.private_team.refresh_from_db()
        self.assertIsNone(self.private_team.jolly_month)

    def test_rinomina_squadra(self):
        self.client.post(self.url, {'name': 'Nuovo Nome'})
        self.private_team.refresh_from_db()
        self.assertEqual(self.private_team.name, 'Nuovo Nome')

    def test_registrazioni_chiuse_bloccano_le_modifiche(self):
        from django.utils import timezone
        from datetime import timedelta
        self.private_league.registration_closes = timezone.now().date() - timedelta(days=1)
        self.private_league.save()
        self.client.post(self.url, {'name': 'Troppo Tardi'})
        self.private_team.refresh_from_db()
        self.assertEqual(self.private_team.name, 'Squadra Privata')


class ProfileViewTest(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.client.login(username='member', password='x')

    def test_get_mostra_le_preferenze(self):
        resp = self.client.get(reverse('profile'))
        self.assertEqual(resp.status_code, 200)

    def test_post_aggiorna_le_preferenze(self):
        from .models import UserProfile
        resp = self.client.post(reverse('profile'), {
            'email_notifications_enabled': 'on',
            'theme_preference': 'dark',
        })
        self.assertRedirects(resp, reverse('profile'))
        profile = UserProfile.objects.get(user=self.member)
        self.assertFalse(profile.push_notifications_enabled)
        self.assertTrue(profile.email_notifications_enabled)
        self.assertEqual(profile.theme_preference, 'dark')

    def test_tema_invalido_viene_ignorato(self):
        from .models import UserProfile
        self.client.post(reverse('profile'), {'theme_preference': 'neon'})
        profile = UserProfile.objects.get(user=self.member)
        self.assertEqual(profile.theme_preference, UserProfile.THEME_AUTO)


class PersonSearchViewTest(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.client.login(username='member', password='x')
        self.url = reverse('person_search')

    def test_query_troppo_corta_restituisce_lista_vuota(self):
        resp = self.client.get(self.url, {'q': 'a'})
        self.assertEqual(resp.json(), {'results': []})

    @patch('game.views.WikidataClient')
    def test_risultati_e_cache(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.search_by_italian_name.return_value = (
            [{'id': 'Q1', 'label': 'Persona Uno'}], False,
        )
        q = 'cache-hit-9f3a'  # query unica: la cache locmem sopravvive fra i test
        first = self.client.get(self.url, {'q': q})
        self.assertEqual(first.json()['results'][0]['id'], 'Q1')
        second = self.client.get(self.url, {'q': q})
        self.assertEqual(second.json()['results'][0]['id'], 'Q1')
        self.assertEqual(instance.search_by_italian_name.call_count, 1)

    @patch('game.views.WikidataClient')
    def test_sparql_fallito_avvisa_e_non_cachea(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.search_by_italian_name.return_value = ([], True)
        q = 'sparql-down-77b1'
        resp = self.client.get(self.url, {'q': q})
        self.assertIn('warning', resp.json())
        self.client.get(self.url, {'q': q})
        self.assertEqual(instance.search_by_italian_name.call_count, 2)

    @patch('game.views.WikidataClient')
    def test_eccezione_del_client_non_va_in_500(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.search_by_italian_name.side_effect = RuntimeError('rete giù')
        resp = self.client.get(self.url, {'q': 'errore-4c2d'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['results'], [])

    @patch('game.views.WikidataClient')
    def test_filtro_lingue_della_lega(self, mock_client_class):
        instance = mock_client_class.return_value
        instance.search_by_italian_name.return_value = ([], False)
        self.private_league.search_wikipedia_langs = 'itwiki,enwiki'
        self.private_league.save()
        self.client.get(self.url, {'q': 'lingue-8d1e', 'league': 'lega-privata'})
        _, kwargs = instance.search_by_italian_name.call_args
        self.assertEqual(kwargs.get('require_wikis'), ['itwiki', 'enwiki'])


class PersonInfoViewTest(ViewsBaseTestCase):

    def test_json_con_i_campi_essenziali(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('person_info', args=[self.person.pk]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['name_it'], 'Silvio Berlusconi')
        self.assertEqual(data['wikidata_id'], 'Q11860')
        self.assertFalse(data['is_dead'])
        self.assertTrue(data['wikidata_url'].endswith('Q11860'))

    def test_persona_inesistente_404(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('person_info', args=[99999]))
        self.assertEqual(resp.status_code, 404)


class PushApiTest(ViewsBaseTestCase):

    SUB = ('{"endpoint": "https://push.example/ep1",'
           ' "keys": {"p256dh": "chiave", "auth": "segreto"}}')

    def setUp(self):
        super().setUp()
        self.client.login(username='member', password='x')

    def test_subscribe_registra_endpoint(self):
        from .models import PushSubscription
        resp = self.client.post(reverse('push_subscribe'), data=self.SUB,
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['created'])
        sub = PushSubscription.objects.get(endpoint='https://push.example/ep1')
        self.assertEqual(sub.user, self.member)

    def test_subscribe_incompleta_rifiutata(self):
        resp = self.client.post(
            reverse('push_subscribe'),
            data='{"endpoint": "https://push.example/ep2", "keys": {}}',
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_subscribe_json_invalido_rifiutato(self):
        resp = self.client.post(reverse('push_subscribe'), data='non-json',
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_unsubscribe_cancella_solo_i_propri_endpoint(self):
        from .models import PushSubscription
        PushSubscription.objects.create(
            user=self.member, endpoint='https://push.example/mio',
            p256dh='k', auth='a')
        PushSubscription.objects.create(
            user=self.outsider, endpoint='https://push.example/altrui',
            p256dh='k', auth='a')
        resp = self.client.post(
            reverse('push_unsubscribe'),
            data='{"endpoint": "https://push.example/altrui"}',
            content_type='application/json')
        self.assertEqual(resp.json()['deleted'], 0)
        resp = self.client.post(
            reverse('push_unsubscribe'),
            data='{"endpoint": "https://push.example/mio"}',
            content_type='application/json')
        self.assertEqual(resp.json()['deleted'], 1)
        self.assertTrue(PushSubscription.objects.filter(
            endpoint='https://push.example/altrui').exists())

    def test_push_test_senza_iscrizioni_400(self):
        resp = self.client.post(reverse('push_test'))
        self.assertEqual(resp.status_code, 400)

    @patch('game.push.send_push', return_value=True)
    def test_push_test_invia_alle_iscrizioni(self, mock_send):
        from .models import PushSubscription
        PushSubscription.objects.create(
            user=self.member, endpoint='https://push.example/test',
            p256dh='k', auth='a')
        resp = self.client.post(reverse('push_test'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['sent'], 1)
        mock_send.assert_called_once()


class PwaViewsTest(TestCase):
    """Manifest e service worker: pubblici e ben formati."""

    def test_manifest_json(self):
        resp = Client().get('/manifest.webmanifest')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['name'], 'Fantamorte')
        self.assertGreater(len(data['icons']), 0)
        self.assertEqual(data['start_url'], '/')

    def test_service_worker_javascript_con_cache_versionata(self):
        resp = Client().get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/javascript')
        self.assertIn(b"const CACHE = 'fantamorte-v", resp.content)


class HomePageTest(ViewsBaseTestCase):

    def test_mostra_le_mie_leghe_e_suggerisce_le_pubbliche(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        # member è iscritto alla privata; la pubblica è suggerita.
        self.assertContains(resp, 'Lega Privata')
        self.assertContains(resp, 'Lega Pubblica')

    def test_lega_gia_joinata_non_viene_suggerita(self):
        from .models import LeagueMembership
        LeagueMembership.objects.create(
            league=self.public_league, user=self.member,
            role=LeagueMembership.ROLE_MEMBER)
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('home'))
        self.assertEqual(len(resp.context['suggested_leagues']), 0)


class LeaguePagesPrivacyTest(ViewsBaseTestCase):
    """Classifica e decessi di una lega privata: solo per i membri."""

    def test_classifica_privata_negata_ai_non_membri(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('league_rankings', args=['lega-privata']))
        self.assertRedirects(resp, reverse('league_list'))

    def test_classifica_visibile_al_membro(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_rankings', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)

    def test_decessi_privati_negati_ai_non_membri(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('league_deaths', args=['lega-privata']))
        self.assertRedirects(resp, reverse('league_list'))

    def test_decessi_visibili_al_membro(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_deaths', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)


class DeathDetailPrivacyTest(ViewsBaseTestCase):
    """La pagina decesso non deve rivelare squadre di leghe private a estranei."""

    def setUp(self):
        super().setUp()
        from .models import Death
        from django.utils import timezone
        self.person.is_dead = True
        self.person.death_date = date(2025, 1, 15)
        self.person.save()
        self.death = Death.objects.create(
            person=self.person, death_date=date(2025, 1, 15), death_age=80,
            is_confirmed=True, confirmed_at=timezone.now())

    def test_membro_vede_le_squadre_coinvolte(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('death_detail', args=[self.death.pk]))
        self.assertEqual(resp.status_code, 200)
        teams = [t['team'] for t in resp.context['teams_affected']]
        self.assertIn(self.private_team, teams)

    def test_estraneo_non_vede_le_squadre_di_leghe_private(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('death_detail', args=[self.death.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['teams_affected'], [])


class BulkDiffTest(ViewsBaseTestCase):

    def _post(self, body='{}'):
        return self.client.post(
            reverse('league_wikidata_diff', args=['lega-privata']),
            data=body, content_type='application/json')

    def test_non_admin_rifiutato(self):
        self.client.login(username='member', password='x')
        self.assertEqual(self._post().status_code, 403)

    def test_json_invalido_rifiutato(self):
        self.client.login(username='owner', password='x')
        self.assertEqual(self._post('{{{').status_code, 400)

    @patch('wikidata_api.client.WikidataClient.get_entity')
    def test_diff_rileva_il_cambio_nome(self, mock_entity):
        mock_entity.return_value = _entity('Nome Cambiato')
        self.client.login(username='owner', password='x')
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        results = resp.json()['results']
        self.assertEqual(len(results), 1)
        changed_fields = {c['field']: c for c in results[0]['changes']}
        self.assertIn('name_it', changed_fields)
        self.assertEqual(changed_fields['name_it']['old'], 'Silvio Berlusconi')
        self.assertEqual(changed_fields['name_it']['new'], 'Nome Cambiato')

    @patch('wikidata_api.client.WikidataClient.get_entity')
    def test_errore_wikidata_riportato_per_persona(self, mock_entity):
        mock_entity.side_effect = RuntimeError('timeout')
        self.client.login(username='owner', password='x')
        resp = self._post()
        results = resp.json()['results']
        self.assertIn('timeout', results[0]['error'])
        self.assertEqual(results[0]['changes'], [])


class WhatIfViewTest(ViewsBaseTestCase):

    def test_non_proprietario_403(self):
        self.client.login(username='outsider', password='x')
        resp = self.client.get(reverse('team_what_if', args=[self.private_team.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_proprietario_vede_la_simulazione(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('team_what_if', args=[self.private_team.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Silvio Berlusconi')

    def test_mese_fuori_range_viene_normalizzato(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('team_what_if', args=[self.private_team.pk]),
                               {'month': '99'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['month'], 12)


class LeaguePlayersRefreshPageTest(ViewsBaseTestCase):

    def test_non_admin_403(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('league_players_refresh', args=['lega-privata']))
        self.assertEqual(resp.status_code, 403)

    def test_admin_vede_i_giocatori_della_lega(self):
        self.client.login(username='owner', password='x')
        resp = self.client.get(reverse('league_players_refresh', args=['lega-privata']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Silvio Berlusconi')
