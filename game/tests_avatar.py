"""Avatar persona: thumbnail Commons, fallback iniziali, uso nelle pagine (#98)."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from ._views_test_cases import ViewsBaseTestCase
from .templatetags.fantamorte_tags import commons_thumb, person_initials

FILEPATH = 'https://commons.wikimedia.org/wiki/Special:FilePath/Silvio%20Berlusconi.jpg'


class CommonsThumbTest(SimpleTestCase):

    def test_sostituisce_la_larghezza_salvata(self):
        self.assertEqual(
            commons_thumb(FILEPATH + '?width=400', 120),
            FILEPATH + '?width=120',
        )

    def test_aggiunge_la_larghezza_se_manca(self):
        self.assertEqual(commons_thumb(FILEPATH, 250), FILEPATH + '?width=250')

    def test_url_non_commons_invariato(self):
        url = 'https://example.org/foto.jpg?width=400'
        self.assertEqual(commons_thumb(url, 120), url)

    def test_vuoto(self):
        self.assertEqual(commons_thumb('', 120), '')
        self.assertEqual(commons_thumb(None, 120), '')


class PersonInitialsTest(SimpleTestCase):

    def test_nome_e_cognome(self):
        self.assertEqual(person_initials('Silvio Berlusconi'), 'SB')

    def test_prende_primo_e_ultimo(self):
        self.assertEqual(person_initials('Karol Józef Wojtyła'), 'KW')

    def test_ignora_parole_non_alfabetiche(self):
        self.assertEqual(person_initials('Elisabetta II'), 'EI')
        self.assertEqual(person_initials('Pio (papa) 12'), 'PP')

    def test_nome_singolo_e_vuoto(self):
        self.assertEqual(person_initials('Madonna'), 'M')
        self.assertEqual(person_initials(''), '?')


@override_settings(PERSON_IMAGE_PROXY=False)
@patch('wikidata_api.client.WikidataClient.get_image_credits', return_value={})
class AvatarNellePagineTest(ViewsBaseTestCase):
    """Hotlink diretto (proxy spento): la riscrittura della larghezza è
    visibile nell'URL. Il proxy ha i suoi test in tests_person_images."""

    def setUp(self):
        super().setUp()
        self.person.image_url = FILEPATH + '?width=400'
        self.person.save()
        self.client.login(username='member', password='x')

    def test_rosa_usa_la_thumbnail_piccola(self, _credits):
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertContains(resp, 'class="fm-avatar fm-avatar-md')
        self.assertContains(resp, FILEPATH + '?width=120')
        self.assertNotContains(resp, FILEPATH + '?width=400')

    def test_senza_foto_restano_le_iniziali(self, _credits):
        self.person.image_url = ''
        self.person.save()
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertContains(resp, '<span class="fm-avatar-initials">SB</span>', html=True)
        self.assertNotContains(resp, 'Special:FilePath')

    def test_morto_in_bianco_e_nero(self, _credits):
        self.person.is_dead = True
        self.person.save()
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertContains(resp, 'fm-avatar-dead')

    def test_pagina_persona_ritratto_xl(self, _credits):
        resp = self.client.get(reverse('person_detail', args=[self.person.pk]))
        self.assertContains(resp, 'fm-avatar-xl')
        self.assertContains(resp, FILEPATH + '?width=500')

    def test_api_modal_include_avatar_renderizzato(self, _credits):
        resp = self.client.get(reverse('person_info', args=[self.person.pk]))
        data = resp.json()
        self.assertIn('fm-avatar-xl', data['avatar_html'])
        self.assertIn(FILEPATH + '?width=500', data['avatar_html'])
