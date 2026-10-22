"""Nuova lega a partire da una esistente (#101)."""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .league_clone import shift_year, suggest_name
from .models import (
    BonusType, League, LeagueBonus, LeagueMembership, Notification, Team, TeamMember,
    WikipediaPerson,
)

User = get_user_model()


class LeagueCloneBase(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('owner', password='x')
        self.admin = User.objects.create_user('admin', password='x')
        self.member = User.objects.create_user('member', password='x')
        self.outsider = User.objects.create_user('outsider', password='x')
        self.source = League.objects.create(
            name='Fantamorte 2026', slug='fm-2026', owner=self.owner,
            visibility=League.VISIBILITY_PRIVATE,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            registration_opens=date(2025, 11, 1), registration_closes=date(2026, 1, 1),
            base_points=40, captain_multiplier=3, jolly_multiplier=2, max_captains=2,
            max_non_captains=9, max_total_age=600, jolly_enabled=False,
            substitution_deadline_days=5, captain_succession=League.CAPTAIN_SUCCESSION_FREE,
            secret_rosters_preseason=True, search_wikipedia_langs='itwiki,enwiki',
            description='Regole della casa',
        )
        for user, role in ((self.owner, 'owner'), (self.admin, 'admin'), (self.member, 'member')):
            LeagueMembership.objects.create(league=self.source, user=user, role=role)
        self.system_bt = BonusType.objects.create(name='Giovane', points=10)
        self.custom_bt = BonusType.objects.create(
            name='Grammy', points=30, league=self.source, detection_method='wikidata',
            wikidata_property='P166', wikidata_value='Q41254')
        self.inactive_bt = BonusType.objects.create(name='Spento', points=5)
        LeagueBonus.objects.create(league=self.source, bonus_type=self.system_bt, override_points=15)
        LeagueBonus.objects.create(league=self.source, bonus_type=self.custom_bt)
        LeagueBonus.objects.create(league=self.source, bonus_type=self.inactive_bt, is_active=False)
        team = Team.objects.create(name='Squadra', manager=self.member, league=self.source)
        TeamMember.objects.create(
            team=team, person=WikipediaPerson.objects.create(wikidata_id='Q1', name_it='Tizio'))
        self.url = reverse('league_clone', args=[self.source.slug])

    def post(self, **overrides):
        data = {
            'name': 'Fantamorte 2027', 'visibility': 'private',
            'registration_opens': '2026-11-01', 'registration_closes': '2027-01-01',
            'start_date': '2027-01-01', 'end_date': '2027-12-31', 'invite_members': '1',
        }
        data.update(overrides)
        return self.client.post(self.url, data)


class HelpersTest(TestCase):

    def test_shift_year(self):
        self.assertEqual(shift_year(date(2026, 3, 4)), date(2027, 3, 4))
        self.assertEqual(shift_year(date(2028, 2, 29)), date(2029, 2, 28))

    def test_suggest_name(self):
        self.assertEqual(suggest_name('Fantamorte 2026'), 'Fantamorte 2027')
        self.assertEqual(suggest_name('Lega degli amici'), 'Lega degli amici (nuova edizione)')


class LeagueCloneViewTest(LeagueCloneBase):

    def test_form_precompilato_con_date_spostate(self):
        self.client.login(username='owner', password='x')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.context['data']
        self.assertEqual(data['name'], 'Fantamorte 2027')
        self.assertEqual(data['start_date'], '2027-01-01')
        self.assertEqual(data['registration_opens'], '2026-11-01')
        self.assertEqual(resp.context['invitable_count'], 2)

    def test_regole_e_bonus_copiati(self):
        self.client.login(username='admin', password='x')
        resp = self.post()
        new = League.objects.get(name='Fantamorte 2027')
        self.assertRedirects(resp, reverse('league_admin', args=[new.slug]))
        for field in ('base_points', 'captain_multiplier', 'max_captains', 'max_non_captains',
                      'max_total_age', 'jolly_enabled', 'substitution_deadline_days',
                      'captain_succession', 'secret_rosters_preseason',
                      'search_wikipedia_langs', 'description'):
            self.assertEqual(getattr(new, field), getattr(self.source, field), field)
        self.assertEqual(new.start_date, date(2027, 1, 1))
        self.assertEqual(new.owner, self.admin)
        self.assertTrue(new.invite_code)
        self.assertNotEqual(new.invite_code, self.source.invite_code)
        self.assertFalse(new.is_locked)

        bonuses = {lb.bonus_type.name: lb for lb in new.league_bonuses.select_related('bonus_type')}
        self.assertEqual(set(bonuses), {'Giovane', 'Grammy', 'Spento'})
        self.assertEqual(bonuses['Giovane'].override_points, 15)
        self.assertEqual(bonuses['Giovane'].bonus_type, self.system_bt)  # di sistema: condiviso
        self.assertFalse(bonuses['Spento'].is_active)
        # Il personalizzato è duplicato sulla nuova lega, non condiviso.
        grammy = bonuses['Grammy'].bonus_type
        self.assertNotEqual(grammy.pk, self.custom_bt.pk)
        self.assertEqual(grammy.league, new)
        self.assertEqual((grammy.wikidata_property, grammy.wikidata_value), ('P166', 'Q41254'))

    def test_niente_squadre_ne_iscritti_tranne_chi_crea(self):
        self.client.login(username='owner', password='x')
        self.post()
        new = League.objects.get(name='Fantamorte 2027')
        self.assertFalse(new.teams.exists())
        self.assertEqual(
            list(new.memberships.values_list('user__username', 'role')), [('owner', 'owner')])

    def test_inviti_agli_iscritti(self):
        self.client.login(username='owner', password='x')
        self.post()
        new = League.objects.get(name='Fantamorte 2027')
        invites = Notification.objects.filter(kind=Notification.KIND_LEAGUE_INVITE, league=new)
        self.assertEqual(set(invites.values_list('user__username', flat=True)), {'admin', 'member'})
        # Lega privata: il link porta già il codice invito.
        self.assertIn(f'?code={new.invite_code}', invites.first().url)

    def test_senza_spunta_nessun_invito(self):
        self.client.login(username='owner', password='x')
        self.post(invite_members='')
        self.assertFalse(Notification.objects.filter(kind=Notification.KIND_LEAGUE_INVITE).exists())

    def test_date_incoerenti_rifiutate(self):
        self.client.login(username='owner', password='x')
        resp = self.post(registration_closes='2027-02-01')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(League.objects.filter(name='Fantamorte 2027').exists())
        resp = self.post(name='fantamorte 2026')
        self.assertContains(resp, 'Esiste già una lega con questo nome')

    def test_permessi(self):
        self.client.login(username='member', password='x')
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.client.login(username='outsider', password='x')
        self.assertEqual(self.post().status_code, 403)
        self.assertFalse(League.objects.filter(name='Fantamorte 2027').exists())

    def test_tab_nel_pannello_admin(self):
        self.client.login(username='owner', password='x')
        resp = self.client.get(reverse('league_admin', args=[self.source.slug]))
        self.assertContains(resp, self.url)
