"""Foto delle persone: attribuzione Commons e cache locale (#103)."""
import shutil
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from . import person_images
from ._views_test_cases import ViewsBaseTestCase
from .models import WikipediaPerson
from .person_images import commons_filename, credit_for_display, credit_is_stale, image_hash

FILEPATH = 'https://commons.wikimedia.org/wiki/Special:FilePath/Silvio%20Berlusconi%202010.jpg?width=400'
FILENAME = 'Silvio Berlusconi 2010.jpg'
CREDITS = 'wikidata_api.client.WikidataClient.get_image_credits'


class CommonsFilenameTest(SimpleTestCase):

    def test_estrae_e_decodifica(self):
        self.assertEqual(commons_filename(FILEPATH), FILENAME)

    def test_url_non_commons(self):
        self.assertEqual(commons_filename('https://example.org/wiki/Special:FilePath/x.jpg'), '')
        self.assertEqual(commons_filename('https://commons.wikimedia.org/wiki/File:x.jpg'), '')
        self.assertEqual(commons_filename(''), '')


class _Base(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        override = override_settings(PERSON_IMAGE_CACHE_DIR=self.tmp, PERSON_IMAGE_PROXY=True)
        override.enable()
        self.addCleanup(override.disable)
        self.person.image_url = FILEPATH
        self.person.save()
        self.client.login(username='member', password='x')

    def url(self, width=120, digest=None):
        return reverse('person_image', args=[
            self.person.pk, width, digest or image_hash(self.person.image_url)])


@patch(CREDITS, return_value={})
class ProxyViewTest(_Base):

    def test_avatar_punta_al_proxy(self, _credits):
        resp = self.client.get(reverse('team_detail', args=[self.private_team.pk]))
        self.assertContains(resp, self.url(120))
        self.assertNotContains(resp, 'Special:FilePath')

    def test_primo_accesso_scarica_poi_serve_da_disco(self, _credits):
        with patch.object(person_images, '_download', return_value=(b'JPEGDATA', 'image/jpeg')) as dl:
            first = self.client.get(self.url())
            second = self.client.get(self.url())
        self.assertEqual(dl.call_count, 1)
        for resp in (first, second):
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], 'image/jpeg')
            self.assertEqual(b''.join(resp.streaming_content), b'JPEGDATA')
            self.assertIn('immutable', resp['Cache-Control'])

    def test_hash_vecchio_reindirizza_alla_foto_attuale(self, _credits):
        resp = self.client.get(self.url(digest='0123456789ab'))
        self.assertRedirects(resp, self.url(), fetch_redirect_response=False)

    def test_larghezza_fuori_whitelist(self, _credits):
        self.assertEqual(self.client.get(self.url(width=999)).status_code, 404)

    def test_download_fallito_404_e_non_ritenta_subito(self, _credits):
        with patch.object(person_images, '_download', side_effect=ValueError('giù')) as dl:
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp['Cache-Control'], 'no-store')
        self.assertEqual(dl.call_count, 1)

    def test_foto_non_commons_non_proxata(self, _credits):
        self.person.image_url = 'https://example.org/foto.jpg'
        self.person.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_richiede_login(self, _credits):
        self.client.logout()
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


class DownloadSicurezzaTest(SimpleTestCase):

    def _response(self, content_type='image/jpeg', url='https://upload.wikimedia.org/x.jpg', body=b'x'):
        resp = MagicMock()
        resp.url = url
        resp.headers = {'Content-Type': content_type}
        resp.iter_content.return_value = [body]
        return resp

    def _download(self, resp):
        session = MagicMock()
        session.get.return_value = resp
        with patch('wikidata_api.client._shared_session', return_value=session):
            return person_images._download(FILEPATH, 120)

    def test_ok(self):
        self.assertEqual(self._download(self._response()), (b'x', 'image/jpeg'))

    def test_svg_rifiutato(self):
        with self.assertRaises(ValueError):
            self._download(self._response(content_type='image/svg+xml'))

    def test_redirect_fuori_da_wikimedia_rifiutato(self):
        with self.assertRaises(ValueError):
            self._download(self._response(url='https://evil.example/x.jpg'))

    def test_troppo_grande(self):
        big = b'x' * (person_images.MAX_BYTES + 1)
        with self.assertRaises(ValueError):
            self._download(self._response(body=big))


class CreditiTest(_Base):

    CREDIT = {FILENAME: {
        'author': 'Mario Bianchi', 'license': 'CC BY-SA 4.0',
        'license_url': 'https://creativecommons.org/licenses/by-sa/4.0',
        'page_url': 'https://commons.wikimedia.org/wiki/File:Silvio_Berlusconi_2010.jpg',
    }}

    def test_pagina_persona_recupera_e_mostra_il_credito(self):
        with patch(CREDITS, return_value=self.CREDIT) as mocked:
            resp = self.client.get(reverse('person_detail', args=[self.person.pk]))
            # Il secondo accesso non richiede più nulla.
            self.client.get(reverse('person_detail', args=[self.person.pk]))
        self.assertEqual(mocked.call_count, 1)
        self.assertContains(resp, 'Foto: Mario Bianchi')
        self.assertContains(resp, 'CC BY-SA 4.0')
        self.person.refresh_from_db()
        self.assertEqual(self.person.image_credit['file'], FILENAME)
        self.assertFalse(credit_is_stale(self.person))

    def test_cambio_foto_rende_il_credito_vecchio(self):
        self.person.image_credit = {'file': FILENAME, 'author': 'X'}
        self.assertFalse(credit_is_stale(self.person))
        self.person.image_url = 'https://commons.wikimedia.org/wiki/Special:FilePath/Altra.jpg'
        self.assertTrue(credit_is_stale(self.person))
        # Il credito della foto vecchia non si mostra sotto quella nuova.
        self.assertNotIn('author', credit_for_display(self.person))

    def test_commons_giu_mostra_almeno_il_link(self):
        with patch(CREDITS, side_effect=RuntimeError('timeout')):
            resp = self.client.get(reverse('person_detail', args=[self.person.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'https://commons.wikimedia.org/wiki/File:Silvio_Berlusconi_2010.jpg')
        self.person.refresh_from_db()
        self.assertEqual(self.person.image_credit, {})

    def test_modal_include_il_credito(self):
        self.person.image_credit = {'file': FILENAME, **self.CREDIT[FILENAME]}
        self.person.save()
        data = self.client.get(reverse('person_info', args=[self.person.pk])).json()
        self.assertIn('Mario Bianchi', data['avatar_html'])

    def test_command_a_blocchi_solo_sui_vecchi(self):
        fresh = WikipediaPerson.objects.create(
            wikidata_id='Q99', name_it='Già fatto',
            image_url='https://commons.wikimedia.org/wiki/Special:FilePath/A.jpg',
            image_credit={'file': 'A.jpg'})
        with patch(CREDITS, return_value=self.CREDIT) as mocked:
            out = StringIO()
            call_command('refresh_image_credits', stdout=out)
        self.assertEqual(mocked.call_args.args[0], [FILENAME])
        self.assertIn('Crediti aggiornati: 1', out.getvalue())
        fresh.refresh_from_db()
        self.assertEqual(fresh.image_credit, {'file': 'A.jpg'})
