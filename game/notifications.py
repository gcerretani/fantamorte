"""Modulo centrale delle notifiche.

Ogni evento crea prima una riga ``Notification`` nel feed in-app, che e'
sempre attivo. Push ed email sono canali opzionali configurabili *per singolo
evento* dal profilo utente.
"""
import logging

from django.urls import reverse

from .models import League, LeagueMembership, Notification, Team, TeamMember

logger = logging.getLogger(__name__)


# Una riga della UI per ogni Notification.kind: nessun raggruppamento implicito.
NOTIFICATION_CATEGORIES = [
    {
        'key': Notification.KIND_DEATH,
        'label': 'Decesso in una tua lega',
        'help': 'Muore un personaggio presente in una rosa della tua lega.',
        'default': {'push': True, 'email': True},
    },
    {
        'key': Notification.KIND_DEATH_TEAM,
        'label': 'Decesso nella tua squadra',
        'help': 'Muore un personaggio ancora presente nella tua rosa.',
        'default': {'push': True, 'email': True},
    },
    {
        'key': Notification.KIND_PRESEASON_REMOVED,
        'label': 'Decesso prima dell\'inizio della lega',
        'help': 'Un personaggio della tua rosa muore prima dell\'inizio della lega.',
        'default': {'push': True, 'email': True},
    },
    {
        'key': Notification.KIND_SUBSTITUTION,
        'label': 'Reminder di sostituzione',
        'help': 'La finestra per sostituire un personaggio deceduto sta per scadere.',
        'default': {'push': True, 'email': True},
    },
    {
        'key': Notification.KIND_LEAGUE_JOINED,
        'label': 'Nuovo iscritto alla tua lega',
        'help': 'Qualcuno si iscrive a una lega di cui sei proprietario.',
        'default': {'push': False, 'email': False},
    },
    {
        'key': Notification.KIND_LEAGUE_STARTED,
        'label': 'Lega iniziata',
        'help': 'Una lega di cui fai parte entra nel periodo di gioco.',
        'default': {'push': False, 'email': False},
    },
    {
        'key': Notification.KIND_LEAGUE_ENDED,
        'label': 'Lega conclusa',
        'help': 'Una lega di cui fai parte termina il periodo di gioco.',
        'default': {'push': False, 'email': False},
    },
    {
        'key': Notification.KIND_TEAM_LOCKED,
        'label': 'Squadra bloccata',
        'help': 'La tua rosa viene bloccata e non e' piu' modificabile.',
        'default': {'push': False, 'email': False},
    },
]

CHANNELS = ('push', 'email')
CATEGORY_KEYS = {cat['key'] for cat in NOTIFICATION_CATEGORIES}
EVENT_DEFAULTS = {cat['key']: dict(cat['default']) for cat in NOTIFICATION_CATEGORIES}

# Compatibilita' con le preferenze salvate prima della v1. La migration 0027
# espande queste chiavi, ma il fallback rende sicuro anche un DB non ancora
# migrato durante un rolling deploy.
LEGACY_CATEGORY_BY_EVENT = {
    Notification.KIND_DEATH: 'death',
    Notification.KIND_DEATH_TEAM: 'death',
    Notification.KIND_SUBSTITUTION: 'substitution',
    Notification.KIND_PRESEASON_REMOVED: 'substitution',
    Notification.KIND_LEAGUE_JOINED: 'league_joined',
    Notification.KIND_LEAGUE_STARTED: 'league_events',
    Notification.KIND_LEAGUE_ENDED: 'league_events',
    Notification.KIND_TEAM_LOCKED: 'league_events',
}


def expand_legacy_notification_prefs(prefs):
    """Espande la vecchia matrice raggruppata nelle 8 chiavi per-evento.

    Le preferenze per-evento gia' presenti vincono. Le vecchie chiavi vengono
    mantenute: sono innocue e permettono downgrade/rollback senza perdere le
    scelte dell'utente.
    """
    source = dict(prefs or {})
    result = dict(source)
    for event, legacy in LEGACY_CATEGORY_BY_EVENT.items():
        if event in result:
            continue
        legacy_state = source.get(legacy)
        if isinstance(legacy_state, dict):
            result[event] = {
                'push': bool(legacy_state.get('push', EVENT_DEFAULTS[event]['push'])),
                'email': bool(legacy_state.get('email', EVENT_DEFAULTS[event]['email'])),
            }
        else:
            result[event] = dict(EVENT_DEFAULTS[event])
    return result


def wants(user, kind, channel):
    """True se ``user`` vuole ``kind`` sul canale push/email.

    Il feed in-app non passa da qui ed e' sempre attivo.
    """
    if channel not in CHANNELS:
        return False
    default = EVENT_DEFAULTS.get(kind, {}).get(channel, False)
    profile = getattr(user, 'profile', None)
    if profile is None:
        return bool(default)
    prefs = profile.notification_prefs or {}
    event_state = prefs.get(kind)
    if isinstance(event_state, dict) and channel in event_state:
        return bool(event_state[channel])
    legacy = LEGACY_CATEGORY_BY_EVENT.get(kind)
    legacy_state = prefs.get(legacy) if legacy else None
    if isinstance(legacy_state, dict) and channel in legacy_state:
        return bool(legacy_state[channel])
    return bool(default)


# --------------------------------------------------------------------------
# Risoluzione destinatari (condivisa feed/push/email)
# --------------------------------------------------------------------------

def leagues_for_death(death):
    """Leghe realmente interessate dal decesso.

    Una lega conta solo se la data del decesso cade nel suo periodo di gioco
    e la persona e' ancora presente in almeno una rosa attiva di quella lega.
    """
    return list(League.objects.filter(
        start_date__lte=death.death_date,
        end_date__gte=death.death_date,
        teams__members__person=death.person,
        teams__members__replaced_by=None,
    ).distinct())


def affected_manager_leagues(person, leagues):
    if not leagues:
        return {}
    teams = Team.objects.filter(
        members__person=person, members__replaced_by=None, league__in=leagues,
    ).select_related('league')
    return {t.manager_id: t.league for t in teams}


def affected_manager_ids(person, leagues):
    return set(affected_manager_leagues(person, leagues))


def death_member_user_ids(leagues):
    return list(
        LeagueMembership.objects.filter(league__in=leagues)
        .values_list('user_id', flat=True).distinct()
    )


# --------------------------------------------------------------------------
# Creazione righe del feed
# --------------------------------------------------------------------------

def _create(user, kind, title, body='', url='', is_urgent=False, death=None, league=None):
    return Notification.objects.create(
        user=user, kind=kind, title=title, body=body, url=url,
        is_urgent=is_urgent, death=death, league=league,
    )


def _death_body(death):
    dd = death.death_date
    date_str = dd.strftime('%d/%m/%Y') if hasattr(dd, 'strftime') else str(dd)
    parts = [f'E' deceduto/a il {date_str}.']
    if death.death_age:
        parts.append(f'Età: {death.death_age} anni.')
    return ' '.join(parts)


def create_death_notifications(death):
    leagues = leagues_for_death(death)
    if not leagues:
        return 0
    person = death.person
    affected = affected_manager_ids(person, leagues)
    user_ids = death_member_user_ids(leagues)
    if not user_ids:
        return 0

    url = reverse('person_detail', args=[death.person_id]) + '#decesso'
    body = _death_body(death)
    created = 0
    for uid in user_ids:
        is_affected = uid in affected
        if is_affected:
            kind = Notification.KIND_DEATH_TEAM
            title = f'☠ {person.name_it} era nella tua squadra!'
        else:
            kind = Notification.KIND_DEATH
            title = f'☠ {person.name_it}'
        _, was_created = Notification.objects.get_or_create(
            user_id=uid, death=death, kind=kind,
            defaults={'title': title, 'body': body, 'url': url, 'is_urgent': is_affected},
        )
        if was_created:
            created += 1
    logger.info('Feed decesso %s: %d notifiche create', person.name_it, created)
    return created


def create_substitution_notification(team_member, days_left):
    user = team_member.team.manager
    person = team_member.person
    league = team_member.team.league
    title = (f'⏳ {days_left} giorn{"o" if days_left == 1 else "i"} '
             f'per sostituire {person.name_it}')
    body_parts = [f'{person.name_it} e' deceduto/a e fa parte della tua squadra.']
    if league:
        body_parts.append(f'Lega: {league.name}.')
    return _create(
        user=user, kind=Notification.KIND_SUBSTITUTION,
        title=title, body=' '.join(body_parts),
        url=reverse('team_edit', args=[team_member.team_id]),
        is_urgent=True, league=league,
    )


def notify_preseason_member_dead(team, person):
    league = team.league
    body_parts = [f'{person.name_it} e' deceduto/a prima dell\'inizio della lega.']
    if league and league.has_started():
        days = league.substitution_deadline_days or 0
        if days:
            body_parts.append(f'Hai {days} giorn{"o" if days == 1 else "i"} per sostituirlo/a.')
        else:
            body_parts.append('Puoi sostituirlo/a dalla pagina della squadra.')
    elif league and league.is_registration_open():
        body_parts.append('Toglilo/a dalla rosa e scegli un altro personaggio.')
    else:
        body_parts.append('Potrai sostituirlo/a dall\'inizio della lega.')
    return _create(
        user=team.manager, kind=Notification.KIND_PRESEASON_REMOVED,
        title=f'☠ {person.name_it} e' deceduto/a prima dell\'inizio',
        body=' '.join(body_parts),
        url=reverse('team_edit', args=[team.pk]),
        is_urgent=True, league=league,
    )


def notify_preseason_dead_members(death):
    if not death.death_date:
        return 0
    members = TeamMember.objects.filter(
        person=death.person, replaced_by=None,
        team__league__start_date__gt=death.death_date,
    ).select_related('team', 'team__manager', 'team__league')
    notified = 0
    for member in members:
        notify_preseason_member_dead(member.team, death.person)
        notified += 1
    if notified:
        logger.info('Decesso pre-stagione di %s: %d manager notificati',
                    death.person.name_it, notified)
    return notified


def notify_league_joined(membership):
    league = membership.league
    joined = membership.user
    owner = league.owner
    if owner is None or owner.pk == joined.pk:
        return None
    return _create(
        user=owner, kind=Notification.KIND_LEAGUE_JOINED,
        title=f'{joined.username} si e' iscritto a {league.name}',
        url=reverse('league_detail', args=[league.slug]),
        league=league,
    )


def notify_team_locked(team):
    return _create(
        user=team.manager, kind=Notification.KIND_TEAM_LOCKED,
        title='La tua squadra e' stata bloccata',
        body=f'La rosa di "{team.name}" non e' piu' modificabile.',
        url=reverse('team_detail', args=[team.pk]),
        league=team.league,
    )


def emit_league_lifecycle_notifications(league, kind):
    if kind == Notification.KIND_LEAGUE_STARTED:
        title = f'La lega {league.name} e' iniziata'
        body = 'Le squadre sono definitive: da ora i decessi contano.'
    elif kind == Notification.KIND_LEAGUE_ENDED:
        title = f'La lega {league.name} si e' conclusa'
        body = 'Dai un\'occhiata alla classifica finale.'
    else:
        return 0
    url = reverse('league_detail', args=[league.slug])
    created = 0
    memberships = LeagueMembership.objects.filter(league=league).select_related('user')
    for membership in memberships:
        notification, was_created = Notification.objects.get_or_create(
            user=membership.user, league=league, kind=kind,
            defaults={'title': title, 'body': body, 'url': url},
        )
        if not was_created:
            continue
        created += 1
        # I canali vengono emessi solo assieme alla prima creazione del feed:
        # una seconda esecuzione del cron non duplica push/email.
        try:
            from .push import send_league_lifecycle_push
            send_league_lifecycle_push(membership.user, league, kind)
        except Exception:
            logger.exception('Errore push lifecycle %s per user %s', kind, membership.user_id)
        try:
            from .email import send_event_email
            send_event_email(membership.user, kind, notification.title,
                             notification.body, notification.url)
        except Exception:
            logger.exception('Errore email lifecycle %s per user %s', kind, membership.user_id)
    return created


def unread_count(user):
    if not getattr(user, 'is_authenticated', False):
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()


def mark_all_read(user):
    return Notification.objects.filter(user=user, is_read=False).update(is_read=True)
