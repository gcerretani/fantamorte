from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .league_bonus_decisions import LeagueDeathBonus
from .scoring import invalidate_league_rankings


@receiver(post_save, sender=LeagueDeathBonus)
@receiver(post_delete, sender=LeagueDeathBonus)
def invalidate_rankings_on_local_bonus_change(sender, instance, **kwargs):
    invalidate_league_rankings(instance.league_id)
