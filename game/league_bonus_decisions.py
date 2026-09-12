"""League-scoped manual bonus assignments.

``DeathBonus`` remains the global fact store.  This model is deliberately
additive: existing global rows are never rewritten by the migration, so old
league scores remain unchanged.  New league-admin actions are stored here and
can never leak into another league.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .models import BonusType, Death, League


class LeagueDeathBonus(models.Model):
    league = models.ForeignKey(
        League, on_delete=models.CASCADE, related_name='death_bonus_decisions',
    )
    death = models.ForeignKey(
        Death, on_delete=models.CASCADE, related_name='league_bonus_decisions',
    )
    bonus_type = models.ForeignKey(
        BonusType, on_delete=models.CASCADE, related_name='league_death_decisions',
    )
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='created_league_death_bonus_decisions',
    )
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='updated_league_death_bonus_decisions',
    )
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'game'
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'death', 'bonus_type'],
                name='unique_league_death_bonus_decision',
            ),
        ]
        indexes = [
            # (league, death) is already a leftmost prefix of the unique
            # constraint above; keep only the complementary access path.
            models.Index(fields=['league', 'bonus_type'], name='game_ldb_league_bonus_idx'),
        ]
        verbose_name = 'Assegnazione bonus decesso di lega'
        verbose_name_plural = 'Assegnazioni bonus decesso di lega'

    def clean(self):
        super().clean()
        if self.bonus_type_id and self.league_id:
            scoped_league_id = self.bonus_type.league_id
            if scoped_league_id is not None and scoped_league_id != self.league_id:
                raise ValidationError(
                    {'bonus_type': 'Un bonus personalizzato può essere assegnato solo nella propria lega.'}
                )

    def save(self, *args, **kwargs):
        # Model.save() normally does not invoke clean()/full_clean(). Enforce
        # this cross-table scope invariant on every ordinary ORM write so that
        # scripts and future write paths cannot bypass the league boundary.
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bonus_type.name} per {self.death.person.name_it} [{self.league.name}]'
