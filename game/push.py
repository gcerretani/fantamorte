"""Invio notifiche Web Push (VAPID) tramite pywebpush."""
import json
import logging
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Death, League, PushSubscription
from .push_security import NoRedirectSession, UnsafePushEndpoint, validate_push_endpoint

logger = logging.getLogger(__name__)


def _vapid_configured():
    return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '')) and \
        bool(getattr(settings, 'VAPID_CLAIM_EMAIL', ''))


def send_push(subscription: PushSubscription, payload: dict) -> bool:
    """Invia un messaggio push solo verso un endpoint pubblico validato."""
    if not _vapid_configured():
        logger.debug('VAPID non configurato, push saltata')
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.warning('pywebpush non installata, push saltata')
        return False

    try:
        # Resolve immediately before the outbound call, not just when the
        # subscription was stored. This also protects legacy rows.
        validate_push_endpoint(subscription.endpoint, resolve=True)
        with NoRedirectSession() as session:
            webpush(
                subscription_info=subscription.to_dict(),
                data=json.dumps(payload),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={'sub': f'mailto:{settings.VAPID_CLAIM_EMAIL}'},
                ttl=60 * 60 * 24,
                timeout=10,
                requests_session=session,
            )
        subscription.last_used_at = timezone.now()
        subscription.save(update_fields=['last_used_at'])
        return True
    except UnsafePushEndpoint as e:
        logger.warning('Endpoint push rifiutato per %s: %s', subscription.pk, e)
        return False
    except WebPushException as e:
        status = getattr(e.response, 'status_code', None)
        if status in (404, 410):
            subscription.delete()
        else:
            logger.warning('Errore push (%s): %s', status, e)
        return False


def broadcast_death_notification(death: Death) -> int:
    from .notifications import (
        affected_manager_leagues, death_member_user_ids, leagues_for_death, wants,
    )

    person = death.person
    payload_base = {
        'type': 'death',
        'title': f'☠ {person.name_it}',
        'body': _build_body(death),
        'url': reverse('person_detail', args=[death.person_id]) + '#decesso',
        'tag': f'death-{death.pk}',
        'death_id': death.pk,
    }
    leagues = leagues_for_death(death)
    user_ids = death_member_user_ids(leagues)
    if not user_ids:
        return 0

    affected_leagues = affected_manager_leagues(person, leagues)
    subs = PushSubscription.objects.filter(user_id__in=user_ids).select_related('user')
    sent = 0
    for sub in subs:
        if not wants(sub.user, 'death', 'push'):
            continue
        payload = dict(payload_base)
        league = affected_leagues.get(sub.user_id)
        if league is not None:
            payload['title'] = f'☠ {person.name_it} era nella tua squadra!'
            payload['urgent'] = True
            hint = _substitution_hint(league)
            if hint:
                payload['body'] = f'{payload["body"]} {hint}'
        if send_push(sub, payload):
            sent += 1
    logger.info('Push decesso %s: %d notifiche inviate', person.name_it, sent)
    return sent


def send_substitution_reminder_push(team_member, days_left: int) -> bool:
    from .notifications import wants

    user = team_member.team.manager
    if not wants(user, 'substitution', 'push'):
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
    return ' '.join(parts)


def _substitution_hint(league: League) -> str:
    if league is None or league.is_finished():
        return ''
    days = league.substitution_deadline_days or 0
    if days <= 0:
        return ''
    return f'Hai {days} giorn{"o" if days == 1 else "i"} per sostituirlo (lega {league.name}).'
