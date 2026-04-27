"""Signal handlers per profili utente, notifiche push sui decessi e
invalidazione della cache della classifica di lega."""
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import (
    Death, DeathBonus, League, LeagueBonus, Team, TeamMember, UserProfile,
)
from .scoring import invalidate_league_rankings


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

    import logging
    logger = logging.getLogger(__name__)

    if not getattr(settings, 'PUSH_NOTIFICATIONS_ASYNC', False):
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
