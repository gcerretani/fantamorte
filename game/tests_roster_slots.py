"""Rosa a slot: catena delle sostituzioni e sezione «Caduti» (#99)."""
from datetime import date, timedelta

from django.urls import reverse
from django.utils import timezone

from ._views_test_cases import ViewsBaseTestCase
from .models import Death, Team, TeamMember, WikipediaPerson


class RosterSlotsBase(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        self.team = self.private_team
        self.berlusconi = self.team.members.get()

    def _person(self, qid, name, dead_on=None):
        person = WikipediaPerson.objects.create(
            wikidata_id=qid, name_it=name, is_dead=dead_on is not None,
            death_date=dead_on,
        )
        if dead_on:
            Death.objects.create(
                person=person, death_date=dead_on, is_confirmed=True,
                confirmed_at=timezone.now(),
            )
        return person

    def _substitute(self, old, person, days_ago=0, **kwargs):
        new = TeamMember.objects.create(team=self.team, person=person, **kwargs)
        if days_ago:
            TeamMember.objects.filter(pk=new.pk).update(
                added_at=timezone.now() - timedelta(days=days_ago))
            new.refresh_from_db()
        old.replaced_by = new
        old.save()
        return new


class RosterSlotsModelTest(RosterSlotsBase):

    def test_senza_sostituzioni_ogni_membro_e_uno_slot(self):
        TeamMember.objects.create(team=self.team, person=self._person('Q2', 'Alda Merini'))
        slots = self.team.roster_slots()
        self.assertEqual(len(slots), 2)
        self.assertTrue(all(s.predecessors == [] and s.replaced is None for s in slots))

    def test_catena_di_due_sostituzioni_nello_stesso_slot(self):
        anna = self._person('Q3', 'Anna', dead_on=date(2024, 3, 1))
        bruno = self._person('Q4', 'Bruno')
        a = TeamMember.objects.create(team=self.team, person=anna)
        b = self._substitute(a, self._person('Q5', 'Bea', dead_on=date(2024, 6, 1)), days_ago=200)
        c = self._substitute(b, bruno, days_ago=10)

        slots = {s.current.pk: s for s in self.team.roster_slots()}
        self.assertEqual(set(slots), {self.berlusconi.pk, c.pk})
        slot = slots[c.pk]
        # Dal predecessore diretto al più vecchio.
        self.assertEqual([m.pk for m in slot.predecessors], [b.pk, a.pk])
        self.assertEqual(slot.replaced.pk, b.pk)
        # La FK in memoria punta al successore: la data della sostituzione.
        self.assertEqual(slot.predecessors[0].replaced_by.pk, c.pk)
        self.assertEqual(slot.predecessors[1].replaced_by.pk, b.pk)

    def test_capitano_in_testa(self):
        zeta = TeamMember.objects.create(
            team=self.team, person=self._person('Q6', 'Zeta'), is_captain=True)
        slots = self.team.roster_slots()
        self.assertEqual(slots[0].current.pk, zeta.pk)

    def test_lavora_sul_prefetch_senza_query_per_anello(self):
        a = TeamMember.objects.create(
            team=self.team, person=self._person('Q7', 'Uno', dead_on=date(2024, 1, 1)))
        b = self._substitute(a, self._person('Q8', 'Due', dead_on=date(2024, 2, 1)))
        self._substitute(b, self._person('Q9', 'Tre'))
        members = list(self.team.members.select_related('person'))
        with self.assertNumQueries(0):
            slots = self.team.roster_slots(members)
            for s in slots:
                for p in s.predecessors:
                    p.replaced_by.added_at  # noqa: B018


class TeamDetailSlotsViewTest(RosterSlotsBase):

    def setUp(self):
        super().setUp()
        self.client.login(username='member', password='x')

    def _get(self):
        return self.client.get(reverse('team_detail', args=[self.team.pk]))

    def test_sostituzione_visibile_in_rosa_e_tra_i_caduti(self):
        # Berlusconi muore in stagione e viene sostituito da Carla.
        person = self.berlusconi.person
        person.is_dead = True
        person.death_date = date(2023, 6, 12)
        person.save()
        Death.objects.create(person=person, death_date=person.death_date,
                             is_confirmed=True, confirmed_at=timezone.now())
        self._substitute(self.berlusconi, self._person('Q10', 'Carla Fracci'), days_ago=1)

        resp = self._get()
        self.assertContains(resp, '1 sostituzione')
        self.assertContains(resp, 'subentrato/a')
        self.assertContains(resp, 'al posto di')
        # Il morto sostituito resta visibile coi suoi punti e chi lo ha rimpiazzato.
        self.assertEqual(len(resp.context['fallen']), 1)
        fallen = resp.context['fallen'][0]
        self.assertEqual(fallen['person'], person)
        self.assertEqual(fallen['successor'].person.name_it, 'Carla Fracci')
        self.assertIsNotNone(fallen['detail'])
        self.assertContains(resp, 'sostituito/a da')

    def test_squadra_senza_sostituzioni_invariata(self):
        resp = self._get()
        self.assertNotContains(resp, 'sostituzion')
        self.assertNotContains(resp, 'al posto di')
        self.assertContains(resp, 'Nessun caduto')

    def test_motivo_dei_caduti_senza_punti(self):
        TeamMember.objects.create(
            team=self.team, person=self._person('Q11', 'Pre Stagione', dead_on=date(2019, 5, 1)))
        unconfirmed = self._person('Q12', 'Non Confermato', dead_on=None)
        unconfirmed.is_dead = True
        unconfirmed.death_date = date(2024, 1, 1)
        unconfirmed.save()
        TeamMember.objects.create(team=self.team, person=unconfirmed)

        reasons = {f['person'].name_it: f['reason'] for f in self._get().context['fallen']}
        self.assertEqual(reasons['Pre Stagione'], "Prima dell'inizio della lega")
        self.assertEqual(reasons['Non Confermato'], 'Decesso in attesa di conferma')

    def test_subentrato_gia_morto_non_vale_e_lo_dice(self):
        dead = self._person('Q13', 'Morto Prima', dead_on=date(2024, 1, 1))
        self._substitute(self.berlusconi, dead)  # scelto oggi, morto nel 2024
        reasons = {f['person'].name_it: f['reason'] for f in self._get().context['fallen']}
        self.assertEqual(reasons['Morto Prima'], 'Già deceduto/a quando è subentrato/a')

    def test_rosa_segreta_non_rivela_le_sostituzioni(self):
        league = self.private_league
        league.secret_rosters_preseason = True
        league.start_date = date.today() + timedelta(days=30)
        league.end_date = date.today() + timedelta(days=400)
        league.registration_closes = league.start_date
        league.save()
        other = Team.objects.create(name='Altra', manager=self.owner, league=league)
        a = TeamMember.objects.create(
            team=other, person=self._person('Q14', 'Segreto', dead_on=date(2019, 1, 1)))
        new = TeamMember.objects.create(team=other, person=self._person('Q15', 'Nuovo'))
        a.replaced_by = new
        a.save()
        resp = self.client.get(reverse('team_detail', args=[other.pk]))
        self.assertTrue(resp.context['roster_hidden'])
        self.assertNotContains(resp, 'Segreto')
        self.assertNotContains(resp, 'sostituzion')

    def test_team_edit_mostra_la_storia_dello_slot(self):
        person = self.berlusconi.person
        person.is_dead = True
        person.death_date = date(2023, 6, 12)
        person.save()
        self._substitute(self.berlusconi, self._person('Q16', 'Dario Fo'))
        resp = self.client.get(reverse('team_edit', args=[self.team.pk]))
        self.assertContains(resp, 'al posto di')
        self.assertContains(resp, 'subentrato/a')
