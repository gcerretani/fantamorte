"""Test unitari del client Wikidata, senza alcuna richiesta di rete.

Coprono solo le funzioni pure/whitelist (`detect_age_bonus`,
`_parse_date_claim`, validazione QID di `get_entity`). Qualsiasi chiamata di
rete viene bloccata patchando `WikidataClient._get` così un eventuale bug
che la invocasse comunque farebbe fallire il test.
"""
from datetime import date
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase

from game.models import BonusType
from . import client as client_module
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


class HierarchicalBonusCheckTest(TestCase):
    """_check_wikidata_bonus: match esatto in cache, poi gerarchico via SPARQL."""

    class FakeBonus:
        def __init__(self, prop, value):
            self.wikidata_property = prop
            self.wikidata_value = value

    def setUp(self):
        cache.clear()  # gli ASK gerarchici sono cachati tra i test

    def _claims(self, prop, qid):
        return {prop: [{'mainsnak': {'snaktype': 'value', 'datavalue': {
            'type': 'wikibase-entityid', 'value': {'id': qid}}}}]}

    def test_match_esatto_senza_rete(self):
        client = WikidataClient()
        with patch.object(WikidataClient, '_sparql', side_effect=AssertionError('rete non attesa')):
            ok = client._check_wikidata_bonus(
                'Q937', self.FakeBonus('P166', 'Q7191'), self._claims('P166', 'Q7191'))
        self.assertTrue(ok)

    def test_match_gerarchico_via_sparql(self):
        # Einstein: P166=Q38104 (Nobel per la fisica), bonus generico Q7191.
        client = WikidataClient()
        with patch.object(WikidataClient, '_sparql', return_value={'boolean': True}) as mock_sparql:
            ok = client._check_wikidata_bonus(
                'Q937', self.FakeBonus('P166', 'Q7191'), self._claims('P166', 'Q38104'))
        self.assertTrue(ok)
        query = mock_sparql.call_args[0][0]
        self.assertIn('wd:Q937', query)
        self.assertIn('wdt:P166', query)
        self.assertIn('wd:Q7191', query)

    def test_gerarchia_negativa(self):
        client = WikidataClient()
        with patch.object(WikidataClient, '_sparql', return_value={'boolean': False}):
            ok = client._check_wikidata_bonus(
                'Q937', self.FakeBonus('P166', 'Q99999'), self._claims('P166', 'Q38104'))
        self.assertFalse(ok)

    def test_proprieta_o_valore_malformati_rifiutati(self):
        client = WikidataClient()
        claims = self._claims('P166', 'Q38104')
        with patch.object(WikidataClient, '_sparql', side_effect=AssertionError('rete non attesa')):
            self.assertFalse(client._check_wikidata_bonus(
                'Q937', self.FakeBonus('P166} UNION {evil', 'Q7191'), claims))
            self.assertFalse(client._check_wikidata_bonus(
                'Q937', self.FakeBonus('P166', 'Q7191 . ?x ?y ?z'), claims))

    def test_solo_presenza_proprieta(self):
        client = WikidataClient()
        ok = WikidataClient()._check_wikidata_bonus(
            'Q937', self.FakeBonus('P39', ''), self._claims('P39', 'Q11696'))
        self.assertTrue(ok)

    def test_ask_gerarchico_cachato(self):
        """La seconda chiamata con gli stessi argomenti non tocca SPARQL."""
        client = WikidataClient()
        bonus = self.FakeBonus('P166', 'Q7191')
        claims = self._claims('P166', 'Q38104')
        with patch.object(WikidataClient, '_sparql', return_value={'boolean': True}) as mock_sparql:
            self.assertTrue(client._check_wikidata_bonus('Q937', bonus, claims))
        self.assertEqual(mock_sparql.call_count, 1)
        with patch.object(WikidataClient, '_sparql', side_effect=AssertionError('rete non attesa')):
            self.assertTrue(client._check_wikidata_bonus('Q937', bonus, claims))

    def test_ask_fallito_non_viene_cachato(self):
        """Un errore SPARQL torna False ma non avvelena la cache."""
        client = WikidataClient()
        bonus = self.FakeBonus('P166', 'Q7191')
        claims = self._claims('P166', 'Q38104')
        with patch.object(WikidataClient, '_sparql', side_effect=RuntimeError('timeout')):
            self.assertFalse(client._check_wikidata_bonus('Q937', bonus, claims))
        with patch.object(WikidataClient, '_sparql', return_value={'boolean': True}):
            self.assertTrue(client._check_wikidata_bonus('Q937', bonus, claims))


class ThrottleTest(TestCase):
    """_throttle: mai prima della prima richiesta, sì tra richieste ravvicinate."""

    def setUp(self):
        client_module._reset_session_for_tests()

    def tearDown(self):
        client_module._reset_session_for_tests()

    def test_prima_richiesta_senza_attesa(self):
        with patch.object(client_module.time, 'sleep') as mock_sleep:
            client_module._throttle(0.5)
        mock_sleep.assert_not_called()

    def test_seconda_richiesta_ravvicinata_attende(self):
        client_module._throttle(0.5)
        with patch.object(client_module.time, 'sleep') as mock_sleep:
            client_module._throttle(0.5)
        mock_sleep.assert_called_once()
        self.assertGreater(mock_sleep.call_args[0][0], 0)

    def test_delay_zero_non_attende_mai(self):
        client_module._throttle(0.5)
        with patch.object(client_module.time, 'sleep') as mock_sleep:
            client_module._throttle(0)
        mock_sleep.assert_not_called()


class SharedSessionAndTimeoutTest(TestCase):
    """Session condivisa a livello di modulo e timeout sovrascrivibili."""

    def setUp(self):
        client_module._reset_session_for_tests()

    def tearDown(self):
        client_module._reset_session_for_tests()

    def test_due_client_condividono_la_session(self):
        self.assertIs(WikidataClient().session, WikidataClient().session)

    def test_timeout_override_arriva_alla_session(self):
        client = WikidataClient()
        client.delay = 0
        client.timeout = 5
        fake_resp = MagicMock()
        fake_resp.json.return_value = {}
        with patch.object(client.session, 'get', return_value=fake_resp) as mock_get:
            client._get('https://example.org/api')
        self.assertEqual(mock_get.call_args.kwargs['timeout'], 5)

    def test_sparql_timeout_override(self):
        client = WikidataClient()
        client.delay = 0
        client.sparql_timeout = 8
        fake_resp = MagicMock()
        fake_resp.json.return_value = {}
        with patch.object(client.session, 'get', return_value=fake_resp) as mock_get:
            client._sparql('ASK { }')
        self.assertEqual(mock_get.call_args.kwargs['timeout'], 8)


class MulLabelFallbackTest(TestCase):
    """Fallback label it → mul → en → QID.

    La lingua speciale `mul` è la label "di default per tutte le lingue" di
    Wikidata: alcune entità (es. Q22686) hanno SOLO quella, e senza fallback
    l'app mostrerebbe il QID nudo.
    """

    def _payload(self, labels, descriptions=None):
        return {'entities': {'Q22686': {
            'labels': labels,
            'descriptions': descriptions or {},
            'claims': {},
            'sitelinks': {},
        }}}

    def _entity(self, labels, descriptions=None):
        client = WikidataClient()
        with patch.object(client, '_get', return_value=self._payload(labels, descriptions)):
            return client.get_entity('Q22686')

    def test_label_mul_usata_se_manca_it(self):
        entity = self._entity({'mul': {'value': 'Donald Trump'}})
        self.assertEqual(entity['name_it'], 'Donald Trump')

    def test_it_ha_precedenza_su_mul(self):
        entity = self._entity({
            'it': {'value': 'Nome Italiano'},
            'mul': {'value': 'Default Name'},
        })
        self.assertEqual(entity['name_it'], 'Nome Italiano')

    def test_mul_ha_precedenza_su_en(self):
        entity = self._entity({
            'mul': {'value': 'Default Name'},
            'en': {'value': 'English Name'},
        })
        self.assertEqual(entity['name_it'], 'Default Name')

    def test_qid_solo_come_ultima_spiaggia(self):
        entity = self._entity({})
        self.assertEqual(entity['name_it'], 'Q22686')

    def test_descrizione_con_fallback_mul(self):
        entity = self._entity(
            {'mul': {'value': 'Donald Trump'}},
            descriptions={'mul': {'value': 'presidente USA'}},
        )
        self.assertEqual(entity['description_it'], 'presidente USA')

    def test_fetch_labels_fallback_mul(self):
        client = WikidataClient()
        payload = {'entities': {
            'Q1': {'labels': {'mul': {'value': 'Solo Mul'}}},
            'Q2': {'labels': {'it': {'value': 'Anche It'}, 'mul': {'value': 'ignorata'}}},
        }}
        with patch.object(client, '_get', return_value=payload) as mock_get:
            labels = client._fetch_labels(['Q1', 'Q2'])
        self.assertEqual(labels, {'Q1': 'Solo Mul', 'Q2': 'Anche It'})
        # `mul` va anche richiesta esplicitamente a wbgetentities.
        self.assertIn('mul', mock_get.call_args[0][1]['languages'].split('|'))


class CombinedLabelsFetchTest(TestCase):
    """get_entity risolve occupazione e cittadinanza con UNA sola wbgetentities."""

    def _entity_payload(self):
        def entity_claim(prop_qid):
            return [{'mainsnak': {'snaktype': 'value', 'datavalue': {
                'type': 'wikibase-entityid', 'value': {'id': prop_qid}}}}]
        return {'entities': {'Q937': {
            'labels': {'it': {'value': 'Albert Einstein'}},
            'descriptions': {},
            'claims': {'P106': entity_claim('Q169470'), 'P27': entity_claim('Q39')},
            'sitelinks': {},
        }}}

    def _labels_payload(self):
        return {'entities': {
            'Q169470': {'labels': {'it': {'value': 'fisico'}}},
            'Q39': {'labels': {'it': {'value': 'Svizzera'}}},
        }}

    def test_una_sola_chiamata_labels(self):
        client = WikidataClient()
        responses = [self._entity_payload(), self._labels_payload()]
        with patch.object(client, '_get', side_effect=responses) as mock_get:
            entity = client.get_entity('Q937')
        # 1 chiamata EntityData + 1 sola wbgetentities per P106+P27 insieme.
        self.assertEqual(mock_get.call_count, 2)
        ids = mock_get.call_args_list[1][0][1]['ids']
        self.assertEqual(set(ids.split('|')), {'Q169470', 'Q39'})
        self.assertEqual(entity['occupation'], 'fisico')
        self.assertEqual(entity['nationality'], 'Svizzera')

    def test_errore_labels_azzera_entrambi(self):
        client = WikidataClient()
        responses = [self._entity_payload(), RuntimeError('timeout')]
        with patch.object(client, '_get', side_effect=responses):
            entity = client.get_entity('Q937')
        self.assertIsNone(entity['occupation'])
        self.assertIsNone(entity['nationality'])
