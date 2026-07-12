"""Signal handlers per profili utente, notifiche push sui decessi e
invalidazione della cache della classifica di lega."""
import logging

from django.contrib.auth.models import User
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import (
    Death, DeathBonus, League, LeagueBonus, LeagueMembership, Team, TeamMember,
    UserProfile,
)
from .scoring import invalidate_league_rankings

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def ensure_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(pre_save, sender=Death)
def _track_death_confirmation_state(sender, instance, **kwargs):
    """Memorizza lo stato precedente di is_confirmed per riconoscere la transizione."""
    if not instance.pk:
        instance._was_confirmed = False
        return
    try:
        prev = sender.objects.only('is_confirmed').get(pk=instance.pk)
        instance._was_confirmed = prev.is_confirmed
    except sender.DoesNotExist:
        instance._was_confirmed = False


@receiver(post_save, sender=Death)
def notify_on_death_confirmed(sender, instance, created, **kwargs):
    """Quando una Death passa a is_confirmed=True, invia push e email a chi ha optato."""
    was_confirmed = getattr(instance, '_was_confirmed', False)
    if not (instance.is_confirmed and not was_confirmed):
        return

    # Feed in-app (persist-first): sempre creato, a prescindere dai canali.
    try:
        from .notifications import create_death_notifications
        create_death_notifications(instance)
    except Exception:
        logger.exception('Errore creazione feed per Death %s', instance.pk)

    # Decesso pre-stagione: rimuovi il membro dalle rose (in composizione la
    # sostituzione non ha senso) e notifica il manager. Non interferisce con le
    # morti in stagione, che restano gestite dal flusso di sostituzione.
    try:
        from .notifications import remove_preseason_dead_members
        remove_preseason_dead_members(instance)
    except Exception:
        logger.exception('Errore rimozione membri pre-stagione per Death %s', instance.pk)

    # Push best-effort: gli errori non devono bloccare il salvataggio.
    try:
        from .push import broadcast_death_notification
        broadcast_death_notification(instance)
    except Exception:
        logger.exception('Errore invio push per Death %s', instance.pk)

    # Email: sempre sincrone (volume basso). Errori non devono bloccare il salvataggio.
    try:
        from .email import broadcast_death_email
        broadcast_death_email(instance)
    except Exception:
        logger.exception('Errore invio email per Death %s', instance.pk)


@receiver(post_save, sender=LeagueMembership)
def notify_on_league_joined(sender, instance, created, **kwargs):
    """Nuovo iscritto a una lega → notifica l'owner (feed in-app)."""
    if not created:
        return
    try:
        from .notifications import notify_league_joined
        notify_league_joined(instance)
    except Exception:
        logger.exception('Errore notifica iscrizione lega %s', instance.pk)


@receiver(pre_save, sender=Team)
def _track_team_lock_state(sender, instance, **kwargs):
    """Memorizza lo stato precedente di is_locked per riconoscere la transizione."""
    if not instance.pk:
        instance._was_locked = False
        return
    try:
        prev = sender.objects.only('is_locked').get(pk=instance.pk)
        instance._was_locked = prev.is_locked
    except sender.DoesNotExist:
        instance._was_locked = False


@receiver(post_save, sender=Team)
def notify_on_team_locked(sender, instance, created, **kwargs):
    """Quando una squadra passa a is_locked=True → notifica il manager (feed)."""
    was_locked = getattr(instance, '_was_locked', False)
    if not (instance.is_locked and not was_locked):
        return
    try:
        from .notifications import notify_team_locked
        notify_team_locked(instance)
    except Exception:
        logger.exception('Errore notifica blocco squadra %s', instance.pk)


def _invalidate_for_leagues_with_death(death):
    """Invalida la cache rankings di tutte le leghe che contengono questo death."""
    league_ids = League.objects.filter(
        start_date__lte=death.death_date, end_date__gte=death.death_date,
    ).values_list('id', flat=True)
    for lid in league_ids:
        invalidate_league_rankings(lid)


@receiver(post_save, sender=Death)
@receiver(post_delete, sender=Death)
def _invalidate_rankings_on_death_change(sender, instance, **kwargs):
    _invalidate_for_leagues_with_death(instance)


@receiver(post_save, sender=DeathBonus)
@receiver(post_delete, sender=DeathBonus)
def _invalidate_rankings_on_death_bonus_change(sender, instance, **kwargs):
    _invalidate_for_leagues_with_death(instance.death)


@receiver(post_save, sender=League)
def _invalidate_rankings_on_league_change(sender, instance, **kwargs):
    """Le regole della lega (base_points, moltiplicatori, date) entrano nel
    calcolo del punteggio: un salvataggio dal pannello admin deve invalidare
    subito la classifica, non attendere il TTL di 5 minuti."""
    invalidate_league_rankings(instance.pk)


@receiver(post_save, sender=LeagueBonus)
@receiver(post_delete, sender=LeagueBonus)
def _invalidate_rankings_on_league_bonus_change(sender, instance, **kwargs):
    invalidate_league_rankings(instance.league_id)


@receiver(post_save, sender=Team)
@receiver(post_delete, sender=Team)
def _invalidate_rankings_on_team_change(sender, instance, **kwargs):
    invalidate_league_rankings(instance.league_id)


@receiver(post_save, sender=TeamMember)
@receiver(post_delete, sender=TeamMember)
def _invalidate_rankings_on_team_member_change(sender, instance, **kwargs):
    league_id = getattr(instance.team, 'league_id', None)
    if league_id:
        invalidate_league_rankings(league_id)
