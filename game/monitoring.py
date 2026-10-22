"""Monitoraggio di check_deaths (#108).

`check_deaths` registra l'esito di ogni run su `SiteSettings`
(`record_death_check_success` / `record_death_check_failure`). Lo stato si
legge con `death_check_health()`: lo espone `/healthz/` (senza far fallire
l'healthcheck, che riavvierebbe il web per colpa di Wikidata) e lo usa il
command `monitor_death_checks`, che avvisa lo staff via email quando i
controlli sono fermi o falliscono di continuo — una volta al giorno al
massimo, e di nuovo solo dopo che si sono ripresi.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db.models import F
from django.utils import timezone

from .models import SiteSettings

logger = logging.getLogger(__name__)

FAILURES_BEFORE_ALERT = 3
ALERT_REPEAT = timedelta(hours=24)

STATUS_OK = 'ok'
STATUS_NEVER = 'never'
STATUS_STALE = 'stale'
STATUS_FAILING = 'failing'


def record_death_check_success():
    SiteSettings.get()  # garantisce la riga
    SiteSettings.objects.filter(pk=1).update(
        death_check_last_success_at=timezone.now(),
        death_check_consecutive_failures=0,
        death_check_last_error='',
    )


def record_death_check_failure(error):
    SiteSettings.get()
    SiteSettings.objects.filter(pk=1).update(
        death_check_last_failure_at=timezone.now(),
        death_check_consecutive_failures=F('death_check_consecutive_failures') + 1,
        death_check_last_error=str(error)[:500],
    )


def stale_after(site):
    """Oltre quanto tempo senza un run riuscito i controlli sono "fermi":
    tre cicli dello scheduler, e mai meno di due ore."""
    schedule = max(1, site.wikidata_check_schedule_hours)
    return timedelta(hours=max(3 * schedule, 2))


def death_check_health(site=None, now=None):
    site = site or SiteSettings.get()
    now = now or timezone.now()
    if site.death_check_consecutive_failures >= FAILURES_BEFORE_ALERT:
        status = STATUS_FAILING
    elif site.death_check_last_success_at is None:
        status = STATUS_NEVER
    elif now - site.death_check_last_success_at > stale_after(site):
        status = STATUS_STALE
    else:
        status = STATUS_OK
    return {
        'status': status,
        'last_success_at': site.death_check_last_success_at,
        'consecutive_failures': site.death_check_consecutive_failures,
        'last_error': site.death_check_last_error,
    }


def _staff_emails():
    User = get_user_model()
    emails = set(
        User.objects.filter(is_staff=True, is_active=True)
        .exclude(email='').values_list('email', flat=True)
    )
    emails.update(email for _, email in getattr(settings, 'ADMINS', []))
    return sorted(emails)


def alert_if_unhealthy(now=None):
    """Manda l'avviso allo staff se serve. Ritorna lo stato e se ha inviato.

    `never` non allerta: un'istanza appena installata non ha ancora storia.
    """
    now = now or timezone.now()
    site = SiteSettings.get()
    health = death_check_health(site, now)
    status = health['status']
    if status in (STATUS_OK, STATUS_NEVER):
        if site.death_check_alerted_at is not None:
            SiteSettings.objects.filter(pk=site.pk).update(death_check_alerted_at=None)
        return status, False
    if site.death_check_alerted_at and now - site.death_check_alerted_at < ALERT_REPEAT:
        return status, False
    recipients = _staff_emails()
    if not recipients:
        logger.error('check_deaths in stato %s ma nessuno staff con email da avvisare', status)
        return status, False

    last = health['last_success_at']
    last_txt = timezone.localtime(last).strftime('%d/%m/%Y %H:%M') if last else 'mai'
    if status == STATUS_FAILING:
        subject = f'[Fantamorte] check_deaths fallisce da {health["consecutive_failures"]} run'
    else:
        subject = '[Fantamorte] check_deaths non gira da troppo tempo'
    body = (
        f'Stato dei controlli decessi: {status}.\n'
        f'Ultimo run riuscito: {last_txt}.\n'
        f'Fallimenti consecutivi: {health["consecutive_failures"]}.\n'
    )
    if health['last_error']:
        body += f'Ultimo errore: {health["last_error"]}\n'
    body += (
        '\nFinché i controlli non ripartono, i decessi su Wikidata non vengono '
        'rilevati: niente punti né notifiche. Controlla il container scheduler '
        '(docker compose logs scheduler) e la raggiungibilità di query.wikidata.org.\n'
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)
    except Exception:
        logger.exception('Invio avviso check_deaths fallito')
        return status, False
    SiteSettings.objects.filter(pk=site.pk).update(death_check_alerted_at=now)
    return status, True
