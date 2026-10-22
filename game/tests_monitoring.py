"""Monitoraggio di check_deaths (#108)."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import League, SiteSettings, Team, TeamMember, WikipediaPerson
from .monitoring import (
    STATUS_FAILING, STATUS_NEVER, STATUS_OK, STATUS_STALE, alert_if_unhealthy,
    death_check_health,
)

User = get_user_model()
BATCH = 'wikidata_api.client.WikidataClient.check_deaths_batch'


class _Base(TestCase):

    def setUp(self):
        self.staff = User.objects.create_user('staff', email='staff@example.com', is_staff=True)
        self.site = SiteSettings.get()

    def set_site(self, **fields):
        SiteSettings.objects.filter(pk=1).update(**fields)
        self.site = SiteSettings.get()


class CheckDeathsRegistraEsitoTest(_Base):

    def setUp(self):
        super().setUp()
        owner = User.objects.create_user('owner')
        today = timezone.now().date()
        league = League.objects.create(
            name='L', slug='l', owner=owner, start_date=today, end_date=today + timedelta(days=30),
            registration_opens=today, registration_closes=today)
        team = Team.objects.create(name='T', manager=owner, league=league)
        TeamMember.objects.create(
            team=team, person=WikipediaPerson.objects.create(wikidata_id='Q1', name_it='Vivo'))

    def test_successo(self):
        with patch(BATCH, return_value=[]):
            call_command('check_deaths', '--force', stdout=StringIO())
        site = SiteSettings.get()
        self.assertIsNotNone(site.death_check_last_success_at)
        self.assertEqual(site.death_check_consecutive_failures, 0)

    def test_fallimenti_consecutivi_e_reset(self):
        for _ in range(2):
            with patch(BATCH, side_effect=RuntimeError('WDQS 503')), self.assertRaises(CommandError):
                call_command('check_deaths', '--force', stdout=StringIO())
        site = SiteSettings.get()
        self.assertEqual(site.death_check_consecutive_failures, 2)
        self.assertIn('WDQS 503', site.death_check_last_error)
        with patch(BATCH, return_value=[]):
            call_command('check_deaths', '--force', stdout=StringIO())
        self.assertEqual(SiteSettings.get().death_check_consecutive_failures, 0)

    def test_dry_run_non_registra(self):
        with patch(BATCH, return_value=[]):
            call_command('check_deaths', '--force', '--dry-run', stdout=StringIO())
        self.assertIsNone(SiteSettings.get().death_check_last_success_at)

    def test_nessuna_rosa_attiva_conta_come_successo(self):
        TeamMember.objects.all().delete()
        call_command('check_deaths', stdout=StringIO())
        self.assertIsNotNone(SiteSettings.get().death_check_last_success_at)


class HealthTest(_Base):

    def test_stati(self):
        now = timezone.now()
        self.assertEqual(death_check_health(self.site, now)['status'], STATUS_NEVER)
        self.set_site(death_check_last_success_at=now - timedelta(minutes=30))
        self.assertEqual(death_check_health(self.site, now)['status'], STATUS_OK)
        self.set_site(death_check_last_success_at=now - timedelta(hours=4))
        self.assertEqual(death_check_health(self.site, now)['status'], STATUS_STALE)
        self.set_site(death_check_last_success_at=now, death_check_consecutive_failures=3)
        self.assertEqual(death_check_health(self.site, now)['status'], STATUS_FAILING)

    def test_soglia_segue_la_cadenza_dello_scheduler(self):
        now = timezone.now()
        self.set_site(wikidata_check_schedule_hours=6,
                      death_check_last_success_at=now - timedelta(hours=10))
        self.assertEqual(death_check_health(self.site, now)['status'], STATUS_OK)

    def test_healthz_espone_lo_stato_senza_fallire(self):
        self.set_site(death_check_consecutive_failures=5)
        resp = self.client.get(reverse('healthz'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'status': 'ok', 'death_check': STATUS_FAILING})


class AlertTest(_Base):

    def test_avviso_una_volta_al_giorno_e_reset_alla_ripresa(self):
        now = timezone.now()
        self.set_site(death_check_last_success_at=now - timedelta(hours=10))
        self.assertEqual(alert_if_unhealthy(now), (STATUS_STALE, True))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['staff@example.com'])
        self.assertIn('non gira da troppo tempo', mail.outbox[0].subject)
        # Stesso problema poco dopo: niente doppione.
        self.assertEqual(alert_if_unhealthy(now + timedelta(hours=1)), (STATUS_STALE, False))
        # Dopo 24 ore si ricorda.
        self.assertEqual(alert_if_unhealthy(now + timedelta(hours=25)), (STATUS_STALE, True))
        # Ripresa: si azzera, un nuovo guasto riavvisa subito.
        self.set_site(death_check_last_success_at=now + timedelta(hours=26))
        self.assertEqual(alert_if_unhealthy(now + timedelta(hours=26)), (STATUS_OK, False))
        self.assertIsNone(SiteSettings.get().death_check_alerted_at)

    def test_istanza_nuova_non_allerta(self):
        self.assertEqual(alert_if_unhealthy(), (STATUS_NEVER, False))
        self.assertEqual(mail.outbox, [])

    def test_fallimenti_nel_testo(self):
        self.set_site(death_check_last_success_at=timezone.now(),
                      death_check_consecutive_failures=4, death_check_last_error='Errore SPARQL: 429')
        out = StringIO()
        call_command('monitor_death_checks', stdout=out)
        self.assertIn('avviso inviato', out.getvalue())
        self.assertIn('fallisce da 4 run', mail.outbox[0].subject)
        self.assertIn('Errore SPARQL: 429', mail.outbox[0].body)
