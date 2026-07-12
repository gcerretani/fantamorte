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

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import notifications as notif
from .models import (
    Death, League, LeagueMembership, Notification, Team, TeamMember,
    UserProfile, WikipediaPerson, default_notification_prefs,
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


class PreseasonDeathRemovalTest(NotificationFeedBase):
    """Decesso PRIMA dell'inizio della lega (fase di composizione): il membro
    va rimosso dalla rosa — non sostituito — e il manager notificato. Le morti
    in stagione restano gestite dal flusso di sostituzione."""

    def _preseason_league_team(self):
        # Lega che INIZIA dopo la data del decesso (2025-06-01): composizione.
        league = League.objects.create(
            name='Lega Futura', slug='lega-futura', owner=self.owner,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            registration_opens=date(2025, 1, 1), registration_closes=date(2025, 12, 31),
        )
        LeagueMembership.objects.create(league=league, user=self.owner, role='owner')
        team = Team.objects.create(name='Rosa Futura', manager=self.owner, league=league)
        person = WikipediaPerson.objects.create(
            wikidata_id='Q999', name_it='Moritur Anzitempo',
            birth_date=date(1930, 1, 1), is_dead=False,
        )
        member = TeamMember.objects.create(team=team, person=person)
        return league, team, person, member

    def test_membro_rimosso_e_manager_notificato(self):
        league, team, person, member = self._preseason_league_team()
        person.is_dead = True
        person.save()
        # Conferma del decesso pre-stagione → il signal rimuove il membro.
        Death.objects.create(
            person=person, death_date=date(2025, 6, 1), death_age=95, is_confirmed=True,
        )
        self.assertFalse(TeamMember.objects.filter(pk=member.pk).exists())
        n = Notification.objects.filter(
            user=self.owner, kind=Notification.KIND_PRESEASON_REMOVED,
        )
        self.assertEqual(n.count(), 1)
        self.assertTrue(n.first().is_urgent)
        self.assertIn('Moritur Anzitempo', n.first().title)

    def test_died_before_season_e_no_sostituzione(self):
        league, team, person, member = self._preseason_league_team()
        person.is_dead = True
        person.save()
        # Decesso NON confermato: niente auto-rimozione, così testiamo i metodi
        # del model sul membro ancora presente in rosa.
        Death.objects.create(person=person, death_date=date(2025, 6, 1), is_confirmed=False)
        member.refresh_from_db()
        self.assertTrue(member.died_before_season())
        self.assertFalse(member.can_be_substituted())

    def test_morte_in_stagione_non_rimossa(self):
        # Lega base: 2020→2030, decesso 2025 → in stagione: nessuna rimozione,
        # nessuna notifica pre-stagione, e il membro resta sostituibile.
        self._confirm_death()
        member = TeamMember.objects.filter(team=self.team_owner, person=self.person).first()
        self.assertIsNotNone(member)
        self.assertFalse(member.died_before_season())
        self.assertTrue(member.can_be_substituted())
        self.assertFalse(
            Notification.objects.filter(kind=Notification.KIND_PRESEASON_REMOVED).exists()
        )
