import logging
import time
import requests
from datetime import date
from django.conf import settings
from . import sparql as sparql_templates


logger = logging.getLogger(__name__)


class WikidataClient:
    ENTITY_URL = 'https://www.wikidata.org/wiki/Special:EntityData/{}.json'
    SPARQL_URL = 'https://query.wikidata.org/sparql'
    IT_WIKI_API = 'https://it.wikipedia.org/w/api.php'

    def __init__(self):
        ua = getattr(settings, 'WIKIDATA_USER_AGENT', 'Fantamorte/1.0')
        self.delay = getattr(settings, 'WIKIDATA_REQUEST_DELAY', 0.5)
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': ua, 'Accept': 'application/json'})

    def _get(self, url, params=None):
        time.sleep(self.delay)
        resp = self.session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _sparql(self, query):
        time.sleep(self.delay)
        resp = self.session.get(
            self.SPARQL_URL,
            params={'query': query, 'format': 'json'},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def search_by_italian_name(self, name, require_wikis=None):
        """Cerca persone su Wikidata per nome.

        Ritorna `(results, sparql_failed)`: una lista di dict e un flag che
        indica se il filtro SPARQL ha fallito (in tal caso i risultati non sono
        filtrati per umano/lingua).
        """
        # Step 1: search Wikidata entities by Italian label/alias
        data = self._get('https://www.wikidata.org/w/api.php', {
            'action': 'wbsearchentities',
            'search': name,
            'language': 'it',
            'type': 'item',
            'limit': 20,
            'format': 'json',
        })
        candidates = data.get('search', [])
        if not candidates:
            return [], False

        # Step 2: SPARQL to filter P31=Q5 (human) and get itwiki title in one shot.
        # This is much lighter than wbgetentities with props=claims.
        values = ' '.join(f'wd:{c["id"]}' for c in candidates)
        if require_wikis:
            union_clauses = ' UNION '.join(
                f'{{ ?wp schema:about ?item ; schema:isPartOf <https://{w[:-4]}.wikipedia.org/> . }}'
                for w in require_wikis
            )
            wiki_filter = f'  FILTER EXISTS {{ {union_clauses} }}'
        else:
            wiki_filter = ''
        query = sparql_templates.HUMAN_SEARCH_QUERY.format(values=values, wiki_filter=wiki_filter)
        try:
            sparql_data = self._sparql(query)
        except Exception:
            logger.warning(
                'SPARQL timeout/error during search for %r, returning unfiltered wbsearchentities results', name
            )
            fallback = [
                {
                    'wikidata_id': c['id'],
                    'name_it': c.get('label', c['id']),
                    'description': c.get('description', ''),
                    'wikipedia_url_it': '',
                }
                for c in candidates
            ]
            return fallback, True

        human_map = {}  # qid -> itwiki_title
        for binding in sparql_data.get('results', {}).get('bindings', []):
            qid = binding['item']['value'].split('/')[-1]
            human_map[qid] = binding.get('itwikiTitle', {}).get('value', '')

        results = []
        for candidate in candidates:
            qid = candidate['id']
            if qid not in human_map:
                continue
            wiki_title = human_map[qid]
            results.append({
                'wikidata_id': qid,
                'name_it': candidate.get('label', qid),
                'description': candidate.get('description', ''),
                'wikipedia_url_it': f'https://it.wikipedia.org/wiki/{wiki_title.replace(" ", "_")}' if wiki_title else '',
            })
        return results, False

    def get_entity(self, wikidata_id):
        data = self._get(self.ENTITY_URL.format(wikidata_id))
        entity = data.get('entities', {}).get(wikidata_id, {})
        labels = entity.get('labels', {})
        descriptions = entity.get('descriptions', {})
        claims = entity.get('claims', {})

        name_it = labels.get('it', {}).get('value') or labels.get('en', {}).get('value', wikidata_id)
        name_en = labels.get('en', {}).get('value', '')
        description_it = descriptions.get('it', {}).get('value') or descriptions.get('en', {}).get('value', '')

        birth_date, birth_year = self._parse_date_claim(claims.get('P569', []))
        death_date, death_year = self._parse_date_claim(claims.get('P570', []))

        sitelinks = entity.get('sitelinks', {})
        wiki_title = sitelinks.get('itwiki', {}).get('title', '')
        wikipedia_url = f'https://it.wikipedia.org/wiki/{wiki_title.replace(" ", "_")}' if wiki_title else ''

        image_url = self._build_commons_image_url(claims.get('P18', []))
        try:
            occupation = self._labels_for_entity_claims(claims.get('P106', []), limit=4)
        except Exception:
            # None = non determinabile (es. timeout label lookup); il diff lo ignora.
            logger.warning('Recupero occupation fallito per %s', wikidata_id, exc_info=True)
            occupation = None
        try:
            nationality = self._labels_for_entity_claims(claims.get('P27', []), limit=2)
        except Exception:
            logger.warning('Recupero nationality fallito per %s', wikidata_id, exc_info=True)
            nationality = None

        return {
            'name_it': name_it,
            'name_en': name_en,
            'description_it': description_it,
            'birth_date': birth_date,
            'birth_year': birth_year,
            'death_date': death_date,
            'death_year': death_year,
            'wikipedia_url_it': wikipedia_url,
            'wiki_title_it': wiki_title,
            'image_url': image_url,
            'occupation': occupation,
            'nationality': nationality,
            'claims_cache': {k: v for k, v in claims.items()},
        }

    def get_summary(self, wiki_title):
        """Restituisce l'estratto introduttivo della pagina Wikipedia italiana."""
        if not wiki_title:
            return ''
        data = self._get(self.IT_WIKI_API, {
            'action': 'query',
            'titles': wiki_title,
            'prop': 'extracts',
            'exintro': 1,
            'explaintext': 1,
            'redirects': 1,
            'format': 'json',
        })
        pages = data.get('query', {}).get('pages', {})
        for page in pages.values():
            extract = page.get('extract')
            if extract:
                return extract.strip()
        return ''

    def _build_commons_image_url(self, claim_list):
        if not claim_list:
            return ''
        snak = claim_list[0].get('mainsnak', {})
        if snak.get('snaktype') != 'value':
            return ''
        filename = snak.get('datavalue', {}).get('value')
        if not filename:
            return ''
        # Special:FilePath ridireziona all'immagine reale, gestendo redirect e dimensioni.
        from urllib.parse import quote
        return f'https://commons.wikimedia.org/wiki/Special:FilePath/{quote(filename)}?width=400'

    def _labels_for_entity_claims(self, claim_list, limit=4, lang='it'):
        """Risolve le label italiane delle entità referenziate da una lista di claims."""
        qids = []
        for claim in claim_list[:limit]:
            snak = claim.get('mainsnak', {})
            if snak.get('snaktype') != 'value':
                continue
            dv = snak.get('datavalue', {})
            if dv.get('type') != 'wikibase-entityid':
                continue
            qid = dv.get('value', {}).get('id')
            if qid:
                qids.append(qid)
        if not qids:
            return ''
        params = {
            'action': 'wbgetentities',
            'ids': '|'.join(qids),
            'props': 'labels',
            'languages': f'{lang}|en',
            'format': 'json',
        }
        data = self._get('https://www.wikidata.org/w/api.php', params)
        entities = data.get('entities', {})
        labels = []
        for qid in qids:
            ent = entities.get(qid, {})
            lbls = ent.get('labels', {})
            lbl = (lbls.get(lang) or lbls.get('en') or {}).get('value')
            if lbl:
                labels.append(lbl)
        return ', '.join(labels)

    def _parse_date_claim(self, claim_list):
        if not claim_list:
            return None, None
        snak = claim_list[0].get('mainsnak', {})
        if snak.get('snaktype') != 'value':
            return None, None
        dv = snak.get('datavalue', {}).get('value', {})
        time_str = dv.get('time', '')
        precision = dv.get('precision', 0)
        if not time_str:
            return None, None
        # format: +YYYY-MM-DDTHH:MM:SSZ or +YYYY-00-00T...
        time_str = time_str.lstrip('+').split('T')[0]
        parts = time_str.split('-')
        try:
            year = int(parts[0])
        except (ValueError, IndexError):
            return None, None
        if precision >= 11 and len(parts) == 3:
            try:
                month = int(parts[1]) or 1
                day = int(parts[2]) or 1
                return date(year, month, day), year
            except ValueError:
                pass
        return None, year

    def check_deaths_batch(self, wikidata_ids, year):
        if not wikidata_ids:
            return []
        values = ' '.join(f'wd:{qid}' for qid in wikidata_ids)
        query = sparql_templates.DEATH_CHECK_QUERY.format(values=values, year=year)
        data = self._sparql(query)
        dead = []
        for binding in data.get('results', {}).get('bindings', []):
            uri = binding.get('item', {}).get('value', '')
            qid = uri.split('/')[-1]
            if qid:
                dead.append(qid)
        return dead

    def detect_bonuses(self, wikidata_id, claims_cache, bonus_types):
        detected = []
        for bt in bonus_types:
            if bt.detection_method == 'wikidata':
                if self._check_wikidata_bonus(wikidata_id, bt, claims_cache):
                    detected.append(bt)
            elif bt.detection_method == 'age':
                pass  # age-based detection happens in check_deaths command with actual age
        return detected

    def _check_wikidata_bonus(self, wikidata_id, bonus_type, claims_cache):
        prop = bonus_type.wikidata_property
        value = bonus_type.wikidata_value
        if not prop:
            return False
        if prop not in claims_cache:
            return False
        if not value:
            return True
        # For Oscar-style: check if any award has P31 == value — use SPARQL
        # Special case: P166 with value Q19020 (Oscar) — use property path query
        if prop == 'P166' and value:
            query = sparql_templates.PROPERTY_VALUE_CHECK_QUERY.format(
                qid=wikidata_id, prop=prop, value=value
            )
            try:
                result = self._sparql(query)
                return result.get('boolean', False)
            except Exception:
                return False
        # For simple property presence with specific value
        claims = claims_cache.get(prop, [])
        for claim in claims:
            snak = claim.get('mainsnak', {})
            if snak.get('snaktype') == 'value':
                dv = snak.get('datavalue', {})
                if dv.get('type') == 'wikibase-entityid':
                    if dv.get('value', {}).get('id') == value:
                        return True
        return False

    def detect_age_bonus(self, age, bonus_type):
        if bonus_type.detection_method != 'age' or not bonus_type.age_formula:
            return False
        formula = bonus_type.age_formula.strip()
        # Safe eval: only allow simple comparisons with `age`
        allowed = set('age<>=!0123456789 ')
        if not all(c in allowed for c in formula):
            return False
        try:
            return bool(eval(formula, {'age': age, '__builtins__': {}}))  # noqa: S307
        except Exception:
            return False
