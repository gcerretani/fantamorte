"""Test del feed notifiche in-app + matrice preferenze per canale.

Copre:
- creazione righe feed alla conferma decesso (una per destinatario, urgente/
  team per chi ha la persona in squadra, idempotenza, feed sempre creato);
- gating dei canali push/email via matrice preferenze (`wants`);
- reminder sostituzione, iscrizione lega, blocco squadra, lifecycle lega;
- endpoint feed (lista, unread-count, mark-read) e autosave preferenze.
"""
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import notifications as notif
from .models import (
    Death, League, LeagueMembership, Notification, PushSubscription, Team,
    TeamMember, UserProfile, WikipediaPerson, default_notification_prefs,
    describe_user_agent,
)

User = get_user_model()


class NotificationFeedBase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', email='owner@example.com', password='x')
        self.member = User.objects.create_user('member', email='member@example.com', password='x')
        self.outsider = User.objects.create_user('outsider', email='out@example.com', password='x')

        self.league = League.objects.create(
            name='Lega Feed', slug='lega-feed', owner=self.owner,
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2019, 12, 1), registration_closes=date(2020, 1, 31),
        )
        LeagueMembership.objects.create(league=self.league, user=self.owner, role='owner')
        LeagueMembership.objects.create(league=self.league, user=self.member, role='member')

        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q1', name_it='Tizio Caio',
            birth_date=date(1940, 1, 1), is_dead=False,
        )
        # owner ha la persona in squadra; member no.
        self.team_owner = Team.objects.create(name='Squadra Owner', manager=self.owner, league=self.league)
        TeamMember.objects.create(team=self.team_owner, person=self.person)
        self.team_member = Team.objects.create(name='Squadra Member', manager=self.member, league=self.league)

    def _confirm_death(self):
        self.person.is_dead = True
        self.person.save()
        return Death.objects.create(
            person=self.person, death_date=date(2025, 6, 1),
            death_age=85, is_confirmed=True,
        )


class DeathFeedTest(NotificationFeedBase):

    def test_una_notifica_per_membro_con_urgenza_per_chi_ha_in_squadra(self):
        death = self._confirm_death()  # il signal crea il feed
        owner_notifs = Notification.objects.filter(user=self.owner, death=death)
        member_notifs = Notification.objects.filter(user=self.member, death=death)
        self.assertEqual(owner_notifs.count(), 1)
        self.assertEqual(member_notifs.count(), 1)
        # owner: la persona è in squadra → urgente + kind death_team
        self.assertEqual(owner_notifs.first().kind, Notification.KIND_DEATH_TEAM)
        self.assertTrue(owner_notifs.first().is_urgent)
        # member: decesso normale
        self.assertEqual(member_notifs.first().kind, Notification.KIND_DEATH)
        self.assertFalse(member_notifs.first().is_urgent)
        # outsider (non iscritto) non riceve nulla
        self.assertEqual(Notification.objects.filter(user=self.outsider, death=death).count(), 0)

    def test_feed_creato_anche_se_canali_disattivati(self):
        # Nessun canale attivo: il feed in-app deve comunque comparire.
        for u in (self.owner, self.member):
            u.profile.notification_prefs = {'death': {'push': False, 'email': False}}
            u.profile.save()
        death = self._confirm_death()
        self.assertEqual(Notification.objects.filter(death=death).count(), 2)

    def test_idempotenza_no_doppioni(self):
        death = self._confirm_death()
        # Ri-eseguo esplicitamente la creazione: non deve duplicare.
        created = notif.create_death_notifications(death)
        self.assertEqual(created, 0)
        self.assertEqual(Notification.objects.filter(death=death).count(), 2)


class WantsMatrixTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u', password='x')

    def test_default_prefs(self):
        p = self.user.profile
        self.assertTrue(p.wants('death', 'push'))
        self.assertTrue(p.wants('death', 'email'))
        self.assertFalse(p.wants('league_joined', 'push'))
        self.assertFalse(p.wants('league_events', 'email'))

    def test_chiavi_mancanti_fallback_ai_default(self):
        # prefs parziali: 'death' presente senza 'email' → fallback default (True)
        self.user.profile.notification_prefs = {'death': {'push': False}}
        self.user.profile.save()
        p = UserProfile.objects.get(pk=self.user.profile.pk)
        self.assertFalse(p.wants('death', 'push'))
        self.assertTrue(p.wants('death', 'email'))  # mancante → default
        # categoria assente del tutto → default
        self.assertTrue(p.wants('substitution', 'push'))

    def test_wants_accetta_kind_oltre_categoria(self):
        # KIND_DEATH_TEAM appartiene alla categoria 'death'
        self.assertTrue(notif.wants(self.user, Notification.KIND_DEATH_TEAM, 'push'))


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Fantamorte <noreply@example.com>',
)
class ChannelGatingTest(NotificationFeedBase):

    def test_email_rispetta_matrice(self):
        from .email import broadcast_death_email
        # member disattiva email sui decessi
        self.member.profile.notification_prefs = {'death': {'push': True, 'email': False}}
        self.member.profile.save()
        death = self._confirm_death()
        mail.outbox.clear()
        broadcast_death_email(death)
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertIn('owner@example.com', recipients)
        self.assertNotIn('member@example.com', recipients)


class SubstitutionFeedTest(NotificationFeedBase):

    def test_reminder_crea_notifica(self):
        death = self._confirm_death()
        tm = TeamMember.objects.get(team=self.team_owner, person=self.person)
        notif.create_substitution_notification(tm, 3)
        n = Notification.objects.filter(user=self.owner, kind=Notification.KIND_SUBSTITUTION).first()
        self.assertIsNotNone(n)
        self.assertTrue(n.is_urgent)
        self.assertIn('sostituire', n.title)


class LeagueTeamEventTest(NotificationFeedBase):

    def test_iscrizione_notifica_owner_non_se_stesso(self):
        Notification.objects.all().delete()
        newbie = User.objects.create_user('newbie', password='x')
        LeagueMembership.objects.create(league=self.league, user=newbie, role='member')
        owner_notif = Notification.objects.filter(
            user=self.owner, kind=Notification.KIND_LEAGUE_JOINED,
        )
        self.assertEqual(owner_notif.count(), 1)
        # newbie (che si è iscritto) non riceve una notifica per la propria iscrizione
        self.assertFalse(
            Notification.objects.filter(user=newbie, kind=Notification.KIND_LEAGUE_JOINED).exists()
        )

    def test_owner_che_si_iscrive_non_si_autonotifica(self):
        Notification.objects.all().delete()
        # L'owner è già iscritto in setUp; simuliamo una lega nuova dove l'owner si iscrive.
        league2 = League.objects.create(
            name='Lega 2', slug='lega-2', owner=self.owner,
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2019, 12, 1), registration_closes=date(2020, 1, 31),
        )
        LeagueMembership.objects.create(league=league2, user=self.owner, role='owner')
        self.assertFalse(
            Notification.objects.filter(kind=Notification.KIND_LEAGUE_JOINED).exists()
        )

    def test_blocco_squadra_notifica_manager(self):
        Notification.objects.all().delete()
        self.team_owner.is_locked = True
        self.team_owner.save()
        n = Notification.objects.filter(user=self.owner, kind=Notification.KIND_TEAM_LOCKED)
        self.assertEqual(n.count(), 1)
        # Ri-salvare senza cambiare is_locked non ricrea la notifica
        self.team_owner.save()
        self.assertEqual(
            Notification.objects.filter(kind=Notification.KIND_TEAM_LOCKED).count(), 1
        )


class LeagueLifecycleCommandTest(NotificationFeedBase):

    def test_comando_idempotente(self):
        from django.core.management import call_command
        Notification.objects.all().delete()
        # La lega è iniziata (start 2020) e non conclusa (end 2030) → solo started.
        call_command('emit_league_lifecycle', '--league', 'lega-feed')
        started = Notification.objects.filter(kind=Notification.KIND_LEAGUE_STARTED)
        self.assertEqual(started.count(), 2)  # owner + member
        self.assertFalse(
            Notification.objects.filter(kind=Notification.KIND_LEAGUE_ENDED).exists()
        )
        # Seconda esecuzione: nessun doppione
        call_command('emit_league_lifecycle', '--league', 'lega-feed')
        self.assertEqual(
            Notification.objects.filter(kind=Notification.KIND_LEAGUE_STARTED).count(), 2
        )


class NotificationEndpointsTest(NotificationFeedBase):

    def setUp(self):
        super().setUp()
        # Azzero le notifiche generate dai signal in setUp (es. league_joined)
        # per isolare il conteggio di questo blocco.
        Notification.objects.all().delete()
        self.client.force_login(self.member)
        # tre notifiche non lette per member
        for i in range(3):
            Notification.objects.create(
                user=self.member, kind=Notification.KIND_DEATH,
                title=f'N{i}', url='/', is_read=False,
            )

    def test_unread_count(self):
        resp = self.client.get(reverse('notifications_unread_count'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['count'], 3)

    def test_lista_api(self):
        resp = self.client.get(reverse('notifications_api'))
        data = resp.json()
        self.assertEqual(data['count'], 3)
        self.assertEqual(len(data['results']), 3)

    def test_mark_read_tutte(self):
        resp = self.client.post(reverse('notifications_mark_read'),
                                data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(notif.unread_count(self.member), 0)

    def test_pagina_feed_segna_lette(self):
        resp = self.client.get(reverse('notifications'))
        self.assertEqual(resp.status_code, 200)
        # unread_before riflette lo stato pre-lettura
        self.assertEqual(resp.context['unread_before'], 3)
        # dopo il render sono tutte lette
        self.assertEqual(notif.unread_count(self.member), 0)

    def test_altro_utente_non_vede_le_mie(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse('notifications_unread_count'))
        self.assertEqual(resp.json()['count'], 0)


class ProfilePreferencesEndpointTest(NotificationFeedBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.member)
        self.url = reverse('profile_preferences')

    def _post(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json')

    def test_salva_matrice(self):
        resp = self._post({'prefs': {'death': {'push': False}}})
        self.assertEqual(resp.status_code, 200)
        p = UserProfile.objects.get(user=self.member)
        self.assertFalse(p.wants('death', 'push'))
        self.assertTrue(p.wants('death', 'email'))  # invariato

    def test_salva_tema(self):
        resp = self._post({'theme_preference': 'dark'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(UserProfile.objects.get(user=self.member).theme_preference, 'dark')

    def test_categoria_sconosciuta_rifiutata(self):
        resp = self._post({'prefs': {'inesistente': {'push': True}}})
        self.assertEqual(resp.status_code, 400)

    def test_canale_sconosciuto_rifiutato(self):
        resp = self._post({'prefs': {'death': {'sms': True}}})
        self.assertEqual(resp.status_code, 400)

    def test_tema_non_valido_rifiutato(self):
        resp = self._post({'theme_preference': 'fucsia'})
        self.assertEqual(resp.status_code, 400)


class PreseasonDeathTest(NotificationFeedBase):
    """Decesso PRIMA dell'inizio della lega: il membro resta in rosa e il posto
    è sempre recuperabile — togliendolo in composizione, sostituendolo a lega
    avviata. Date relative a oggi: qui conta *quando siamo*, non l'anno scritto
    nella fixture."""

    def _preseason_league_team(self, start_in_days=30, slug='lega-futura'):
        today = timezone.now().date()
        start = today + timedelta(days=start_in_days)
        league = League.objects.create(
            name='Lega Futura', slug=slug,
            owner=self.owner,
            start_date=start, end_date=start + timedelta(days=365),
            registration_opens=today - timedelta(days=30),
            registration_closes=start,
        )
        LeagueMembership.objects.create(league=league, user=self.owner, role='owner')
        team = Team.objects.create(name='Rosa Futura', manager=self.owner, league=league)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q999', name_it='Moritur Anzitempo',
            birth_date=date(1930, 1, 1), is_dead=False,
        )
        member = TeamMember.objects.create(team=team, person=person)
        return league, team, person, member

    def _confirm(self, person, league, when=None):
        """Decesso datato il giorno prima dell'inizio della lega."""
        person.is_dead = True
        person.save()
        return Death.objects.create(
            person=person, death_date=league.start_date - timedelta(days=1),
            death_age=95, is_confirmed=True, confirmed_at=when or timezone.now(),
        )

    def test_membro_non_rimosso_e_manager_notificato(self):
        league, team, person, member = self._preseason_league_team()
        self._confirm(person, league)
        # Il membro resta in rosa: cancellarlo lascerebbe senza rimedio chi non
        # può più intervenire.
        self.assertTrue(TeamMember.objects.filter(pk=member.pk).exists())
        n = Notification.objects.filter(
            user=self.owner, kind=Notification.KIND_PRESEASON_REMOVED,
        )
        self.assertEqual(n.count(), 1)
        self.assertTrue(n.first().is_urgent)
        self.assertIn('Moritur Anzitempo', n.first().title)
        # Iscrizioni aperte: il rimedio è togliere e rimpiazzare.
        self.assertIn('Toglilo/a dalla rosa', n.first().body)

    def test_in_composizione_non_si_sostituisce(self):
        league, team, person, member = self._preseason_league_team()
        self._confirm(person, league)
        member.refresh_from_db()
        self.assertTrue(member.died_before_season())
        self.assertFalse(member.can_be_substituted())

    def test_a_lega_avviata_diventa_sostituibile(self):
        """Il caso che prima era un vicolo cieco: conferma a squadre ormai chiuse."""
        league, team, person, member = self._preseason_league_team(start_in_days=-10)
        self._confirm(person, league)
        member.refresh_from_db()
        self.assertTrue(member.died_before_season())
        self.assertTrue(member.can_be_substituted())
        n = Notification.objects.filter(
            user=self.owner, kind=Notification.KIND_PRESEASON_REMOVED,
        ).first()
        self.assertIn('per sostituirlo/a', n.body)

    def test_deadline_decorre_dall_inizio_lega(self):
        """Conferma molto prima dell'avvio: la finestra non deve nascere scaduta."""
        league, team, person, member = self._preseason_league_team(start_in_days=-1)
        # Decesso confermato 60 giorni fa, cioè ben prima dell'inizio.
        self._confirm(person, league, when=timezone.now() - timedelta(days=60))
        member.refresh_from_db()
        deadline = member.get_substitution_deadline()
        self.assertIsNotNone(deadline)
        self.assertGreater(deadline, timezone.now())
        self.assertTrue(member.can_be_substituted())

    def test_morte_in_stagione_invariata(self):
        # Lega base: 2020→2030, decesso 2025 → in stagione: nessuna notifica
        # pre-stagione, e il membro resta sostituibile come sempre.
        self._confirm_death()
        member = TeamMember.objects.filter(team=self.team_owner, person=self.person).first()
        self.assertIsNotNone(member)
        self.assertFalse(member.died_before_season())
        self.assertTrue(member.can_be_substituted())
        self.assertFalse(
            Notification.objects.filter(kind=Notification.KIND_PRESEASON_REMOVED).exists()
        )


class DeviceLabelTest(TestCase):
    """Etichette dispositivo dagli User-Agent: contano i casi trappola.

    Ogni browser dichiara "Safari", Edge e Opera dichiarano "Chrome": se
    l'ordine dei pattern si rompe, l'utente vede il browser sbagliato.
    """

    CASI = [
        ('Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) '
         'Chrome/120.0.0.0 Mobile Safari/537.36', 'Chrome su Android'),
        ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
         '(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1', 'Safari su iPhone'),
        ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
         'Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0', 'Edge su Windows'),
        ('Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
         'Firefox su Windows'),
        ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
         '(KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148 Safari/604.1', 'Chrome su iPhone'),
        ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
         '(KHTML, like Gecko) FxiOS/121.0 Mobile/15E148 Safari/605.1.15', 'Firefox su iPhone'),
        ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
         '(KHTML, like Gecko) Version/17.0 Safari/605.1.15', 'Safari su macOS'),
        ('Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) '
         'SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36', 'Samsung Internet su Android'),
        ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
         'Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0', 'Opera su Linux'),
    ]

    def test_etichette(self):
        for ua, expected in self.CASI:
            with self.subTest(ua=ua[:40]):
                self.assertEqual(describe_user_agent(ua), expected)

    def test_user_agent_vuoto_o_ignoto(self):
        for ua in ('', '   ', None, 'curl/8.4.0'):
            self.assertEqual(describe_user_agent(ua), 'Dispositivo sconosciuto')

    def test_device_label_sul_modello(self):
        user = User.objects.create_user('device-user', password='x')
        sub = PushSubscription.objects.create(
            user=user, endpoint='https://push.example/x', p256dh='k', auth='a',
            user_agent=self.CASI[0][0],
        )
        self.assertEqual(sub.device_label, 'Chrome su Android')
        self.assertEqual(
            PushSubscription.objects.create(
                user=user, endpoint='https://push.example/y', p256dh='k', auth='a',
            ).device_label,
            'Dispositivo sconosciuto',
        )


class LegaConclusaTest(NotificationFeedBase):
    """A lega conclusa non si sostituisce più, e i messaggi non lo promettono.

    Il caso limite è la conferma *tardiva*: la deadline decorre da
    `confirmed_at`, quindi un decesso con data dentro la finestra ma confermato
    oggi aprirebbe una finestra di sostituzione dopo la fine della lega.
    """

    def _finished_league(self, slug='lega-chiusa', end=date(2024, 12, 31)):
        league = League.objects.create(
            name='Lega Chiusa', slug=slug, owner=self.owner,
            start_date=date(2024, 1, 1), end_date=end,
            registration_opens=date(2023, 12, 1), registration_closes=date(2023, 12, 31),
        )
        LeagueMembership.objects.create(league=league, user=self.owner, role='owner')
        LeagueMembership.objects.create(league=league, user=self.member, role='member')
        team = Team.objects.create(name='Rosa Chiusa', manager=self.owner, league=league)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q555', name_it='Defunto Tardivo',
            birth_date=date(1935, 1, 1), is_dead=False,
        )
        team_member = TeamMember.objects.create(team=team, person=person)
        return league, team, person, team_member

    def _confirm_late(self, person):
        """Decesso dentro la finestra della lega, confermato adesso."""
        person.is_dead = True
        person.save()
        return Death.objects.create(
            person=person, death_date=date(2024, 6, 1), death_age=89,
            is_confirmed=True, confirmed_at=timezone.now(),
        )

    def test_can_be_substituted_falso_a_lega_conclusa(self):
        _, _, person, team_member = self._finished_league()
        self._confirm_late(person)
        team_member.refresh_from_db()
        # La deadline è aperta (decorre da confirmed_at = adesso)...
        self.assertIsNotNone(team_member.get_substitution_deadline())
        self.assertGreater(team_member.get_substitution_deadline(), timezone.now())
        # ...ma la lega è finita: non si sostituisce.
        self.assertFalse(team_member.can_be_substituted())
        self.assertFalse(team_member.died_before_season())

    def test_can_be_substituted_vero_con_la_stessa_deadline_a_lega_in_corso(self):
        """Controprova: è la conclusione a decidere, non il tempo residuo."""
        league, _, person, team_member = self._finished_league()
        self._confirm_late(person)
        # Prolungo la lega: il gate è dinamico, la finestra torna disponibile.
        league.end_date = date(2030, 12, 31)
        league.save()
        team_member.refresh_from_db()
        self.assertTrue(team_member.can_be_substituted())

    def test_push_non_promette_sostituzione_a_lega_conclusa(self):
        _, _, person, _ = self._finished_league()
        PushSubscription.objects.create(
            user=self.owner, endpoint='https://push.example/owner', p256dh='k', auth='a',
        )
        with patch('game.push.send_push', return_value=True) as mock_send:
            self._confirm_late(person)
        payloads = [call.args[1] for call in mock_send.call_args_list]
        self.assertEqual(len(payloads), 1)
        # L'owner ha la persona in rosa: titolo urgente, ma nessuna promessa.
        self.assertTrue(payloads[0]['urgent'])
        self.assertNotIn('sostituirlo', payloads[0]['body'])
        self.assertNotIn('Lega Chiusa', payloads[0]['body'])

    def test_push_nomina_la_lega_del_destinatario_se_in_corso(self):
        """Il corpo non deve pescare una lega qualsiasi che contenga la data."""
        # L'altra lega (2020→2030, dalla fixture) contiene la stessa data ma
        # l'owner non ha la persona in rosa lì: non va nominata.
        altra = League.objects.create(
            name='Lega Altrui', slug='lega-altrui', owner=self.outsider,
            start_date=date(2024, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2023, 12, 1), registration_closes=date(2023, 12, 31),
            substitution_deadline_days=5,
        )
        LeagueMembership.objects.create(league=altra, user=self.outsider, role='owner')
        league, _, person, _ = self._finished_league()
        league.end_date = date(2030, 12, 31)
        league.substitution_deadline_days = 9
        league.save()
        PushSubscription.objects.create(
            user=self.owner, endpoint='https://push.example/owner', p256dh='k', auth='a',
        )
        PushSubscription.objects.create(
            user=self.member, endpoint='https://push.example/member', p256dh='k', auth='a',
        )
        with patch('game.push.send_push', return_value=True) as mock_send:
            self._confirm_late(person)
        bodies = {
            call.args[0].user_id: call.args[1]['body']
            for call in mock_send.call_args_list
        }
        # owner: persona in rosa nella sua lega → frase con LA SUA lega
        self.assertIn('9 giorni per sostituirlo', bodies[self.owner.pk])
        self.assertIn('Lega Chiusa', bodies[self.owner.pk])
        self.assertNotIn('Lega Altrui', bodies[self.owner.pk])
        # member: iscritto ma senza la persona in rosa → nessuna frase
        self.assertNotIn('sostituirlo', bodies[self.member.pk])

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_email_non_promette_sostituzione_a_lega_conclusa(self):
        _, _, person, _ = self._finished_league()
        mail.outbox = []
        self._confirm_late(person)
        # L'email per la lega conclusa va all'owner, che ha la persona in rosa:
        # senza il gate conterrebbe la promessa di sostituzione.
        chiuse = [m.body for m in mail.outbox if 'Lega Chiusa' in m.body]
        self.assertTrue(chiuse)
        for corpo in chiuse:
            self.assertNotIn('per sostituirlo', corpo)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_email_promette_sostituzione_solo_a_chi_ce_lha_in_rosa(self):
        """Controllo positivo: a lega in corso la frase c'è, ma solo all'owner."""
        league, _, person, _ = self._finished_league()
        league.end_date = date(2030, 12, 31)
        league.save()
        mail.outbox = []
        self._confirm_late(person)
        per_destinatario = {
            m.to[0]: m.body for m in mail.outbox if 'Lega Chiusa' in m.body
        }
        self.assertIn('per sostituirlo', per_destinatario[self.owner.email])
        self.assertNotIn('per sostituirlo', per_destinatario[self.member.email])
