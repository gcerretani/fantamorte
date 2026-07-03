"""Template SPARQL usate dal client Wikidata.

Solo le query effettivamente referenziate dal client appartengono qui.
"""

DEATH_CHECK_QUERY = """
SELECT ?item WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P570 ?deathDate .
  FILTER(YEAR(?deathDate) = {year})
}}
"""

PROPERTY_VALUE_CHECK_QUERY = """
ASK {{
  wd:{qid} wdt:{prop}/wdt:P31 wd:{value} .
}}
"""

HUMAN_SEARCH_QUERY = """
SELECT ?item ?itwikiTitle WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P31 wd:Q5 .
{wiki_filter}
  OPTIONAL {{
    ?itwikiPage schema:about ?item ;
               schema:isPartOf <https://it.wikipedia.org/> ;
               schema:name ?itwikiTitle .
  }}
}}
"""
