"""Modulo centrale delle notifiche.

Principio "persist-first": ogni evento crea prima una riga `Notification`
(il feed in-app, **sempre attivo**), poi push ed email sono canali di consegna
costruiti sopra la stessa risoluzione di destinatari. Questo rende il sistema
canale-agnostico: aggiungere in futuro FCM/APNs sarà un nuovo canale accanto a
Web Push, riusando queste stesse funzioni e le righe `Notification`.

Qui vivono, in un solo punto:
- le categorie utente-visibili della matrice preferenze (categoria × canale);
- la mappa `kind → categoria` per il gating dei canali;
- la risoluzione dei destinatari di un decesso (condivisa da feed/push/email);
- le funzioni di creazione delle righe del feed;
- gli helper del badge (conteggio non-lette, mark-as-read).
"""
import logging

from django.urls import reverse

from .models import (
    League, LeagueMembership, Notification, Team, TeamMember,
    default_notification_prefs,
)

logger = logging.getLogger(__name__)


# Categorie utente-visibili della matrice preferenze (categoria × canale).
# Ogni categoria raggruppa uno o più `kind` di Notification. Definite QUI in un
# unico punto: UI, validazione dell'endpoint preferenze e gating dei canali le
# leggono da qui.
NOTIFICATION_CATEGORIES = [
    {
        'key': 'death',
        'label': 'Decessi nelle tue leghe',
        'help': 'Un personaggio muore in una lega di cui fai parte.',
        'kinds': [Notification.KIND_DEATH, Notification.KIND_DEATH_TEAM],
    },
    {
        'key': 'substitution',
        'label': 'Membro deceduto',
        'help': 'Un membro della tua squadra muore: reminder di sostituzione, '
                'o avviso se il decesso avviene prima dell\'inizio della lega.',
        'kinds': [Notification.KIND_SUBSTITUTION, Notification.KIND_PRESEASON_REMOVED],
    },
    {
        'key': 'league_joined',
        'label': 'Iscrizioni alla tua lega',
        'help': 'Qualcuno si iscrive a una lega di cui sei proprietario.',
        'kinds': [Notification.KIND_LEAGUE_JOINED],
    },
    {
        'key': 'league_events',
        'label': 'Inizio/fine lega e blocco squadra',
        'help': 'Una lega inizia o si conclude, oppure la tua squadra viene bloccata.',
        'kinds': [
            Notification.KIND_LEAGUE_STARTED,
            Notification.KIND_LEAGUE_ENDED,
            Notification.KIND_TEAM_LOCKED,
        ],
    },
]

# kind → categoria (per il gating di canale in push/email).
KIND_CATEGORY = {
    kind: cat['key']
    for cat in NOTIFICATION_CATEGORIES
    for kind in cat['kinds']
}

CHANNELS = ('push', 'email')
CATEGORY_KEYS = {cat['key'] for cat in NOTIFICATION_CATEGORIES}


# --------------------------------------------------------------------------
# Preferenze di canale
# --------------------------------------------------------------------------

def wants(user, kind_or_category, channel):
    """True se `user` vuole ricevere l'evento sul canale ('push'|'email').

    Accetta sia un `kind` di Notification sia una chiave di categoria. Il feed
    in-app non passa da qui: è sempre attivo.
    """
    category = KIND_CATEGORY.get(kind_or_category, kind_or_category)
    profile = getattr(user, 'profile', None)
    if profile is not None:
        return profile.wants(category, channel)
    defaults = default_notification_prefs().get(category, {})
    return bool(defaults.get(channel, False))


# --------------------------------------------------------------------------
# Risoluzione destinatari (condivisa feed/push/email)
# --------------------------------------------------------------------------

def leagues_for_death(death):
    """Leghe il cui periodo di gioco contiene la data del decesso."""
    return list(League.objects.filter(
        start_date__lte=death.death_date, end_date__gte=death.death_date,
    ))


def affected_manager_leagues(person, leagues):
    """`{manager_id: League}` per chi ha `person` in una squadra attiva in `leagues`.

    Serve a dire a ogni destinatario *la sua* lega: prendere una lega qualsiasi
    tra quelle che contengono la data del decesso significa nominarne una di cui
    l'utente magari non fa nemmeno parte. Se un manager è coinvolto in più leghe
    per lo stesso decesso se ne cita una (le notifiche sono per-decesso, non
    per-lega).
    """
    if not leagues:
        return {}
    teams = Team.objects.filter(
        members__person=person, members__replaced_by=None, league__in=leagues,
    ).select_related('league')
    return {t.manager_id: t.league for t in teams}


def affected_manager_ids(person, leagues):
    """Id dei manager che hanno `person` in una squadra attiva in `leagues`."""
    return set(affected_manager_leagues(person, leagues))


def death_member_user_ids(leagues):
    """Id (distinti) degli utenti iscritti alle leghe interessate dal decesso."""
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
    parts = [f'È deceduto/a il {date_str}.']
    if death.death_age:
        parts.append(f'Età: {death.death_age} anni.')
    return ' '.join(parts)


def create_death_notifications(death):
    """Crea una riga feed per ogni iscritto alle leghe interessate dal decesso.

    `KIND_DEATH_TEAM` + urgente per chi ha la persona in squadra attiva,
    altrimenti `KIND_DEATH`. Idempotente su (user, death, kind).
    Ritorna il numero di righe create.
    """
    leagues = leagues_for_death(death)
    if not leagues:
        return 0
    person = death.person
    affected = affected_manager_ids(person, leagues)
    user_ids = death_member_user_ids(leagues)
    if not user_ids:
        return 0

    url = reverse('death_detail', args=[death.pk])
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
    """Crea la riga feed del reminder di sostituzione per il manager."""
    user = team_member.team.manager
    person = team_member.person
    league = team_member.team.league
    title = (f'⏳ {days_left} giorn{"o" if days_left == 1 else "i"} '
             f'per sostituire {person.name_it}')
    body_parts = [f'{person.name_it} è deceduto/a e fa parte della tua squadra.']
    if league:
        body_parts.append(f'Lega: {league.name}.')
    return _create(
        user=user, kind=Notification.KIND_SUBSTITUTION,
        title=title, body=' '.join(body_parts),
        url=reverse('team_edit', args=[team_member.team_id]),
        is_urgent=True, league=league,
    )


def notify_preseason_member_dead(team, person):
    """Notifica il manager di un membro deceduto PRIMA dell'inizio della lega.

    Il rimedio dipende da dove siamo nel calendario, e il testo lo dice:
    in composizione si toglie dalla rosa e si sceglie un altro (la
    sostituzione non ha senso, il decesso non conta a punteggio); a lega
    avviata si sostituisce come una morte in stagione (vedi
    ``TeamMember.can_be_substituted``).
    """
    league = team.league
    body_parts = [f'{person.name_it} è deceduto/a prima dell\'inizio della lega.']
    if league and league.has_started():
        days = league.substitution_deadline_days or 0
        if days:
            body_parts.append(f'Hai {days} giorn{"o" if days == 1 else "i"} per sostituirlo/a.')
        else:
            body_parts.append('Puoi sostituirlo/a dalla pagina della squadra.')
    elif league and league.is_registration_open():
        body_parts.append('Toglilo/a dalla rosa e scegli un altro personaggio.')
    else:
        # Iscrizioni già chiuse ma lega non ancora iniziata: nessuna azione
        # possibile adesso, il diritto di sostituzione si apre all'avvio.
        body_parts.append('Potrai sostituirlo/a dall\'inizio della lega.')
    return _create(
        user=team.manager, kind=Notification.KIND_PRESEASON_REMOVED,
        title=f'☠ {person.name_it} è deceduto/a prima dell\'inizio',
        body=' '.join(body_parts),
        url=reverse('team_edit', args=[team.pk]),
        is_urgent=True, league=league,
    )


def notify_preseason_dead_members(death):
    """Notifica i manager che hanno in rosa un deceduto PRIMA dell'inizio lega.

    Il membro **non** viene rimosso dalla rosa. Cancellarlo lascerebbe il
    manager senza rimedio ogni volta che non può più intervenire — iscrizioni
    chiuse, oppure conferma arrivata a lega già avviata o conclusa, dove la
    riga sparirebbe in silenzio riscrivendo la rosa storica. Restando in rosa,
    il posto è recuperabile: prima dell'inizio togliendolo e scegliendone un
    altro, dall'inizio in poi con la sostituzione. Ritorna il numero di
    manager notificati.
    """
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
    """Notifica l'owner della lega quando un nuovo membro si iscrive."""
    league = membership.league
    joined = membership.user
    owner = league.owner
    if owner is None or owner.pk == joined.pk:
        return None
    return _create(
        user=owner, kind=Notification.KIND_LEAGUE_JOINED,
        title=f'{joined.username} si è iscritto a {league.name}',
        url=reverse('league_detail', args=[league.slug]),
        league=league,
    )


def notify_team_locked(team):
    """Notifica il manager quando la sua squadra viene bloccata."""
    return _create(
        user=team.manager, kind=Notification.KIND_TEAM_LOCKED,
        title='La tua squadra è stata bloccata',
        body=f'La rosa di "{team.name}" non è più modificabile.',
        url=reverse('team_detail', args=[team.pk]),
        league=team.league,
    )


def emit_league_lifecycle_notifications(league, kind):
    """Crea (idempotente) una notifica lifecycle per ogni membro della lega.

    `kind` ∈ {KIND_LEAGUE_STARTED, KIND_LEAGUE_ENDED}. Dedup su
    (user, league, kind). Ritorna il numero di righe create.
    """
    if kind == Notification.KIND_LEAGUE_STARTED:
        title = f'La lega {league.name} è iniziata'
        body = 'Le squadre sono definitive: da ora i decessi contano.'
    else:
        title = f'La lega {league.name} si è conclusa'
        body = 'Dai un\'occhiata alla classifica finale.'
    url = reverse('league_detail', args=[league.slug])
    created = 0
    for uid in LeagueMembership.objects.filter(league=league).values_list('user_id', flat=True):
        _, was_created = Notification.objects.get_or_create(
            user_id=uid, league=league, kind=kind,
            defaults={'title': title, 'body': body, 'url': url},
        )
        if was_created:
            created += 1
    return created


# --------------------------------------------------------------------------
# Badge / feed helpers
# --------------------------------------------------------------------------

def unread_count(user):
    if not getattr(user, 'is_authenticated', False):
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()


def mark_all_read(user):
    return Notification.objects.filter(user=user, is_read=False).update(is_read=True)
