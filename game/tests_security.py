from datetime import date

from django.urls import reverse

from .models import BonusType, Death, DeathBonus, League, LeagueBonus, Team, TeamMember, WikipediaPerson
from .tests_views import ViewsBaseTestCase


class CrossLeagueBonusIsolationTest(ViewsBaseTestCase):
    """Regression tests for league-admin mutations of shared DeathBonus rows."""

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

    def test_shared_system_bonus_cannot_be_assigned_from_league_ui(self):
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'assign_bonus',
            'death_id': self.death.pk,
            'bonus_type_id': self.system_bonus.pk,
        })
        self.assertFalse(DeathBonus.objects.filter(
            death=self.death, bonus_type=self.system_bonus,
        ).exists())

    def test_shared_system_bonus_cannot_be_removed_from_league_ui(self):
        award = DeathBonus.objects.create(
            death=self.death, bonus_type=self.system_bonus,
            points_awarded=10, is_auto_detected=False,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus', 'death_bonus_id': award.pk,
        })
        self.assertTrue(DeathBonus.objects.filter(pk=award.pk).exists())

    def test_custom_bonus_remains_manageable_by_own_league(self):
        custom = BonusType.objects.create(
            name='Custom locale', league=self.private_league,
            points=7, detection_method=BonusType.DETECTION_MANUAL,
        )
        LeagueBonus.objects.create(league=self.private_league, bonus_type=custom)
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'assign_bonus',
            'death_id': self.death.pk,
            'bonus_type_id': custom.pk,
        })
        self.assertTrue(DeathBonus.objects.filter(death=self.death, bonus_type=custom).exists())

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
        award = DeathBonus.objects.create(
            death=other_death, bonus_type=custom, points_awarded=7,
        )
        self.client.login(username='owner', password='x')
        self.client.post(reverse('league_deaths', args=[self.private_league.slug]), {
            'action': 'remove_bonus', 'death_bonus_id': award.pk,
        })
        self.assertTrue(DeathBonus.objects.filter(pk=award.pk).exists())
