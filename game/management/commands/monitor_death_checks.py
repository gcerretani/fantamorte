"""Avvisa lo staff via email se check_deaths è fermo o fallisce di continuo.

Da eseguire dallo scheduler dopo ogni check_deaths (deploy/scheduler.sh).
Idempotente: al massimo un avviso ogni 24 ore finché il problema persiste.
"""
from django.core.management.base import BaseCommand

from game.monitoring import alert_if_unhealthy


class Command(BaseCommand):
    help = 'Avvisa lo staff se i controlli decessi su Wikidata sono fermi o falliscono'

    def handle(self, *args, **options):
        status, sent = alert_if_unhealthy()
        self.stdout.write(f'Stato check_deaths: {status}' + (' · avviso inviato' if sent else ''))
