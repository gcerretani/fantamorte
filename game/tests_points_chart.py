"""Grafico dell'andamento punti (#105)."""
from datetime import date

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from ._views_test_cases import ViewsBaseTestCase
from .charts import STEP_H, STEP_W, step_chart
from .models import Death, Team, TeamMember, WikipediaPerson

START, END = date(2026, 1, 1), date(2026, 12, 31)


class StepChartTest(SimpleTestCase):

    def test_niente_da_disegnare(self):
        self.assertIsNone(step_chart([{'label': 'A', 'events': []}], START, END, date(2026, 6, 1)))
        # Lega non ancora iniziata.
        self.assertIsNone(step_chart(
            [{'label': 'A', 'events': [(START, 10)]}], START, END, date(2025, 12, 1)))

    def test_gradini_e_scala(self):
        chart = step_chart([
            {'label': 'A', 'events': [(date(2026, 3, 1), 50), (date(2026, 2, 1), 50)], 'highlight': True},
            {'label': 'B', 'events': [(date(2026, 4, 1), 50)], 'base': -10},
        ], START, END, END)
        self.assertEqual(chart['top'], 100)
        a, b = chart['lines']
        self.assertEqual((a['total'], b['total']), (100, 40))
        self.assertTrue(a['highlight'])
        points = [tuple(map(float, p.split(','))) for p in a['points'].split()]
        # Parte da zero a sinistra, finisce al massimo a destra, e ogni
        # decesso è un gradino verticale (stessa x, y diversa).
        self.assertEqual(points[0], (0.0, float(STEP_H)))
        self.assertEqual(points[-1][0], float(STEP_W))
        self.assertAlmostEqual(points[-1][1], 8.0)
        self.assertEqual(points[1][0], points[2][0])
        self.assertGreater(points[1][1], points[2][1])

    def test_lega_in_corso_si_ferma_a_oggi(self):
        chart = step_chart([{'label': 'A', 'events': [(date(2026, 3, 1), 5)]}],
                           START, END, date(2026, 6, 30))
        self.assertEqual(chart['stop'], date(2026, 6, 30))
        self.assertEqual([t['label'] for t in chart['ticks']],
                         ['gen', 'feb', 'mar', 'apr', 'mag', 'giu'])

    def test_numeri_formattati_col_punto(self):
        chart = step_chart([{'label': 'A', 'events': [(date(2026, 3, 1), 5)]}], START, END, END)
        self.assertNotIn(',', chart['ticks'][1]['pct'])


class PointsChartViewTest(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        person = self.person
        person.is_dead = True
        person.death_date = date(2023, 6, 12)
        person.save()
        Death.objects.create(person=person, death_date=person.death_date,
                             is_confirmed=True, confirmed_at=timezone.now())
        self.client.login(username='member', password='x')

    def test_dettaglio_squadra(self):
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertIsNotNone(resp.context['points_chart'])
        self.assertContains(resp, 'class="fm-stepchart"')

    def test_classifica_prime_squadre_piu_la_mia(self):
        for i in range(6):
            manager = self.owner if i == 0 else self.outsider.__class__.objects.create_user(f'u{i}')
            team = Team.objects.create(name=f'Top {i}', manager=manager, league=self.private_league)
            TeamMember.objects.create(team=team, person=self.person)
        # A pari decessi, un punto in meno: la mia squadra è ultima.
        self.private_team.score_adjustment = -1
        self.private_team.save()
        resp = self.client.get(reverse('league_rankings', args=['lega-privata']))
        lines = resp.context['points_chart']['lines']
        self.assertEqual(len(lines), 6)  # 5 in testa + la mia
        self.assertEqual(sum(1 for l in lines if l['highlight']), 1)
        self.assertTrue(next(l for l in lines if l['label'] == 'Squadra Privata')['highlight'])

    def test_senza_decessi_niente_grafico(self):
        Death.objects.all().delete()
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertIsNone(resp.context['points_chart'])
        self.assertNotContains(resp, 'fm-stepchart')
