"""Restyling 2.0: hero della home, podio, niente emoji nei template (#102)."""
import re
from datetime import date
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from ._views_test_cases import ViewsBaseTestCase
from .models import Death, Team, TeamMember, WikipediaPerson


class HomeHeroTest(ViewsBaseTestCase):

    def test_senza_decessi_niente_hero(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('home'))
        self.assertIsNone(resp.context['hero_death'])
        self.assertNotContains(resp, 'fm-hero')

    def test_ultimo_decesso_con_i_punti_della_mia_squadra(self):
        person = self.person
        person.is_dead = True
        person.death_date = date(2023, 6, 12)
        person.save()
        death = Death.objects.create(person=person, death_date=person.death_date,
                                     is_confirmed=True, confirmed_at=timezone.now())
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.context['hero_death'], death)
        self.assertEqual(resp.context['hero_points'][0]['team'], self.private_team)
        self.assertGreater(resp.context['hero_points'][0]['points'], 0)
        self.assertContains(resp, 'Ultimo decesso nelle tue leghe')

    def test_posizione_in_classifica_nella_lista_leghe(self):
        self.client.login(username='member', password='x')
        resp = self.client.get(reverse('home'))
        entry = next(e for e in resp.context['active_leagues'] if e['team'])
        self.assertEqual((entry['position'], entry['teams_count']), (1, 1))
        self.assertContains(resp, '1° su 1')


class PodioTest(ViewsBaseTestCase):

    def test_podio_solo_con_almeno_due_squadre(self):
        self.client.login(username='member', password='x')
        url = reverse('league_rankings', args=['lega-privata'])
        self.assertNotContains(self.client.get(url), 'fm-podium')
        other = Team.objects.create(name='Seconda', manager=self.owner, league=self.private_league)
        TeamMember.objects.create(
            team=other, person=WikipediaPerson.objects.create(wikidata_id='Q2', name_it='Altro'))
        resp = self.client.get(url)
        self.assertContains(resp, 'fm-podium fm-podium-duo')
        self.assertContains(resp, 'fm-podium-step fm-podium-1')


class NienteEmojiNeiTemplateTest(SimpleTestCase):
    """Le emoji si vedono diverse su ogni device: nei template di gioco si
    usano le icone dello sprite SVG (base.html)."""

    EMOJI = re.compile(r'&#1[0-9]{5};|[\U0001F300-\U0001FAFF]')

    def test_nessuna_emoji(self):
        root = Path(settings.BASE_DIR) / 'templates' / 'game'
        offenders = [
            str(p.relative_to(root)) for p in root.glob('*.html')
            if self.EMOJI.search(p.read_text(encoding='utf-8'))
        ]
        self.assertEqual(offenders, [])
