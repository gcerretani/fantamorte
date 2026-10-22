"""Importazione selettiva della rosa da un'altra squadra (#100)."""
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import League, LeagueMembership, Team, TeamMember, WikipediaPerson
from .team_import import plan_import

User = get_user_model()
SITELINKS = 'wikidata_api.client.WikidataClient.qids_with_sitelinks'


class TeamImportBase(TestCase):

    def setUp(self):
        cache.clear()
        today = date.today()
        self.user = User.objects.create_user('manager', password='x')
        self.other = User.objects.create_user('altro', password='x')
        self.old_league = League.objects.create(
            name='Lega 2026', slug='lega-2026', owner=self.user,
            start_date=today - timedelta(days=300), end_date=today - timedelta(days=1),
            registration_opens=today - timedelta(days=400),
            registration_closes=today - timedelta(days=300),
        )
        self.new_league = League.objects.create(
            name='Lega 2027', slug='lega-2027', owner=self.user,
            start_date=today + timedelta(days=60), end_date=today + timedelta(days=425),
            registration_opens=today - timedelta(days=1),
            registration_closes=today + timedelta(days=60),
        )
        for lg in (self.old_league, self.new_league):
            LeagueMembership.objects.create(league=lg, user=self.user, role='owner')
        self.source = Team.objects.create(
            name='Vecchia', manager=self.user, league=self.old_league, jolly_month=4)
        self.target = Team.objects.create(name='Nuova', manager=self.user, league=self.new_league)

    def person(self, qid, name, birth=1940, dead=False, itwiki=True):
        return WikipediaPerson.objects.create(
            wikidata_id=qid, name_it=name, birth_date=date(birth, 1, 1), is_dead=dead,
            wikipedia_url_it=f'https://it.wikipedia.org/wiki/{qid}' if itwiki else '',
        )

    def add(self, team, person, captain=False):
        return TeamMember.objects.create(team=team, person=person, is_captain=captain)

    def plan(self):
        return {r.person.name_it: r for r in plan_import(self.source, self.target)}


class PlanImportTest(TeamImportBase):

    def test_morti_e_gia_in_rosa_scartati(self):
        alive = self.person('Q1', 'Vivo')
        self.add(self.source, alive)
        self.add(self.source, self.person('Q2', 'Morto', dead=True))
        shared = self.person('Q3', 'Condiviso')
        self.add(self.source, shared)
        self.add(self.target, shared)
        rows = self.plan()
        self.assertTrue(rows['Vivo'].importable)
        self.assertEqual(rows['Morto'].reason, 'Deceduto/a')
        self.assertEqual(rows['Condiviso'].reason, 'Già in rosa')

    def test_sostituiti_non_importati(self):
        old = self.add(self.source, self.person('Q4', 'Sostituito', dead=True))
        new = self.add(self.source, self.person('Q5', 'Subentrato'))
        old.replaced_by = new
        old.save()
        self.assertEqual(list(self.plan()), ['Subentrato'])

    def test_lega_senza_filtro_wiki_non_interroga_wikidata(self):
        self.add(self.source, self.person('Q6', 'Senza pagina', itwiki=False))
        with patch(SITELINKS) as mocked:
            rows = self.plan()
        mocked.assert_not_called()
        self.assertTrue(rows['Senza pagina'].importable)

    def test_filtro_wiki_selettivo(self):
        self.new_league.search_wikipedia_langs = 'itwiki'
        self.new_league.save()
        self.add(self.source, self.person('Q7', 'Su itwiki'))
        self.add(self.source, self.person('Q8', 'Solo enwiki', itwiki=False))
        self.add(self.source, self.person('Q9', 'Itwiki non in cache', itwiki=False))
        with patch(SITELINKS, return_value={'Q9'}) as mocked:
            rows = self.plan()
        # Chi ha già wikipedia_url_it non costa una query.
        self.assertEqual(sorted(mocked.call_args.args[0]), ['Q8', 'Q9'])
        self.assertEqual(mocked.call_args.args[1], ['itwiki'])
        self.assertTrue(rows['Su itwiki'].importable)
        self.assertTrue(rows['Itwiki non in cache'].importable)
        self.assertFalse(rows['Solo enwiki'].importable)
        self.assertIn('Nessuna pagina Wikipedia', rows['Solo enwiki'].reason)

    def test_lega_con_piu_wiki_ammesse(self):
        self.new_league.search_wikipedia_langs = 'itwiki,enwiki'
        self.new_league.save()
        self.add(self.source, self.person('Q10', 'Solo enwiki', itwiki=False))
        with patch(SITELINKS, return_value={'Q10'}) as mocked:
            rows = self.plan()
        self.assertEqual(mocked.call_args.args[1], ['itwiki', 'enwiki'])
        self.assertTrue(rows['Solo enwiki'].importable)

    def test_verifica_wiki_cachata(self):
        self.new_league.search_wikipedia_langs = 'enwiki'
        self.new_league.save()
        self.add(self.source, self.person('Q11', 'Tizio'))
        with patch(SITELINKS, return_value=set()) as mocked:
            self.plan()
            self.plan()
        self.assertEqual(mocked.call_count, 1)

    def test_limiti_rosa(self):
        self.new_league.max_captains = 1
        self.new_league.max_non_captains = 2
        self.new_league.save()
        self.add(self.target, self.person('Q20', 'Capitano esistente'), captain=True)
        self.add(self.source, self.person('Q21', 'Ex capitano'), captain=True)
        self.add(self.source, self.person('Q22', 'Alfa'))
        self.add(self.source, self.person('Q23', 'Beta'))
        rows = self.plan()
        # Il capitano d'origine entra senza fascia (già al completo)…
        self.assertTrue(rows['Ex capitano'].importable)
        self.assertFalse(rows['Ex capitano'].as_captain)
        self.assertIn('senza fascia', rows['Ex capitano'].note)
        # …e occupa un posto da non capitano: per Beta non ce n'è più.
        self.assertTrue(rows['Alfa'].importable)
        self.assertEqual(rows['Beta'].reason, 'Rosa al completo')

    def test_capitano_portato_se_c_e_posto(self):
        self.add(self.source, self.person('Q24', 'Capo'), captain=True)
        self.assertTrue(self.plan()['Capo'].as_captain)

    def test_limite_eta(self):
        this_year = date.today().year
        self.new_league.max_total_age = 150
        self.new_league.save()
        self.add(self.source, self.person('Q30', 'Anziano', birth=this_year - 91))
        self.add(self.source, self.person('Q31', 'Giovane', birth=this_year - 41))
        self.add(self.source, self.person('Q32', 'Medio', birth=this_year - 61))
        rows = self.plan()
        # Ordine alfabetico: Anziano (90) + Giovane (40) = 130; Medio (60) sfora.
        self.assertTrue(rows['Anziano'].importable)
        self.assertTrue(rows['Giovane'].importable)
        self.assertIn("limite d'età", rows['Medio'].reason)

    def test_limite_eta_richiede_data_di_nascita(self):
        self.new_league.max_total_age = 500
        self.new_league.save()
        p = self.person('Q33', 'Senza nascita')
        p.birth_date = None
        p.save()
        self.add(self.source, p)
        self.assertIn('data di nascita', self.plan()['Senza nascita'].reason)


class TeamImportViewTest(TeamImportBase):

    def setUp(self):
        super().setUp()
        self.client.login(username='manager', password='x')
        self.add(self.source, self.person('Q40', 'Primo'), captain=True)
        self.add(self.source, self.person('Q41', 'Secondo'))
        self.add(self.source, self.person('Q42', 'Morto', dead=True))
        self.url = reverse('team_import', args=[self.target.pk])

    def test_anteprima(self):
        resp = self.client.get(self.url, {'da': self.source.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['importable_count'], 2)
        self.assertEqual(resp.context['skipped_count'], 1)
        self.assertContains(resp, 'Importa 2 personaggi')
        self.assertContains(resp, 'Deceduto/a')
        # L'anteprima non scrive niente.
        self.assertFalse(self.target.members.exists())

    def test_importa_e_idempotente(self):
        resp = self.client.post(self.url, {'da': self.source.pk, 'copy_jolly': '1'})
        self.assertRedirects(resp, reverse('team_edit', args=[self.target.pk]))
        names = set(self.target.members.values_list('person__name_it', flat=True))
        self.assertEqual(names, {'Primo', 'Secondo'})
        self.assertTrue(self.target.members.get(person__name_it='Primo').is_captain)
        self.target.refresh_from_db()
        self.assertEqual(self.target.jolly_month, 4)
        # Un secondo import non duplica nessuno.
        self.client.post(self.url, {'da': self.source.pk})
        self.assertEqual(self.target.members.count(), 2)

    def test_jolly_non_copiato_senza_spunta(self):
        self.client.post(self.url, {'da': self.source.pk})
        self.target.refresh_from_db()
        self.assertIsNone(self.target.jolly_month)

    def test_squadra_di_un_altro_utente_404(self):
        foreign = Team.objects.create(name='Altrui', manager=self.other, league=self.old_league)
        self.assertEqual(self.client.get(self.url, {'da': foreign.pk}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'da': foreign.pk}).status_code, 404)

    def test_solo_il_manager_a_iscrizioni_aperte(self):
        self.client.login(username='altro', password='x')
        resp = self.client.post(self.url, {'da': self.source.pk})
        self.assertRedirects(resp, reverse('team_edit', args=[self.target.pk]),
                             fetch_redirect_response=False)
        self.assertFalse(self.target.members.exists())

        self.client.login(username='manager', password='x')
        self.new_league.registration_closes = date.today() - timedelta(days=1)
        self.new_league.save()
        self.client.post(self.url, {'da': self.source.pk})
        self.assertFalse(self.target.members.exists())

    def test_wikidata_giu_nessun_import(self):
        self.new_league.search_wikipedia_langs = 'enwiki'
        self.new_league.save()
        with patch(SITELINKS, side_effect=RuntimeError('timeout')):
            resp = self.client.get(self.url, {'da': self.source.pk})
            self.assertTrue(resp.context['wiki_error'])
            self.client.post(self.url, {'da': self.source.pk})
        self.assertFalse(self.target.members.exists())

    def test_card_in_team_edit(self):
        resp = self.client.get(reverse('team_edit', args=[self.target.pk]))
        self.assertContains(resp, reverse('team_import', args=[self.target.pk]))
        # Chi non ha altre squadre non vede la card.
        self.source.delete()
        resp = self.client.get(reverse('team_edit', args=[self.target.pk]))
        self.assertNotContains(resp, reverse('team_import', args=[self.target.pk]))
