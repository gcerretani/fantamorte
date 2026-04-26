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

from .models import Death, PushSubscription, Team, League, LeagueMembership

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
    """Notifica gli iscritti alle leghe in cui il decesso cade nel periodo di gioco.

    Per ogni utente iscritto a una lega "interessata" dal decesso, manda una
    notifica push (se ha abilitato l'opzione). Se l'utente ha quella persona
    nella propria squadra di una di quelle leghe, la notifica è "urgent".
    Ritorna il numero totale di notifiche consegnate.
    """
    person = death.person
    payload_base = {
        'type': 'death',
        'title': f'☠ {person.name_it}',
        'body': _build_body(death),
        'url': reverse('death_detail', args=[death.pk]),
        'tag': f'death-{death.pk}',
        'death_id': death.pk,
    }

    # Leghe il cui range contiene la data del decesso
    leagues = list(League.objects.filter(
        start_date__lte=death.death_date, end_date__gte=death.death_date,
    ))
    if not leagues:
        # Fallback: stagione legacy
        if death.season_id:
            user_ids = set(
                Team.objects.filter(season_id=death.season_id).values_list('manager_id', flat=True)
            )
        else:
            user_ids = set()
    else:
        user_ids = set(
            LeagueMembership.objects.filter(league__in=leagues).values_list('user_id', flat=True)
        )

    if not user_ids:
        return 0

    subs = PushSubscription.objects.filter(
        user_id__in=user_ids,
        user__profile__push_notifications_enabled=True,
    ).select_related('user')

    sent = 0
    for sub in subs:
        affected = Team.objects.filter(
            manager=sub.user, members__person=person, members__replaced_by=None,
            league__in=leagues,
        ).exists()
        payload = dict(payload_base)
        if affected:
            payload['title'] = f'☠ {person.name_it} era nella tua squadra!'
            payload['urgent'] = True
        if send_push(sub, payload):
            sent += 1
    logger.info('Push decesso %s: %d notifiche inviate', person.name_it, sent)
    return sent


def send_substitution_reminder_push(team_member, days_left: int) -> bool:
    """Invia un reminder push a chi possiede `team_member` ricordando la deadline.

    Ritorna True se almeno una subscription ha ricevuto la notifica.
    """
    user = team_member.team.manager
    profile = getattr(user, 'profile', None)
    if not profile or not profile.push_notifications_enabled:
        return False

    person = team_member.person
    title = f'⏳ {days_left} giorn{"o" if days_left == 1 else "i"} per sostituire {person.name_it}'
    body_parts = [f'{person.name_it} è deceduto/a e fa parte della tua squadra.']
    if team_member.team.league_id:
        body_parts.append(f'Lega: {team_member.team.league.name}.')
    payload = {
        'type': 'substitution_reminder',
        'title': title,
        'body': ' '.join(body_parts),
        'url': reverse('team_edit', args=[team_member.team_id]),
        'tag': f'sub-reminder-{team_member.pk}-{days_left}',
        'urgent': True,
    }

    subs = PushSubscription.objects.filter(user=user)
    sent_any = False
    for sub in subs:
        if send_push(sub, payload):
            sent_any = True
    return sent_any


def _build_body(death: Death) -> str:
    dd = death.death_date
    date_str = dd.strftime('%d/%m/%Y') if hasattr(dd, 'strftime') else str(dd)
    parts = [f'È deceduto/a il {date_str}.']
    if death.death_age:
        parts.append(f'Età: {death.death_age} anni.')
    leagues = League.objects.filter(start_date__lte=dd, end_date__gte=dd) if hasattr(dd, 'year') else []
    league = leagues.first() if leagues else None
    if league:
        parts.append(f'Hai {league.substitution_deadline_days} giorni per sostituirlo (lega {league.name}).')
    elif death.season_id:
        parts.append(f'Hai {death.season.substitution_deadline_days} giorni per sostituire il giocatore.')
    return ' '.join(parts)
