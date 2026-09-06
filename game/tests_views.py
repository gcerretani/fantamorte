"""Discovery entrypoint for the view integration suite.

The historical suite lives unchanged in ``_views_test_cases``.  Issue #62
changes the persistence contract of manual league bonus assignments, so only
the two tests that deliberately asserted the old global ``DeathBonus`` storage
are overridden here.  All other view tests are inherited unchanged.
"""
from ._views_test_cases import *  # noqa: F401,F403
from ._views_test_cases import ManualBonusAssignTest as _LegacyManualBonusAssignTest
from .league_bonus_decisions import LeagueDeathBonus
from .models import DeathBonus


class ManualBonusAssignTest(_LegacyManualBonusAssignTest):
    def test_admin_assegna_bonus_manuale(self):
        self.client.login(username='owner', password='x')
        self._assign()
        award = LeagueDeathBonus.objects.get(
            league=self.private_league,
            death=self.death,
            bonus_type=self.manual_bt,
        )
        self.assertEqual(award.created_by, self.owner)
        self.assertTrue(award.reason)
        # The security invariant: a league-admin action never creates a
        # globally shared DeathBonus row.
        self.assertFalse(DeathBonus.objects.filter(
            death=self.death, bonus_type=self.manual_bt,
        ).exists())

    def test_admin_rimuove_bonus_assegnato_a_mano(self):
        self.client.login(username='owner', password='x')
        self._assign()
        award = LeagueDeathBonus.objects.get(
            league=self.private_league,
            death=self.death,
            bonus_type=self.manual_bt,
        )
        self.client.post(
            reverse('league_deaths', args=['lega-privata']),
            {
                'action': 'remove_bonus',
                'death_bonus_id': str(award.pk),
                'bonus_scope': 'local',
            },
        )
        self.assertFalse(LeagueDeathBonus.objects.filter(pk=award.pk).exists())
