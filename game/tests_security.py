from datetime import date

from django.urls import reverse

from . import scoring
from .league_bonus_decisions import LeagueDeathBonus
from .models import BonusType, Death, DeathBonus, League, LeagueBonus, Team, TeamMember, WikipediaPerson
from .tests_views import ViewsBaseTestCase


class CrossLeagueBonusIsolationTest(ViewsBaseTestCase):
    """League-admin awards are scoped in storage and in scoring."""

    def setUp(self):
        super().setUp()
        self.dead = WikipediaPerson.objects.create(
            wikidata_id='Q990001', name_it='Defunto isolamento', is_dead=True,
        )
        TeamMember.objects.create(team=self.private_team, person=self.dead)
        self.death = Death.objects.create(
            person=self.dead,
            death_date=date(2021, 5, 1),
            death_age=70,
            is_confirmed=True,
        )
        self.system_bonus = BonusType.objects.create(
            name='Sistema condiviso', points=10, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=self.system_bonus)

        self.other_league = League.objects.create(
            name='Altra lega', slug='altra-lega', owner=self.outsider,
            visibility=League.VISIBILITY_PRIVATE,
            start_date=date(2021, 1, 1), end_date=date(2021, 12, 31),
            registration_opens=date(2020, 12, 1), registration_closes=date(2020, 12, 31),
        )
        LeagueBonus.objects.create(league=self.other_league, bonus_type=self.system_bonus)
        self.other_team = Team.objects.create(
            name='Squadra altra lega', manager=self.outsider, league=self.other_league,
        )
        TeamMember.objects.create(team=self.other_team, person=self.dead)

    def _assign(self, bonus_type=None):
        self.client.login(username='owner', password='x')
        return self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'assign_bonus',
            'death_id': self.death.pk,
            'bonus_type_id': (bonus_type or self.system_bonus).pk,
        })

    def test_shared_system_bonus_is_stored_only_in_current_league(self):
        self._assign()
        self.assertFalse(DeathBonus.objects.filter(
            death=self.death, bonus_type=self.system_bonus,
        ).exists())
        award = LeagueDeathBonus.objects.get(
            league=self.private_league, death=self.death, bonus_type=self.system_bonus,
        )
        self.assertEqual(award.created_by, self.owner)
        self.assertTrue(award.reason)

    def test_local_assignment_changes_only_one_league_score(self):
        self._assign()
        self.assertEqual(scoring.compute_team_total_score(self.private_team), 60)
        self.assertEqual(scoring.compute_team_total_score(self.other_team), 50)

    def test_later_use_by_another_league_does_not_inherit_local_assignment(self):
        # The other league already has the same system BonusType enabled.  A
        # local decision remains local regardless of when another league uses it.
        self._assign()
        self.assertFalse(
            LeagueDeathBonus.objects.filter(league=self.other_league, death=self.death).exists()
        )
        self.assertEqual(scoring.compute_team_total_score(self.other_team), 50)

    def test_removing_local_assignment_does_not_touch_global_facts(self):
        global_bt = BonusType.objects.create(
            name='Fatto globale', points=4, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=global_bt)
        global_award = DeathBonus.objects.create(
            death=self.death, bonus_type=global_bt, points_awarded=4,
        )
        self._assign()
        local = LeagueDeathBonus.objects.get(
            league=self.private_league, death=self.death, bonus_type=self.system_bonus,
        )
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus',
            'death_bonus_id': local.pk,
            'bonus_scope': 'local',
        })
        self.assertFalse(LeagueDeathBonus.objects.filter(pk=local.pk).exists())
        self.assertTrue(DeathBonus.objects.filter(pk=global_award.pk).exists())

    def test_global_system_fact_cannot_be_removed_from_league_ui(self):
        award = DeathBonus.objects.create(
            death=self.death, bonus_type=self.system_bonus,
            points_awarded=10, is_auto_detected=False,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus',
            'death_bonus_id': award.pk,
            'bonus_scope': 'global',
        })
        self.assertTrue(DeathBonus.objects.filter(pk=award.pk).exists())

    def test_existing_global_bonus_keeps_legacy_scoring_for_all_leagues(self):
        DeathBonus.objects.create(
            death=self.death, bonus_type=self.system_bonus,
            points_awarded=10, is_auto_detected=False,
        )
        self.assertEqual(scoring.compute_team_total_score(self.private_team), 60)
        self.assertEqual(scoring.compute_team_total_score(self.other_team), 60)

    def test_custom_bonus_assignment_is_local_and_manageable(self):
        custom = BonusType.objects.create(
            name='Custom locale', league=self.private_league,
            points=7, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=custom)
        self._assign(custom)
        self.assertTrue(LeagueDeathBonus.objects.filter(
            league=self.private_league, death=self.death, bonus_type=custom,
        ).exists())
        self.assertFalse(DeathBonus.objects.filter(death=self.death, bonus_type=custom).exists())

    def test_legacy_custom_bonus_remains_removable(self):
        custom = BonusType.objects.create(
            name='Custom legacy', league=self.private_league,
            points=7, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=custom)
        legacy = DeathBonus.objects.create(
            death=self.death, bonus_type=custom, points_awarded=7,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus',
            'death_bonus_id': legacy.pk,
            'bonus_scope': 'global',
        })
        self.assertFalse(DeathBonus.objects.filter(pk=legacy.pk).exists())

    def test_bonus_for_death_not_played_in_league_cannot_be_removed(self):
        other_person = WikipediaPerson.objects.create(
            wikidata_id='Q990002', name_it='Defunto altra lega', is_dead=True,
        )
        other_death = Death.objects.create(
            person=other_person, death_date=date(2021, 5, 2), death_age=80, is_confirmed=True,
        )
        custom = BonusType.objects.create(
            name='Custom locale 2', league=self.private_league,
            points=7, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=custom)
        legacy = DeathBonus.objects.create(
            death=other_death, bonus_type=custom, points_awarded=7,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus',
            'death_bonus_id': legacy.pk,
            'bonus_scope': 'global',
        })
        self.assertTrue(DeathBonus.objects.filter(pk=legacy.pk).exists())
