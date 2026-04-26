"""Invio email transazionali per decessi e reminder sostituzione.

Speculare a `push.py`: ogni funzione cattura le proprie eccezioni e ritorna
un esito booleano, così che il chiamante (signal o management command) non
si rompa per problemi di SMTP/configurazione.
"""
import logging

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse

from .models import Death, League, LeagueMembership, Team, TeamMember

logger = logging.getLogger(__name__)


def _email_configured() -> bool:
    """Vero se è configurato un mittente. Il backend SMTP non è obbligatorio:
    in dev/test si può usare console o locmem."""
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
    """Renderizza template txt+html e invia. Ritorna True su successo."""
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


def broadcast_death_email(death: Death) -> int:
    """Manda email a tutti gli utenti delle leghe in cui il decesso cade nel
    periodo di gioco e che hanno opt-in `email_notifications_enabled`.

    Subject differente se la persona è nella squadra dell'utente ("urgent").
    Ritorna il numero di email inviate con successo.
    """
    if not _email_configured():
        return 0

    person = death.person
    leagues = list(League.objects.filter(
        start_date__lte=death.death_date, end_date__gte=death.death_date,
    ))

    if leagues:
        memberships = LeagueMembership.objects.filter(
            league__in=leagues,
            user__profile__email_notifications_enabled=True,
        ).select_related('user', 'league')
        # (user_id, league) couples → un'email per coppia per dare il contesto della lega
        recipients = [(m.user, m.league) for m in memberships if m.user.email]
    else:
        # Fallback stagione legacy
        if death.season_id:
            teams = Team.objects.filter(
                season_id=death.season_id,
                manager__profile__email_notifications_enabled=True,
            ).select_related('manager')
            recipients = [(t.manager, None) for t in teams if t.manager.email]
        else:
            recipients = []

    if not recipients:
        return 0

    sent = 0
    for user, league in recipients:
        affected_qs = Team.objects.filter(
            manager=user, members__person=person, members__replaced_by=None,
        )
        if league is not None:
            affected_qs = affected_qs.filter(league=league)
        affected = affected_qs.exists()

        if affected:
            subject = f'☠ {person.name_it} era nella tua squadra!'
        else:
            subject = f'☠ {person.name_it} è deceduto/a'

        context = {
            'user': user,
            'league': league,
            'person': person,
            'death': death,
            'affected': affected,
            'death_url': _abs_url(reverse('death_detail', args=[death.pk])),
            'profile_url': _abs_url(reverse('profile')),
            'site_url': _site_base_url(),
        }
        if _send(user.email, subject, context, 'email/death_notification'):
            sent += 1

    logger.info('Email decesso %s: %d inviate (su %d destinatari)',
                person.name_it, sent, len(recipients))
    return sent


def send_substitution_reminder_email(team_member: TeamMember, days_left: int) -> bool:
    """Email all'utente con un reminder per la sostituzione di un membro morto."""
    if not _email_configured():
        return False
    user = team_member.team.manager
    profile = getattr(user, 'profile', None)
    if not user.email or not profile or not profile.email_notifications_enabled:
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
