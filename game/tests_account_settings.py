"""Impostazioni account dal profilo e pagine allauth stilate (#121)."""
import hashlib
import json

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import League, LeagueMembership, Notification, PushSubscription, Team, UserProfile

User = get_user_model()


class _Base(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('mario', email='mario@example.com', password='pw-lunga-123')
        EmailAddress.objects.create(user=self.user, email='mario@example.com', verified=True, primary=True)
        self.client.login(username='mario', password='pw-lunga-123')


class ProfiloAccountTest(_Base):

    def test_card_account(self):
        resp = self.client.get(reverse('profile'))
        self.assertContains(resp, 'mario@example.com')
        self.assertContains(resp, 'verificata')
        for name in ('account_username', 'account_email', 'account_change_password', 'account_delete'):
            self.assertContains(resp, reverse(name))

    def test_email_senza_primaria(self):
        EmailAddress.objects.all().delete()
        resp = self.client.get(reverse('profile'))
        self.assertContains(resp, 'mario@example.com')
        self.assertContains(resp, 'da verificare')

    def test_senza_password_propone_di_impostarla(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('profile')), reverse('account_set_password'))


class PagineAllauthStilateTest(_Base):
    """Le pagine allauth passano dal nostro base.html e dagli elementi del design system."""

    def test_gestione_email(self):
        resp = self.client.get(reverse('account_email'))
        self.assertContains(resp, 'fm-account-page')
        self.assertContains(resp, 'class="btn btn-primary"')
        self.assertContains(resp, 'class="form-control')
        self.assertContains(resp, 'badge text-bg-success')
        self.assertContains(resp, 'fm-tabbar')  # navigazione dell'app, non l'HTML grezzo

    def test_cambio_password(self):
        resp = self.client.get(reverse('account_change_password'))
        self.assertContains(resp, 'fm-account-page')
        self.assertContains(resp, 'form-control')

    def test_recupero_password_da_anonimo(self):
        self.client.logout()
        resp = self.client.get(reverse('account_reset_password'))
        self.assertContains(resp, 'fm-account-entrance')
        self.assertContains(resp, 'btn btn-primary')


class NomeUtenteTest(_Base):

    def test_cambio(self):
        resp = self.client.post(reverse('account_username'), {'username': 'mario_rossi'})
        self.assertRedirects(resp, reverse('profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'mario_rossi')

    def test_gia_preso_o_uguale(self):
        User.objects.create_user('Luigi')
        resp = self.client.post(reverse('account_username'), {'username': 'luigi'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        resp = self.client.post(reverse('account_username'), {'username': 'mario'})
        self.assertContains(resp, 'È già il tuo nome utente')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'mario')

    def test_caratteri_non_ammessi(self):
        resp = self.client.post(reverse('account_username'), {'username': 'mario rossi!'})
        self.assertTrue(resp.context['form'].errors)


class EliminaAccountTest(_Base):

    def setUp(self):
        super().setUp()
        owner = User.objects.create_user('owner')
        today = timezone.localdate()
        self.league = League.objects.create(
            name='L', slug='l', owner=owner, start_date=today, end_date=today,
            registration_opens=today, registration_closes=today)
        LeagueMembership.objects.create(league=self.league, user=self.user)
        self.team = Team.objects.create(name='Squadra di Mario', manager=self.user, league=self.league)
        PushSubscription.objects.create(user=self.user, endpoint='https://push.example/x',
                                        p256dh='k', auth='a')
        Notification.objects.create(user=self.user, kind=Notification.KIND_LEAGUE_STARTED, title='t')

    def test_nome_sbagliato_non_elimina(self):
        self.client.post(reverse('account_delete'), {'confirm_username': 'altro'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_anonimizza_e_conserva_le_squadre(self):
        resp = self.client.post(reverse('account_delete'), {'confirm_username': 'mario'})
        self.assertRedirects(resp, reverse('account_login'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.username, f'utente-eliminato-{self.user.pk}')
        self.assertEqual(self.user.email, '')
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(EmailAddress.objects.filter(user=self.user).exists())
        self.assertFalse(PushSubscription.objects.filter(user=self.user).exists())
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        # Lo storico di gioco resta: la squadra è ancora in lega.
        self.assertTrue(Team.objects.filter(pk=self.team.pk, manager=self.user).exists())
        # Sessione chiusa e niente più accesso con le vecchie credenziali.
        self.assertEqual(self.client.get(reverse('profile')).status_code, 302)
        self.assertFalse(self.client.login(username='mario', password='pw-lunga-123'))

    def test_owner_di_leghe_bloccato(self):
        self.league.owner = self.user
        self.league.save()
        resp = self.client.get(reverse('account_delete'))
        self.assertContains(resp, 'trasferisci la proprietà')
        self.assertNotContains(resp, 'name="confirm_username"')
        self.client.post(reverse('account_delete'), {'confirm_username': 'mario'})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)


class SmokePagineAllauthTest(_Base):
    """Ogni pagina allauth raggiungibile si renderizza con i nostri elementi."""

    LOGGED = ['account_email', 'account_change_password', 'socialaccount_connections',
              'account_logout', 'account_reauthenticate']
    ANON = ['account_reset_password', 'account_reset_password_done',
            'account_email_verification_sent']

    def test_pagine(self):
        for name in self.LOGGED:
            with self.subTest(name=name):
                resp = self.client.get(reverse(name))
                self.assertIn(resp.status_code, (200, 302))
                if resp.status_code == 200:
                    self.assertContains(resp, 'fm-topbar')
        self.client.logout()
        for name in self.ANON:
            with self.subTest(name=name):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, 'fm-account-entrance')

    def test_link_di_reset_scaduto(self):
        self.client.logout()
        resp = self.client.get(reverse('account_reset_password_from_key',
                                       kwargs={'uidb36': 'x', 'key': 'chiave-finta'}), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'fm-account-entrance')


GOOGLE = {'google': {'APP': {'client_id': 'cid', 'secret': 's', 'key': ''}}}
OIDC = {'openid_connect': {'APPS': [{
    'provider_id': 'authentik', 'name': 'Authentik', 'client_id': 'cid', 'secret': 's',
    'settings': {'server_url': 'https://sso.example.org/application/o/fantamorte/'},
}]}}


@override_settings(SOCIALACCOUNT_PROVIDERS=GOOGLE)
class BottoniSocialTest(TestCase):
    """Il login social parte da un POST: niente pagina bianca intermedia."""

    def test_login_con_form_post(self):
        resp = self.client.get(reverse('account_login'))
        self.assertContains(resp, '<form method="post" action="/accounts/google/login/?process=login"')
        self.assertNotContains(resp, 'href="/accounts/google/login/')
        self.assertContains(resp, 'Continua con Google')

    def test_pagina_intermedia_comunque_stilata(self):
        resp = self.client.get('/accounts/google/login/?process=login')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'fm-account-entrance')
        self.assertContains(resp, 'btn btn-primary')

    def test_post_va_dritto_al_provider(self):
        resp = self.client.post('/accounts/google/login/?process=login')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('accounts.google.com', resp['Location'])


@override_settings(SOCIALACCOUNT_PROVIDERS=OIDC)
class OidcTest(TestCase):

    def test_bottone_provider_oidc(self):
        resp = self.client.get(reverse('account_login'))
        self.assertContains(resp, 'Continua con Authentik')
        self.assertContains(resp, 'action="/accounts/oidc/authentik/login/?process=login"')


class SoloSsoTest(TestCase):

    def setUp(self):
        user = User.objects.create_user('mario', email='mario@example.com', password='pw-lunga-123')
        EmailAddress.objects.create(user=user, email='mario@example.com', verified=True, primary=True)

    @override_settings(PASSWORD_LOGIN_ENABLED=False, SOCIALACCOUNT_PROVIDERS=OIDC)
    def test_senza_password(self):
        resp = self.client.get(reverse('account_login'))
        self.assertContains(resp, 'Continua con Authentik')
        self.assertNotContains(resp, 'name="password"')
        self.assertNotContains(resp, '— oppure —')
        self.client.post(reverse('account_login'), {'login': 'mario', 'password': 'pw-lunga-123'})
        self.assertNotIn('_auth_user_id', self.client.session)
        # Neanche la registrazione via form.
        self.assertNotContains(self.client.get(reverse('account_signup')), 'name="password1"')

    @override_settings(PASSWORD_LOGIN_ENABLED=False, SOCIALACCOUNT_PROVIDERS={})
    def test_senza_provider_la_password_resta(self):
        resp = self.client.post(reverse('account_login'), {'login': 'mario', 'password': 'pw-lunga-123'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)


class AvatarUtenteTest(_Base):

    def test_gravatar_di_default(self):
        from .user_avatars import avatar_url
        url = avatar_url(self.user, 64)
        digest = hashlib.sha256(b'mario@example.com').hexdigest()
        self.assertEqual(url, f'https://www.gravatar.com/avatar/{digest}?s=64&d=404')

    def test_sorgenti(self):
        from .user_avatars import avatar_url
        profile = self.user.profile
        profile.social_avatar_url = 'https://lh3.googleusercontent.com/a/foto'
        profile.save()
        self.assertEqual(avatar_url(self.user, 64), 'https://lh3.googleusercontent.com/a/foto')
        profile.avatar_source = UserProfile.AVATAR_GRAVATAR
        profile.save()
        self.assertIn('gravatar.com', avatar_url(self.user, 64))
        profile.avatar_source = UserProfile.AVATAR_NONE
        profile.save()
        self.assertEqual(avatar_url(self.user, 64), '')

    def test_foto_google_e_oidc_copiate_sul_profilo(self):
        from allauth.socialaccount.models import SocialAccount
        from .user_avatars import sync_social_avatar
        SocialAccount.objects.create(user=self.user, provider='google', uid='1',
                                     extra_data={'picture': 'https://lh3.googleusercontent.com/a/x'})
        sync_social_avatar(self.user)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.social_avatar_url, 'https://lh3.googleusercontent.com/a/x')
        SocialAccount.objects.all().delete()
        SocialAccount.objects.create(user=self.user, provider='authentik', uid='2',
                                     extra_data={'userinfo': {'picture': 'https://sso.example.org/p.png'}})
        sync_social_avatar(self.user)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.social_avatar_url, 'https://sso.example.org/p.png')

    def test_scelta_dal_profilo_con_anteprima(self):
        resp = self.client.post(reverse('profile_preferences'), json.dumps({'avatar_source': 'none'}),
                                content_type='application/json')
        self.assertEqual(resp.json()['status'], 'ok')
        self.assertIn('fm-avatar-initials', resp.json()['avatar_html'])
        self.assertNotIn('<img', resp.json()['avatar_html'])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.avatar_source, 'none')
        bad = self.client.post(reverse('profile_preferences'), json.dumps({'avatar_source': ['x']}),
                               content_type='application/json')
        self.assertEqual(bad.status_code, 400)

    def test_avatar_in_classifica_e_top_bar(self):
        today = timezone.localdate()
        league = League.objects.create(
            name='L', slug='l', owner=self.user, start_date=today, end_date=today,
            registration_opens=today, registration_closes=today)
        LeagueMembership.objects.create(league=league, user=self.user, role='owner')
        Team.objects.create(name='T', manager=self.user, league=league)
        resp = self.client.get(reverse('league_rankings', args=['l']))
        self.assertContains(resp, 'gravatar.com/avatar/', count=2)  # top bar + riga classifica
