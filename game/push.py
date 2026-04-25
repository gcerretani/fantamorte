"""Invio notifiche Web Push (VAPID) tramite pywebpush.

La libreria pywebpush è opzionale: se non installata, le notifiche vengono
silenziosamente saltate. Questo permette di sviluppare senza dipendenze
crittografiche pesanti, e di abilitare push solo in produzione.
"""
import json
import logging
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Death, PushSubscription, Team

logger = logging.getLogger(__name__)


def _vapid_configured():
    return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '')) and \
        bool(getattr(settings, 'VAPID_CLAIM_EMAIL', ''))


def send_push(subscription: PushSubscription, payload: dict) -> bool:
    """Invia un messaggio push a una subscription. Ritorna True se riuscito."""
    if not _vapid_configured():
        logger.debug('VAPID non configurato, push saltata')
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning('pywebpush non installata, push saltata')
        return False

    try:
        webpush(
            subscription_info=subscription.to_dict(),
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={'sub': f'mailto:{settings.VAPID_CLAIM_EMAIL}'},
            ttl=60 * 60 * 24,
        )
        subscription.last_used_at = timezone.now()
        subscription.save(update_fields=['last_used_at'])
        return True
    except WebPushException as e:
        # 404/410 = subscription non più valida → la rimuoviamo
        status = getattr(e.response, 'status_code', None)
        if status in (404, 410):
            subscription.delete()
        else:
            logger.warning('Errore push (%s): %s', status, e)
        return False


def broadcast_death_notification(death: Death) -> int:
    """Notifica tutti i subscriber attivi (con preferenza push attiva) di un decesso.

    Ritorna il numero di notifiche inviate con successo.
    """
    person = death.person
    season = death.season
    payload_base = {
        'type': 'death',
        'title': f'☠ {person.name_it}',
        'body': _build_body(death),
        'url': reverse('death_detail', args=[death.pk]),
        'tag': f'death-{death.pk}',
        'death_id': death.pk,
    }

    user_ids = set(
        Team.objects.filter(season=season).values_list('manager_id', flat=True)
    )
    qs = PushSubscription.objects.filter(
        user_id__in=user_ids,
        user__profile__push_notifications_enabled=True,
    ).select_related('user')

    sent = 0
    for sub in qs:
        # Se l'utente è nella squadra ed ha il giocatore in rosa, lo segnaliamo
        affected = Team.objects.filter(
            manager=sub.user, season=season, members__person=person, members__replaced_by=None
        ).exists()
        payload = dict(payload_base)
        if affected:
            payload['title'] = f'☠ {person.name_it} era nella tua squadra!'
            payload['urgent'] = True
        if send_push(sub, payload):
            sent += 1
    logger.info('Push decesso %s: %d notifiche inviate', person.name_it, sent)
    return sent


def _build_body(death: Death) -> str:
    dd = death.death_date
    date_str = dd.strftime('%d/%m/%Y') if hasattr(dd, 'strftime') else str(dd)
    parts = [f'È deceduto/a il {date_str}.']
    if death.death_age:
        parts.append(f'Età: {death.death_age} anni.')
    days = death.season.substitution_deadline_days
    if days:
        parts.append(f'Hai {days} giorni per sostituire il giocatore.')
    return ' '.join(parts)
