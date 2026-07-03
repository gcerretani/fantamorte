"""Test unitari del client Wikidata, senza alcuna richiesta di rete.

Coprono solo le funzioni pure/whitelist (`detect_age_bonus`,
`_parse_date_claim`, validazione QID di `get_entity`). Qualsiasi chiamata di
rete viene bloccata patchando `WikidataClient._get` così un eventuale bug
che la invocasse comunque farebbe fallire il test.
"""
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from game.models import BonusType
from .client import WikidataClient


class DetectAgeBonusTest(TestCase):
    """`WikidataClient.detect_age_bonus`: whitelist di eval sulla formula età."""

    def setUp(self):
        self.client = WikidataClient()

    def _bonus(self, detection_method=BonusType.DETECTION_AGE, age_formula='age < 60'):
        # Istanza in memoria: non serve salvarla, la funzione legge solo gli attributi.
        return BonusType(
            name='Test', points=10,
            detection_method=detection_method, age_formula=age_formula,
        )

    def test_formula_vera_per_eta_sotto_soglia(self):
        bonus = self._bonus(age_formula='age < 60')
        self.assertTrue(self.client.detect_age_bonus(50, bonus))

    def test_formula_falsa_per_eta_sopra_soglia(self):
        bonus = self._bonus(age_formula='age < 60')
        self.assertFalse(self.client.detect_age_bonus(70, bonus))

    def test_formula_con_caratteri_fuori_whitelist_torna_false(self):
        bonus = self._bonus(age_formula='__import__("os").system("echo hack")')
        self.assertFalse(self.client.detect_age_bonus(50, bonus))

    def test_detection_method_diverso_da_age_torna_false(self):
        bonus = self._bonus(
            detection_method=BonusType.DETECTION_MANUAL, age_formula='age < 60',
        )
        self.assertFalse(self.client.detect_age_bonus(50, bonus))

    def test_formula_vuota_torna_false(self):
        bonus = self._bonus(age_formula='')
        self.assertFalse(self.client.detect_age_bonus(50, bonus))


class GetEntityInvalidQidTest(TestCase):
    """`get_entity` deve validare il QID PRIMA di fare qualunque richiesta HTTP."""

    def setUp(self):
        self.client = WikidataClient()

    def _assert_rejected_without_network(self, qid):
        with patch.object(
            self.client, '_get',
            side_effect=AssertionError('_get non deve essere chiamato per un QID non valido'),
        ):
            with self.assertRaises(ValueError):
                self.client.get_entity(qid)

    def test_qid_con_path_traversal_viene_rifiutato(self):
        self._assert_rejected_without_network('../evil')

    def test_qid_con_sql_injection_viene_rifiutato(self):
        self._assert_rejected_without_network('Q12; DROP TABLE')

    def test_qid_senza_prefisso_q_viene_rifiutato(self):
        self._assert_rejected_without_network('12345')

    def test_qid_vuoto_viene_rifiutato(self):
        self._assert_rejected_without_network('')


class ParseDateClaimTest(TestCase):
    """`_parse_date_claim` interpreta le date Wikidata (formato +YYYY-MM-DDT...)."""

    def setUp(self):
        self.client = WikidataClient()

    def test_claim_ben_formato_con_precisione_giorno(self):
        claims = [{
            'mainsnak': {
                'snaktype': 'value',
                'datavalue': {'value': {'time': '+1990-05-12T00:00:00Z', 'precision': 11}},
            },
        }]
        result_date, result_year = self.client._parse_date_claim(claims)
        self.assertEqual(result_date, date(1990, 5, 12))
        self.assertEqual(result_year, 1990)

    def test_claim_con_sola_precisione_anno(self):
        claims = [{
            'mainsnak': {
                'snaktype': 'value',
                'datavalue': {'value': {'time': '+1990-00-00T00:00:00Z', 'precision': 9}},
            },
        }]
        result_date, result_year = self.client._parse_date_claim(claims)
        self.assertIsNone(result_date)
        self.assertEqual(result_year, 1990)

    def test_claim_lista_vuota(self):
        result_date, result_year = self.client._parse_date_claim([])
        self.assertIsNone(result_date)
        self.assertIsNone(result_year)

    def test_claim_con_snaktype_novalue(self):
        claims = [{'mainsnak': {'snaktype': 'novalue'}}]
        result_date, result_year = self.client._parse_date_claim(claims)
        self.assertIsNone(result_date)
        self.assertIsNone(result_year)

    def test_claim_senza_campo_time(self):
        claims = [{
            'mainsnak': {
                'snaktype': 'value',
                'datavalue': {'value': {'time': '', 'precision': 11}},
            },
        }]
        result_date, result_year = self.client._parse_date_claim(claims)
        self.assertIsNone(result_date)
        self.assertIsNone(result_year)
