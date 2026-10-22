"""Palmarès dei manager nelle leghe concluse (#107)."""
from datetime import date, timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Death, League, LeagueMembership, Team, TeamMember, WikipediaPerson
from .views import palmares

User = get_user_model()


def row(position, score, finished=True):
    return {'league': SimpleNamespace(is_finished=lambda: finished),
            'position': position, 'score': score}


class PalmaresUnitTest(SimpleTestCase):

    def test_conta_solo_le_leghe_concluse(self):
        self.assertIsNone(palmares([row(1, 100, finished=False)]))
        p = palmares([row(1, 100), row(3, 40), row(5, 10), row(1, 0), row(1, 90, finished=False)])
        self.assertEqual(p, {'played': 4, 'wins': 1, 'podiums': 2, 'best': 1, 'points': 150})


class PalmaresViewTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('campione', password='x')
        rival = User.objects.create_user('rivale', password='x')
        today = timezone.localdate()
        league = League.objects.create(
            name='Lega 2025', slug='lega-2025', owner=self.user,
            start_date=today - timedelta(days=400), end_date=today - timedelta(days=35),
            registration_opens=today - timedelta(days=420),
            registration_closes=today - timedelta(days=400))
        for u in (self.user, rival):
            LeagueMembership.objects.create(league=league, user=u)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q1', name_it='Morto', is_dead=True, death_date=today - timedelta(days=100))
        mine = Team.objects.create(name='Mia', manager=self.user, league=league)
        TeamMember.objects.create(team=mine, person=person)
        Team.objects.create(name='Sua', manager=rival, league=league)
        Death.objects.create(person=person, death_date=person.death_date,
                             is_confirmed=True, confirmed_at=timezone.now())
        self.client.login(username='campione', password='x')

    def test_profilo(self):
        resp = self.client.get(reverse('profile'))
        self.assertEqual(resp.context['palmares']['wins'], 1)
        self.assertContains(resp, 'vittoria')
        self.assertContains(resp, 'lega conclusa')

    def test_statistiche(self):
        resp = self.client.get(reverse('stats'))
        self.assertEqual(resp.context['palmares']['podiums'], 1)
        self.assertContains(resp, 'miglior piazzamento')
