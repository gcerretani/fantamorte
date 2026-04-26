"""Signal handlers per profili utente e notifiche push sui decessi."""
from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Death, UserProfile


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
    """Quando una Death passa a is_confirmed=True, invia push a chi ha optato."""
    was_confirmed = getattr(instance, '_was_confirmed', False)
    if instance.is_confirmed and not was_confirmed:
        if getattr(settings, 'PUSH_NOTIFICATIONS_ASYNC', False):
            return  # delega a un worker esterno se configurato
        try:
            from .push import broadcast_death_notification
            broadcast_death_notification(instance)
        except Exception:
            # Non bloccare il salvataggio se la spedizione push fallisce
            import logging
            logging.getLogger(__name__).exception('Errore invio push per Death %s', instance.pk)
