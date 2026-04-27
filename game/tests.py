from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    BonusType, Death, DeathBonus, League, LeagueBonus,
    Season, SubstitutionReminder, Team, TeamMember, UserProfile, WikipediaPerson,
)
from .scoring import (
    compute_league_rankings,
    compute_team_death_details,
    compute_team_points_for_death,
    compute_team_total_score,
)

User = get_user_model()


class ScoringBaseTestCase(TestCase):
    """Fixture condivisa: lega, stagione, squadra e tre personaggi noti."""

    def setUp(self):
        self.owner = User.objects.create_user('manager', password='x')
        self.league = League.objects.create(
            name='Lega Test',
            slug='lega-test',
            owner=self.owner,
            start_date=date(1990, 1, 1),
            end_date=date(2030, 12, 31),
            registration_opens=date(1989, 12, 1),
            registration_closes=date(1990, 1, 31),
            base_points=50,
            captain_multiplier=2,
            jolly_multiplier=2,
            jolly_enabled=True,
        )
        self.season = Season.objects.create(
            year=2024, is_active=True,
            registration_opens=date(2023, 12, 1),
            registration_closes=date(2024, 1, 31),
        )
        self.team = Team.objects.create(
            name='Squadra Test',
            manager=self.owner,
            league=self.league,
        )

        # Tre personaggi noti con date e età di decesso reali
        self.berlusconi = WikipediaPerson.objects.create(
            wikidata_id='Q11860',
            name_it='Silvio Berlusconi',
            birth_date=date(1936, 9, 29),
            is_dead=True,
        )
        self.giovanni_paolo_ii = WikipediaPerson.objects.create(
            wikidata_id='Q989',
            name_it='Giovanni Paolo II',
            birth_date=date(1920, 5, 18),
            is_dead=True,
        )
        self.fellini = WikipediaPerson.objects.create(
            wikidata_id='Q46248',
            name_it='Federico Fellini',
            birth_date=date(1920, 1, 20),
            is_dead=True,
        )

        # Decessi confermati con età reale
        self.death_berlusconi = Death.objects.create(
            person=self.berlusconi,
            season=self.season,
            death_date=date(2023, 6, 12),  # giugno → usato per test jolly
            death_age=86,
            is_confirmed=True,
        )
        self.death_gp2 = Death.objects.create(
            person=self.giovanni_paolo_ii,
            season=self.season,
            death_date=date(2005, 4, 2),
            death_age=84,
            is_confirmed=True,
        )
        self.death_fellini = Death.objects.create(
            person=self.fellini,
            season=self.season,
            death_date=date(1993, 10, 31),
            death_age=73,
            is_confirmed=True,
        )


class PuntiBaseTest(ScoringBaseTestCase):

    def test_solo_punti_base_senza_bonus(self):
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 50)

    def test_persona_non_in_squadra_restituisce_zero(self):
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 0)

    def test_morte_non_confermata_non_contribuisce_al_totale(self):
        persona = WikipediaPerson.objects.create(
            wikidata_id='Q99999', name_it='Mario Rossi', is_dead=False,
        )
        Death.objects.create(
            person=persona, season=self.season,
            death_date=date(2024, 3, 15), death_age=70, is_confirmed=False,
        )
        TeamMember.objects.create(team=self.team, person=persona)
        self.assertEqual(compute_team_total_score(self.team), 0)


class BonusFissoTest(ScoringBaseTestCase):

    def setUp(self):
        super().setUp()
        self.bonus_politico = BonusType.objects.create(
            name='Politico', points=30,
            detection_method=BonusType.DETECTION_MANUAL,
        )

    def test_bonus_fisso_si_somma_ai_punti_base(self):
        # Berlusconi: 50 base + 30 bonus = 80
        DeathBonus.objects.create(
            death=self.death_berlusconi, bonus_type=self.bonus_politico, points_awarded=30,
        )
        LeagueBonus.objects.create(
            league=self.league, bonus_type=self.bonus_politico, is_active=True,
        )
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 80)

    def test_bonus_non_configurato_nella_lega_vale_zero(self):
        # DeathBonus presente ma nessun LeagueBonus → la lega non ha questo bonus
        DeathBonus.objects.create(
            death=self.death_fellini, bonus_type=self.bonus_politico, points_awarded=30,
        )
        TeamMember.objects.create(team=self.team, person=self.fellini, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 50)  # solo base

    def test_bonus_disattivato_nella_lega_vale_zero(self):
        DeathBonus.objects.create(
            death=self.death_berlusconi, bonus_type=self.bonus_politico, points_awarded=30,
        )
        LeagueBonus.objects.create(
            league=self.league, bonus_type=self.bonus_politico, is_active=False,
        )
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 50)  # bonus disattivato

    def test_override_punti_nella_lega(self):
        # LeagueBonus con override_points=100 sostituisce i 30 del BonusType
        DeathBonus.objects.create(
            death=self.death_berlusconi, bonus_type=self.bonus_politico, points_awarded=30,
        )
        LeagueBonus.objects.create(
            league=self.league, bonus_type=self.bonus_politico,
            is_active=True, override_points=100,
        )
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 150)  # 50 + 100


class BonusFormulaTest(ScoringBaseTestCase):

    def test_formula_age_giovanni_paolo_ii_84_anni(self):
        # Formula "2*(90-age)": 2*(90-84) = 12 → totale 50+12 = 62
        bonus = BonusType.objects.create(
            name='Longevità', points=0,
            points_formula='2*(90-age)',
            detection_method=BonusType.DETECTION_AGE,
        )
        DeathBonus.objects.create(death=self.death_gp2, bonus_type=bonus, points_awarded=12)
        LeagueBonus.objects.create(league=self.league, bonus_type=bonus, is_active=True)
        TeamMember.objects.create(
            team=self.team, person=self.giovanni_paolo_ii, is_captain=False,
        )
        pts = compute_team_points_for_death(self.team, self.death_gp2)
        self.assertEqual(pts, 62)

    def test_formula_age_berlusconi_86_anni(self):
        # Formula "3*(100-age)": 3*(100-86) = 42 → totale 50+42 = 92
        bonus = BonusType.objects.create(
            name='VIP', points=0,
            points_formula='3*(100-age)',
            detection_method=BonusType.DETECTION_AGE,
        )
        DeathBonus.objects.create(death=self.death_berlusconi, bonus_type=bonus, points_awarded=42)
        LeagueBonus.objects.create(league=self.league, bonus_type=bonus, is_active=True)
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 92)

    def test_formula_age_fellini_73_anni(self):
        # Formula "100-age": 100-73 = 27 → totale 50+27 = 77
        bonus = BonusType.objects.create(
            name='Giovane', points=0,
            points_formula='100-age',
            detection_method=BonusType.DETECTION_AGE,
        )
        DeathBonus.objects.create(death=self.death_fellini, bonus_type=bonus, points_awarded=27)
        LeagueBonus.objects.create(league=self.league, bonus_type=bonus, is_active=True)
        TeamMember.objects.create(team=self.team, person=self.fellini, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 77)

    def test_override_formula_nella_lega(self):
        # BonusType formula "age", LeagueBonus override_formula "age*2"
        # Giovanni Paolo II: age=84 → 84*2 = 168 → totale 50+168 = 218
        bonus = BonusType.objects.create(
            name='Età pura', points=0,
            points_formula='age',
            detection_method=BonusType.DETECTION_AGE,
        )
        DeathBonus.objects.create(death=self.death_gp2, bonus_type=bonus, points_awarded=84)
        LeagueBonus.objects.create(
            league=self.league, bonus_type=bonus,
            is_active=True, override_formula='age*2',
        )
        TeamMember.objects.create(
            team=self.team, person=self.giovanni_paolo_ii, is_captain=False,
        )
        pts = compute_team_points_for_death(self.team, self.death_gp2)
        self.assertEqual(pts, 218)


class MoltiplicatoriTest(ScoringBaseTestCase):

    def test_capitano_raddoppia_i_punti(self):
        # 50 * 2 = 100
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=True)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 100)

    def test_jolly_nel_mese_corretto_raddoppia(self):
        # Berlusconi muore a giugno (mese 6), jolly_month=6 → 50 * 2 = 100
        self.team.jolly_month = 6
        self.team.save()
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 100)

    def test_jolly_mese_diverso_non_si_applica(self):
        # Berlusconi muore a giugno, jolly_month=3 → nessun moltiplicatore
        self.team.jolly_month = 3
        self.team.save()
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 50)

    def test_capitano_e_jolly_si_moltiplicano_tra_loro(self):
        # 50 * 2 (capitano) * 2 (jolly) = 200
        self.team.jolly_month = 6
        self.team.save()
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=True)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 200)

    def test_jolly_disabilitato_nella_lega_non_applica_moltiplicatore(self):
        self.league.jolly_enabled = False
        self.league.save()
        self.team.jolly_month = 6
        self.team.save()
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=False)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 50)

    def test_moltiplicatore_capitano_personalizzato_3x(self):
        # captain_multiplier=3 → 50 * 3 = 150
        self.league.captain_multiplier = 3
        self.league.save()
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=True)
        pts = compute_team_points_for_death(self.team, self.death_berlusconi)
        self.assertEqual(pts, 150)

    def test_bonus_e_moltiplicatore_capitano(self):
        # Giovanni Paolo II capitano: (50 base + 20 bonus) * 2 = 140
        bonus = BonusType.objects.create(
            name='Religioso', points=20,
            detection_method=BonusType.DETECTION_MANUAL,
        )
        DeathBonus.objects.create(death=self.death_gp2, bonus_type=bonus, points_awarded=20)
        LeagueBonus.objects.create(league=self.league, bonus_type=bonus, is_active=True)
        TeamMember.objects.create(
            team=self.team, person=self.giovanni_paolo_ii, is_captain=True,
        )
        pts = compute_team_points_for_death(self.team, self.death_gp2)
        self.assertEqual(pts, 140)


class BonusOriginaleTest(ScoringBaseTestCase):

    def setUp(self):
        super().setUp()
        self.bonus_orig = BonusType.objects.create(
            name='Giocata Originale', points=25,
            detection_method=BonusType.DETECTION_ORIGINAL,
        )
        LeagueBonus.objects.create(
            league=self.league, bonus_type=self.bonus_orig, is_active=True,
        )

    def test_is_original_true_aggiunge_bonus(self):
        # Fellini è_original=True → 50 base + 25 originale = 75
        TeamMember.objects.create(
            team=self.team, person=self.fellini,
            is_captain=False, is_original=True,
        )
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 75)

    def test_is_original_false_non_aggiunge_bonus(self):
        TeamMember.objects.create(
            team=self.team, person=self.fellini,
            is_captain=False, is_original=False,
        )
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 50)

    def test_originale_con_capitano(self):
        # Fellini: is_original=True, is_captain=True → (50 + 25) * 2 = 150
        TeamMember.objects.create(
            team=self.team, person=self.fellini,
            is_captain=True, is_original=True,
        )
        pts = compute_team_points_for_death(self.team, self.death_fellini)
        self.assertEqual(pts, 150)


class FiltriDateLeagaTest(ScoringBaseTestCase):

    def test_morte_fuori_dal_range_della_lega_non_conta(self):
        # Lega 2020-2025: Berlusconi (2023) conta, Fellini (1993) no
        lega_moderna = League.objects.create(
            name='Lega Moderna',
            slug='lega-moderna',
            owner=self.owner,
            start_date=date(2020, 1, 1),
            end_date=date(2025, 12, 31),
            registration_opens=date(2019, 12, 1),
            registration_closes=date(2020, 1, 31),
            base_points=50,
        )
        squadra = Team.objects.create(
            name='Squadra Moderna', manager=self.owner, league=lega_moderna,
        )
        TeamMember.objects.create(team=squadra, person=self.berlusconi)
        TeamMember.objects.create(team=squadra, person=self.fellini)
        score = compute_team_total_score(squadra)
        self.assertEqual(score, 50)  # solo Berlusconi (2023)

    def test_morte_esattamente_a_start_date_conta(self):
        lega = League.objects.create(
            name='Lega Precisa',
            slug='lega-precisa',
            owner=self.owner,
            start_date=date(1993, 10, 31),  # stesso giorno di Fellini
            end_date=date(2030, 12, 31),
            registration_opens=date(1993, 1, 1),
            registration_closes=date(1993, 10, 30),
            base_points=50,
        )
        squadra = Team.objects.create(
            name='Squadra Precisa', manager=self.owner, league=lega,
        )
        TeamMember.objects.create(team=squadra, person=self.fellini)
        score = compute_team_total_score(squadra)
        self.assertEqual(score, 50)

    def test_morte_esattamente_a_end_date_conta(self):
        lega = League.objects.create(
            name='Lega Fine',
            slug='lega-fine',
            owner=self.owner,
            start_date=date(1990, 1, 1),
            end_date=date(1993, 10, 31),  # stesso giorno di Fellini
            registration_opens=date(1989, 1, 1),
            registration_closes=date(1990, 1, 31),
            base_points=50,
        )
        squadra = Team.objects.create(
            name='Squadra Fine', manager=self.owner, league=lega,
        )
        TeamMember.objects.create(team=squadra, person=self.fellini)
        score = compute_team_total_score(squadra)
        self.assertEqual(score, 50)


class TotaleEDeathDetailsTest(ScoringBaseTestCase):

    def test_totale_somma_piu_morti_in_squadra(self):
        # Berlusconi (50) + Fellini (50) = 100
        TeamMember.objects.create(team=self.team, person=self.berlusconi)
        TeamMember.objects.create(team=self.team, person=self.fellini)
        self.assertEqual(compute_team_total_score(self.team), 100)

    def test_totale_con_bonus_e_moltiplicatore_capitano(self):
        # GP2 capitano: (50 + 20) * 2 = 140; Fellini: 50 → totale 190
        bonus = BonusType.objects.create(
            name='Religioso', points=20,
            detection_method=BonusType.DETECTION_MANUAL,
        )
        DeathBonus.objects.create(death=self.death_gp2, bonus_type=bonus, points_awarded=20)
        LeagueBonus.objects.create(league=self.league, bonus_type=bonus, is_active=True)
        TeamMember.objects.create(
            team=self.team, person=self.giovanni_paolo_ii, is_captain=True,
        )
        TeamMember.objects.create(team=self.team, person=self.fellini, is_captain=False)
        self.assertEqual(compute_team_total_score(self.team), 190)

    def test_death_details_include_i_dati_corretti(self):
        TeamMember.objects.create(
            team=self.team, person=self.berlusconi, is_captain=True,
        )
        details = compute_team_death_details(self.team)
        self.assertEqual(len(details), 1)
        d = details[0]
        self.assertEqual(d['death'], self.death_berlusconi)
        self.assertTrue(d['is_captain'])
        self.assertEqual(d['points'], 100)  # 50 * 2
        self.assertEqual(d['multiplier'], 2)

    def test_death_details_vuoto_senza_morti_in_squadra(self):
        details = compute_team_death_details(self.team)
        self.assertEqual(details, [])


class RankingTest(ScoringBaseTestCase):

    def test_ranking_ordinato_per_punteggio_decrescente(self):
        manager2 = User.objects.create_user('manager2', password='x')
        squadra2 = Team.objects.create(
            name='Squadra 2', manager=manager2, league=self.league,
        )
        # Squadra1: Berlusconi capitano → 50 * 2 = 100
        # Squadra2: Fellini normale → 50
        TeamMember.objects.create(team=self.team, person=self.berlusconi, is_captain=True)
        TeamMember.objects.create(team=squadra2, person=self.fellini, is_captain=False)

        rankings = compute_league_rankings(self.league)
        self.assertEqual(len(rankings), 2)
        self.assertEqual(rankings[0]['team'], self.team)
        self.assertEqual(rankings[0]['score'], 100)
        self.assertEqual(rankings[1]['team'], squadra2)
        self.assertEqual(rankings[1]['score'], 50)

    def test_ranking_squadra_senza_morti_ha_score_zero(self):
        # Squadra senza persone → score 0
        rankings = compute_league_rankings(self.league)
        self.assertEqual(len(rankings), 1)
        self.assertEqual(rankings[0]['score'], 0)

    def test_ranking_piu_squadre_con_stesso_punteggio(self):
        manager2 = User.objects.create_user('manager2', password='x')
        squadra2 = Team.objects.create(
            name='Squadra 2', manager=manager2, league=self.league,
        )
        TeamMember.objects.create(team=self.team, person=self.berlusconi)
        TeamMember.objects.create(team=squadra2, person=self.fellini)
        rankings = compute_league_rankings(self.league)
        scores = [r['score'] for r in rankings]
        self.assertEqual(scores, sorted(scores, reverse=True))


class ThemePreferenceTest(TestCase):

    def test_default_e_auto_per_nuovo_utente(self):
        u = User.objects.create_user('newbie', password='x')
        # Il signal post_save su User crea il profilo
        self.assertEqual(u.profile.theme_preference, UserProfile.THEME_AUTO)

    def test_solo_choices_valide_vengono_accettate(self):
        u = User.objects.create_user('user1', password='x')
        for value, _ in UserProfile.THEME_CHOICES:
            u.profile.theme_preference = value
            u.profile.full_clean()  # non solleva
            u.profile.save()
        self.assertEqual(
            UserProfile.objects.get(pk=u.profile.pk).theme_preference, 'dark',
        )


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Fantamorte <noreply@example.com>',
)
class DeathEmailTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('alice', email='alice@example.com', password='x')
        self.bob = User.objects.create_user('bob', email='bob@example.com', password='x')
        # bob ha disattivato le email
        self.bob.profile.email_notifications_enabled = False
        self.bob.profile.save()

        self.league = League.objects.create(
            name='Lega Email', slug='lega-email', owner=self.owner,
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            registration_opens=date(2019, 12, 1), registration_closes=date(2020, 1, 31),
        )
        from .models import LeagueMembership
        LeagueMembership.objects.create(league=self.league, user=self.owner, role='owner')
        LeagueMembership.objects.create(league=self.league, user=self.bob, role='member')

        self.team_alice = Team.objects.create(name='A', manager=self.owner, league=self.league)
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q1', name_it='Tizio Caio',
            birth_date=date(1940, 1, 1), is_dead=False,
        )
        TeamMember.objects.create(team=self.team_alice, person=self.person)
        self.season = Season.objects.create(
            year=2025, is_active=True,
            registration_opens=date(2024, 12, 1),
            registration_closes=date(2025, 1, 31),
        )

    def test_email_inviata_solo_a_chi_ha_optin(self):
        from .email import broadcast_death_email
        self.person.is_dead = True
        self.person.save()
        death = Death.objects.create(
            person=self.person, season=self.season,
            death_date=date(2025, 6, 1),
            death_age=85, is_confirmed=True,
        )
        mail.outbox.clear()  # ignora le email triggered dal signal
        broadcast_death_email(death)
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertIn('alice@example.com', recipients)
        self.assertNotIn('bob@example.com', recipients)

    def test_subject_urgent_se_persona_in_squadra(self):
        from .email import broadcast_death_email
        self.person.is_dead = True
        self.person.save()
        death = Death.objects.create(
            person=self.person, season=self.season,
            death_date=date(2025, 6, 1),
            death_age=85, is_confirmed=True,
        )
        mail.outbox.clear()
        broadcast_death_email(death)
        alice_msgs = [m for m in mail.outbox if 'alice@example.com' in m.to]
        self.assertTrue(alice_msgs)
        self.assertIn('era nella tua squadra', alice_msgs[0].subject)

    def test_signal_invia_email_quando_death_confirmed(self):
        # Persona morta ma il Death viene creato non confermato e poi confermato:
        # il signal scatta solo nella transizione False → True.
        self.person.is_dead = True
        self.person.save()
        death = Death.objects.create(
            person=self.person, season=self.season,
            death_date=date(2025, 6, 1),
            death_age=85, is_confirmed=False,
        )
        mail.outbox.clear()
        death.is_confirmed = True
        death.save()
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertIn('alice@example.com', recipients)

    @override_settings(DEFAULT_FROM_EMAIL='')
    def test_no_crash_se_email_non_configurato(self):
        from .email import broadcast_death_email
        self.person.is_dead = True
        self.person.save()
        death = Death.objects.create(
            person=self.person, season=self.season,
            death_date=date(2025, 6, 1),
            death_age=85, is_confirmed=True,
        )
        mail.outbox.clear()
        sent = broadcast_death_email(death)
        self.assertEqual(sent, 0)
        self.assertEqual(mail.outbox, [])


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Fantamorte <noreply@example.com>',
)
class SubstitutionReminderTest(TestCase):

    def setUp(self):
        self.owner = User.objects.create_user('alice', email='alice@example.com', password='x')
        today = timezone.now().date()
        self.league = League.objects.create(
            name='Lega Reminder', slug='lega-reminder', owner=self.owner,
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=300),
            registration_opens=today - timedelta(days=60),
            registration_closes=today - timedelta(days=20),
            substitution_deadline_days=7,
        )
        self.team = Team.objects.create(name='A', manager=self.owner, league=self.league)
        self.person = WikipediaPerson.objects.create(
            wikidata_id='Q42', name_it='Tizio Morto',
            birth_date=date(1930, 1, 1), is_dead=True,
        )
        self.member = TeamMember.objects.create(team=self.team, person=self.person)
        self.season = Season.objects.create(
            year=today.year, is_active=True,
            registration_opens=today - timedelta(days=60),
            registration_closes=today - timedelta(days=20),
        )
        # Decesso con confirmed_at impostato a "5 giorni fa" → deadline a +2 giorni
        # da oggi, quindi rientra nella soglia T-3 ma non in T-1.
        confirmed = timezone.now() - timedelta(days=5)
        self.death = Death.objects.create(
            person=self.person, season=self.season,
            death_date=confirmed.date(),
            death_age=95, is_confirmed=True, confirmed_at=confirmed,
        )

    def _run_command(self, **kwargs):
        from django.core.management import call_command
        call_command('send_substitution_reminders', **kwargs)

    def test_invia_solo_t_minus_3_quando_mancano_due_giorni(self):
        mail.outbox.clear()
        self._run_command()
        # Deve essere stato creato un solo marker, per la soglia 3
        markers = list(SubstitutionReminder.objects.filter(team_member=self.member))
        self.assertEqual([m.threshold_days for m in markers], [3])
        # E un'email è partita
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('sostituire', mail.outbox[0].subject.lower())

    def test_idempotente_non_duplica_su_seconda_esecuzione(self):
        self._run_command()
        mail.outbox.clear()
        self._run_command()
        # Nessuna nuova email; il marker esistente blocca il reinvio
        self.assertEqual(mail.outbox, [])
        self.assertEqual(
            SubstitutionReminder.objects.filter(team_member=self.member).count(), 1,
        )

    def test_invia_t_minus_1_quando_la_deadline_si_avvicina(self):
        # Sposta confirmed_at a "6 giorni e 12 ore fa" → deadline tra 12h
        self.death.confirmed_at = timezone.now() - timedelta(days=6, hours=12)
        self.death.save()
        SubstitutionReminder.objects.filter(team_member=self.member).delete()
        self._run_command()
        markers = list(SubstitutionReminder.objects.filter(team_member=self.member))
        self.assertEqual([m.threshold_days for m in markers], [1])

    def test_dry_run_non_scrive_marker(self):
        self._run_command(dry_run=True)
        self.assertFalse(SubstitutionReminder.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_skippa_se_membro_gia_sostituito(self):
        replacement_person = WikipediaPerson.objects.create(
            wikidata_id='Q43', name_it='Sostituto',
            birth_date=date(1950, 1, 1), is_dead=False,
        )
        replacement = TeamMember.objects.create(team=self.team, person=replacement_person)
        self.member.replaced_by = replacement
        self.member.save()
        self._run_command()
        self.assertFalse(SubstitutionReminder.objects.exists())


class RankingsCacheTest(ScoringBaseTestCase):

    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        TeamMember.objects.create(team=self.team, person=self.berlusconi)

    def test_seconda_chiamata_legge_da_cache(self):
        from .scoring import _RANKINGS_DATA_KEY, _rankings_version
        from django.core.cache import cache

        first = compute_league_rankings(self.league)
        version = _rankings_version(self.league.id)
        key = _RANKINGS_DATA_KEY.format(league_id=self.league.id, version=version)
        self.assertIsNotNone(cache.get(key))
        # Manomettiamo la cache: la seconda chiamata deve restituirla così com'è
        cache.set(key, [{'fake': True}], 300)
        second = compute_league_rankings(self.league)
        self.assertEqual(second, [{'fake': True}])
        self.assertNotEqual(first, second)

    def test_invalidazione_su_nuovo_decesso_confermato(self):
        first = compute_league_rankings(self.league)
        first_score = first[0]['score']
        # Aggiungo Fellini in squadra e creo un nuovo decesso confermato
        TeamMember.objects.create(team=self.team, person=self.giovanni_paolo_ii)
        # Il decesso esiste già da setUp (ScoringBaseTestCase), ma il signal su
        # TeamMember bumpa la versione → la prossima call deve ricalcolare.
        second = compute_league_rankings(self.league)
        self.assertGreater(second[0]['score'], first_score)


class SitemapStaticTest(TestCase):
    """Sanity check: la cache locmem default funziona e non genera errori."""

    def test_cache_get_set_default(self):
        from django.core.cache import cache
        cache.set('fm-test', 42, 30)
        self.assertEqual(cache.get('fm-test'), 42)
