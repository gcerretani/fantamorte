import time
import requests
from datetime import date
from django.conf import settings
from . import sparql as sparql_templates


class WikidataClient:
    ENTITY_URL = 'https://www.wikidata.org/wiki/Special:EntityData/{}.json'
    SPARQL_URL = 'https://query.wikidata.org/sparql'
    IT_WIKI_API = 'https://it.wikipedia.org/w/api.php'
    IT_WIKI_SEARCH_API = 'https://it.wikipedia.org/w/api.php'

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

    def search_by_italian_name(self, name):
        data = self._get(self.IT_WIKI_SEARCH_API, {
            'action': 'query',
            'list': 'search',
            'srsearch': name,
            'srlimit': 10,
            'srprop': 'snippet',
            'format': 'json',
        })
        titles = [r['title'] for r in data.get('query', {}).get('search', [])]
        if not titles:
            return []

        prop_data = self._get(self.IT_WIKI_API, {
            'action': 'query',
            'titles': '|'.join(titles),
            'prop': 'pageprops',
            'ppprop': 'wikibase_item',
            'format': 'json',
        })

        results = []
        pages = prop_data.get('query', {}).get('pages', {})
        for page in pages.values():
            qid = page.get('pageprops', {}).get('wikibase_item')
            if qid:
                results.append({
                    'wikidata_id': qid,
                    'name_it': page.get('title', ''),
                    'description': '',
                    'wikipedia_url_it': f'https://it.wikipedia.org/wiki/{page.get("title", "").replace(" ", "_")}',
                })
        return results

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
        occupation = self._labels_for_entity_claims(claims.get('P106', []), limit=4)
        nationality = self._labels_for_entity_claims(claims.get('P27', []), limit=2)

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
        try:
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
        except Exception:
            return ''

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
