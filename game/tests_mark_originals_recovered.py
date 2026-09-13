"""Regression tests recovered for the mark_originals management command."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import League, Team, TeamMember, WikipediaPerson

User = get_user_model()


class MarkOriginalsRecoveredTest(TestCase):
    def setUp(self):
        self.manager_a = User.objects.create_user('original-a', password='x')
        self.manager_b = User.objects.create_user('original-b', password='x')
        today = timezone.now().date()
        self.league = League.objects.create(
            name='Lega Originali', slug='lega-originali-recovered', owner=self.manager_a,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=355),
            registration_opens=today - timedelta(days=40),
            registration_closes=today - timedelta(days=11),
        )
        self.team_a = Team.objects.create(name='Team A', manager=self.manager_a, league=self.league)
        self.team_b = Team.objects.create(name='Team B', manager=self.manager_b, league=self.league)
        self.unique = WikipediaPerson.objects.create(wikidata_id='Q910001', name_it='Scelta Unica')
        self.shared = WikipediaPerson.objects.create(wikidata_id='Q910002', name_it='Scelta Condivisa')
        self.replacement = WikipediaPerson.objects.create(wikidata_id='Q910003', name_it='Sostituto')
        self.unique_member = TeamMember.objects.create(team=self.team_a, person=self.unique)
        self.shared_a = TeamMember.objects.create(team=self.team_a, person=self.shared)
        self.shared_b = TeamMember.objects.create(team=self.team_b, person=self.shared)

    def test_unique_initial_pick_is_marked_shared_pick_is_not(self):
        call_command('mark_originals', league=self.league.slug)
        self.unique_member.refresh_from_db()
        self.shared_a.refresh_from_db()
        self.shared_b.refresh_from_db()
        self.assertTrue(self.unique_member.is_original)
        self.assertFalse(self.shared_a.is_original)
        self.assertFalse(self.shared_b.is_original)

    def test_replacement_is_excluded_from_initial_roster_count(self):
        new_member = TeamMember.objects.create(team=self.team_b, person=self.replacement)
        self.shared_b.replaced_by = new_member
        self.shared_b.save(update_fields=['replaced_by'])

        call_command('mark_originals', league=self.league.slug)

        new_member.refresh_from_db()
        self.unique_member.refresh_from_db()
        self.assertFalse(new_member.is_original)
        self.assertTrue(self.unique_member.is_original)

    def test_reset_clears_stale_flags_before_recomputation(self):
        TeamMember.objects.filter(pk=self.shared_a.pk).update(is_original=True)
        call_command('mark_originals', league=self.league.slug, reset=True)
        self.shared_a.refresh_from_db()
        self.unique_member.refresh_from_db()
        self.assertFalse(self.shared_a.is_original)
        self.assertTrue(self.unique_member.is_original)

    def test_default_run_skips_finished_leagues(self):
        today = timezone.now().date()
        finished = League.objects.create(
            name='Lega Finita', slug='lega-finita-recovered', owner=self.manager_a,
            start_date=today - timedelta(days=400), end_date=today - timedelta(days=30),
            registration_opens=today - timedelta(days=430),
            registration_closes=today - timedelta(days=401),
        )
        finished_team = Team.objects.create(name='Vecchia', manager=self.manager_a, league=finished)
        finished_person = WikipediaPerson.objects.create(wikidata_id='Q910004', name_it='Vecchio unico')
        finished_member = TeamMember.objects.create(team=finished_team, person=finished_person)

        call_command('mark_originals')

        self.unique_member.refresh_from_db()
        finished_member.refresh_from_db()
        self.assertTrue(self.unique_member.is_original)
        self.assertFalse(finished_member.is_original)
