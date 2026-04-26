from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    BonusType, Death, DeathBonus, League, LeagueBonus,
    Season, Team, TeamMember, WikipediaPerson,
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
