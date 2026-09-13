from datetime import date, datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Death, League, Team, TeamMember, WikipediaPerson
from .scoring import compute_team_points_for_death

User = get_user_model()


class RosterEntryScoringEligibilityTest(TestCase):
    """Only in-season replacement entries are gated by their real entry date."""

    def setUp(self):
        self.user = User.objects.create_user('eligibility-manager', password='x')
        self.league = League.objects.create(
            name='Eligibility League', slug='eligibility-league', owner=self.user,
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            registration_opens=date(2025, 12, 1), registration_closes=date(2025, 12, 31),
            base_points=50,
        )
        self.team = Team.objects.create(
            name='Eligibility Team', manager=self.user, league=self.league,
        )

    def _person_and_death(self, qid, death_date):
        person = WikipediaPerson.objects.create(
            wikidata_id=qid, name_it=qid, is_dead=True,
        )
        death = Death.objects.create(
            person=person, death_date=death_date, death_age=70, is_confirmed=True,
        )
        return person, death

    def test_initial_roster_timestamp_does_not_rewrite_historical_scoring(self):
        person, death = self._person_and_death('Q992001', date(2026, 6, 1))
        member = TeamMember.objects.create(team=self.team, person=person)
        TeamMember.objects.filter(pk=member.pk).update(
            added_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc)
        )
        member.refresh_from_db()
        self.assertEqual(compute_team_points_for_death(self.team, death), 50)

    def test_replacement_added_after_death_gets_no_points(self):
        old_person, _ = self._person_and_death('Q992002', date(2026, 5, 1))
        old_member = TeamMember.objects.create(team=self.team, person=old_person)
        replacement_person, replacement_death = self._person_and_death(
            'Q992003', date(2026, 6, 1)
        )
        replacement = TeamMember.objects.create(team=self.team, person=replacement_person)
        TeamMember.objects.filter(pk=replacement.pk).update(
            added_at=datetime(2026, 9, 1, tzinfo=dt_timezone.utc)
        )
        replacement.refresh_from_db()
        old_member.replaced_by = replacement
        old_member.save(update_fields=['replaced_by'])
        self.assertEqual(compute_team_points_for_death(self.team, replacement_death), 0)

    def test_replacement_added_same_day_remains_eligible_with_date_only_death(self):
        old_person, _ = self._person_and_death('Q992004', date(2026, 5, 1))
        old_member = TeamMember.objects.create(team=self.team, person=old_person)
        replacement_person, replacement_death = self._person_and_death(
            'Q992005', date(2026, 6, 1)
        )
        replacement = TeamMember.objects.create(team=self.team, person=replacement_person)
        TeamMember.objects.filter(pk=replacement.pk).update(
            added_at=datetime(2026, 6, 1, 12, 0, tzinfo=dt_timezone.utc)
        )
        replacement.refresh_from_db()
        old_member.replaced_by = replacement
        old_member.save(update_fields=['replaced_by'])
        self.assertEqual(compute_team_points_for_death(self.team, replacement_death), 50)
