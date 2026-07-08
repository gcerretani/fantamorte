"""Test degli adapter allauth: ACCOUNT_SIGNUP_ENABLED e
SOCIALACCOUNT_SIGNUP_ENABLED sono due interruttori indipendenti, e nessuno
dei due deve toccare il login di chi ha gia' un account."""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from game.adapters import ClosedSignupAccountAdapter, ClosedSignupSocialAccountAdapter

User = get_user_model()


class ClosedSignupAccountAdapterTest(TestCase):

    @override_settings(ACCOUNT_SIGNUP_ENABLED=True)
    def test_signup_form_aperto(self):
        self.assertTrue(ClosedSignupAccountAdapter().is_open_for_signup(None))

    @override_settings(ACCOUNT_SIGNUP_ENABLED=False)
    def test_signup_form_chiuso(self):
        self.assertFalse(ClosedSignupAccountAdapter().is_open_for_signup(None))

    @override_settings(ACCOUNT_SIGNUP_ENABLED=False, SOCIALACCOUNT_SIGNUP_ENABLED=True)
    def test_signup_form_indipendente_da_oauth(self):
        """Chiudere il form non deve chiudere anche l'OAuth."""
        self.assertFalse(ClosedSignupAccountAdapter().is_open_for_signup(None))
        self.assertTrue(ClosedSignupSocialAccountAdapter().is_open_for_signup(None, None))


class ClosedSignupSocialAccountAdapterTest(TestCase):

    @override_settings(SOCIALACCOUNT_SIGNUP_ENABLED=True)
    def test_signup_oauth_aperto(self):
        self.assertTrue(ClosedSignupSocialAccountAdapter().is_open_for_signup(None, None))

    @override_settings(SOCIALACCOUNT_SIGNUP_ENABLED=False)
    def test_signup_oauth_chiuso(self):
        self.assertFalse(ClosedSignupSocialAccountAdapter().is_open_for_signup(None, None))

    @override_settings(SOCIALACCOUNT_SIGNUP_ENABLED=False, ACCOUNT_SIGNUP_ENABLED=True)
    def test_signup_oauth_indipendente_dal_form(self):
        """Chiudere l'OAuth non deve chiudere anche il form."""
        self.assertFalse(ClosedSignupSocialAccountAdapter().is_open_for_signup(None, None))
        self.assertTrue(ClosedSignupAccountAdapter().is_open_for_signup(None))


class LoginNonToccatoDaiFlagDiSignupTest(TestCase):
    """Il login di un account gia' esistente deve restare sempre disponibile,
    a prescindere dai due flag di signup."""

    def setUp(self):
        self.client = Client()
        User.objects.create_user('utente', password='x')

    @override_settings(ACCOUNT_SIGNUP_ENABLED=False, SOCIALACCOUNT_SIGNUP_ENABLED=False)
    def test_login_form_funziona_con_signup_chiusi(self):
        self.assertTrue(self.client.login(username='utente', password='x'))

    @override_settings(ACCOUNT_SIGNUP_ENABLED=False, SOCIALACCOUNT_SIGNUP_ENABLED=False)
    def test_pagina_login_resta_raggiungibile(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
