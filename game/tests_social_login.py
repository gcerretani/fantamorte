"""Login social senza la pagina di conferma intermedia senza stile."""
from django.test import TestCase, override_settings
from django.urls import reverse

GOOGLE = {'google': {'APP': {'client_id': 'cid', 'secret': 's', 'key': ''}}}


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE)
class BottoniSocialTest(TestCase):

    def test_login_e_signup_con_form_post(self):
        for name in ('account_login', 'account_signup'):
            resp = self.client.get(reverse(name))
            self.assertContains(resp, '<form method="post" action="/accounts/google/login/?process=login"')
            self.assertNotContains(resp, 'href="/accounts/google/login/')

    def test_post_va_dritto_al_provider(self):
        resp = self.client.post('/accounts/google/login/?process=login')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('accounts.google.com', resp['Location'])

    def test_pagina_intermedia_nel_layout_del_sito(self):
        resp = self.client.get('/accounts/google/login/?process=login')
        self.assertContains(resp, 'fm-topbar')
