"""Invio email transazionali per gli eventi del feed notifiche."""
import logging

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import escape

from .models import Death, LeagueMembership, Notification, Team, TeamMember

logger = logging.getLogger(__name__)


def _email_configured() -> bool:
    return bool(getattr(settings, 'DEFAULT_FROM_EMAIL', ''))


def _site_base_url() -> str:
    base = getattr(settings, 'SITE_BASE_URL', '') or ''
    if base:
        return base.rstrip('/')
    try:
        domain = Site.objects.get_current().domain
        scheme = 'https' if not settings.DEBUG else 'http'
        return f'{scheme}://{domain}'
    except Exception:
        return ''


def _abs_url(path: str) -> str:
    base = _site_base_url()
    return f'{base}{path}' if base else path


def _send(to_email: str, subject: str, context: dict, template_base: str) -> bool:
    if not _email_configured() or not to_email:
        return False
    try:
        text_body = render_to_string(f'{template_base}.txt', context)
        html_body = render_to_string(f'{template_base}.html', context)
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        return True
    except Exception as e:
        logger.warning('Errore invio email a %s (%s): %s', to_email, template_base, e)
        return False


def send_event_email(user, kind, title, body='', url='') -> bool:
    """Email generica per gli eventi che non hanno un template specializzato."""
    from .notifications import wants

    if not _email_configured() or not user.email or not wants(user, kind, 'email'):
        return False
    target = _abs_url(url) if url else _site_base_url()
    text = body or title
    if target:
        text = f'{text}\n\nApri Fantamorte: {target}'
    html = f'<p>{escape(body or title)}</p>'
    if target:
        html += f'<p><a href="{escape(target)}">Apri Fantamorte</a></p>'
    try:
        msg = EmailMultiAlternatives(
            subject=title,
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        return True
    except Exception as e:
        logger.warning('Errore invio email evento %s a %s: %s', kind, user.email, e)
        return False


def broadcast_death_email(death: Death) -> int:
    if not _email_configured():
        return 0

    from .notifications import leagues_for_death, wants

    person = death.person
    leagues = leagues_for_death(death)
    memberships = LeagueMembership.objects.filter(
        league__in=leagues,
    ).select_related('user', 'league', 'user__profile')

    recipients = []
    for membership in memberships:
        user = membership.user
        league = membership.league
        if not user.email:
            continue
        affected = Team.objects.filter(
            manager=user,
            league=league,
            members__person=person,
            members__replaced_by=None,
        ).exists()
        kind = Notification.KIND_DEATH_TEAM if affected else Notification.KIND_DEATH
        if wants(user, kind, 'email'):
            recipients.append((user, league, affected))

    sent = 0
    for user, league, affected in recipients:
        subject = (
            f'☠ {person.name_it} era nella tua squadra!'
            if affected else f'☠ {person.name_it} è deceduto/a'
        )
        person_path = reverse('person_detail', args=[death.person_id])
        if league is not None:
            person_path += f'?league={league.slug}'
        person_path += '#decesso'

        context = {
            'user': user,
            'league': league,
            'person': person,
            'death': death,
            'affected': affected,
            'death_url': _abs_url(person_path),
            'profile_url': _abs_url(reverse('profile')),
            'site_url': _site_base_url(),
        }
        if _send(user.email, subject, context, 'email/death_notification'):
            sent += 1

    logger.info('Email decesso %s: %d inviate (su %d destinatari)',
                person.name_it, sent, len(recipients))
    return sent


def send_substitution_reminder_email(team_member: TeamMember, days_left: int) -> bool:
    if not _email_configured():
        return False
    from .notifications import wants

    user = team_member.team.manager
    if not user.email or not wants(user, Notification.KIND_SUBSTITUTION, 'email'):
        return False

    person = team_member.person
    league = team_member.team.league
    deadline = team_member.get_substitution_deadline()

    subject = f'⏳ Hai {days_left} giorn{"o" if days_left == 1 else "i"} per sostituire {person.name_it}'
    context = {
        'user': user,
        'team_member': team_member,
        'person': person,
        'team': team_member.team,
        'league': league,
        'days_left': days_left,
        'deadline': deadline,
        'team_url': _abs_url(reverse('team_edit', args=[team_member.team.pk])),
        'profile_url': _abs_url(reverse('profile')),
        'site_url': _site_base_url(),
    }
    return _send(user.email, subject, context, 'email/substitution_reminder')
