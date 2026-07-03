"""Test di `LoginRequiredEverywhereMiddleware`: tutto è privato tranne le
rotte esplicitamente pubbliche (login/signup, static/media, PWA)."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase

User = get_user_model()


class LoginRequiredEverywhereMiddlewareTest(TestCase):

    def setUp(self):
        self.client = Client()

    def test_home_anonima_reindirizza_al_login(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/'))

    def test_lista_leghe_anonima_reindirizza_al_login(self):
        response = self.client.get('/leghe/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/accounts/login/'))

    def test_pagina_login_e_pubblica(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)

    def test_manifest_e_pubblico(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)

    def test_service_worker_e_pubblico(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)

    def test_pagina_offline_e_pubblica(self):
        response = self.client.get('/offline/')
        self.assertEqual(response.status_code, 200)

    def test_home_autenticata_restituisce_200(self):
        User.objects.create_user('utente', password='x')
        self.client.login(username='utente', password='x')
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
