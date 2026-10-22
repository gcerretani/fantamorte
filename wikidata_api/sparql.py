"""Template SPARQL usate dal client Wikidata.

Solo le query effettivamente referenziate dal client appartengono qui.
"""

# «Di questi, a chi è comparsa una data di morte?». Nessun filtro sull'anno:
# i candidati sono già solo persone che il DB crede vive, quindi ogni riga di
# risposta è un decesso nuovo. Un tempo si filtrava per anno della lega, il che
# costava una query per anno e creava due punti ciechi: i decessi dell'anno
# precedente all'inizio (pre-stagione) e quelli delle leghe non ancora
# iniziate, i cui anni sono nel futuro. Il periodo di gioco resta dove va
# filtrato davvero, cioè nello scoring.
# DISTINCT perché una persona può avere più claim P570 (precisioni diverse).
DEATH_CHECK_QUERY = """
SELECT DISTINCT ?item WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P570 ?deathDate .
}}
"""

# Match gerarchico: il valore del claim può essere uno dei target stessi (path
# a lunghezza zero) oppure una loro istanza/sottoclasse/parte, in qualunque
# combinazione. Es. Einstein ha P166=Q38104 (Nobel per la fisica), che è
# "parte di" Q7191 (Premio Nobel): deve far scattare il bonus generico.
# `{targets}` è una lista di uno o più `wd:Q...` (bonus con più QID accettati,
# es. Q7191,Q47170 per includere il Nobel per l'Economia).
PROPERTY_VALUE_CHECK_QUERY = """
ASK {{
  wd:{qid} wdt:{prop} ?v .
  ?v (wdt:P31|wdt:P279|wdt:P361)* ?target .
  VALUES ?target {{ {targets} }}
}}
"""

# Oltre al filtro umano/lingua porta anche foto (P18) e nascita (P569): nei
# risultati della ricerca servono a distinguere gli omonimi, e stanno nella
# stessa query. SAMPLE + GROUP BY: una riga per persona anche con più foto.
HUMAN_SEARCH_QUERY = """
SELECT ?item (SAMPLE(?title) AS ?itwikiTitle) (SAMPLE(?img) AS ?image)
       (SAMPLE(?birth) AS ?birthDate) WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P31 wd:Q5 .
{wiki_filter}
  OPTIONAL {{
    ?itwikiPage schema:about ?item ;
               schema:isPartOf <https://it.wikipedia.org/> ;
               schema:name ?title .
  }}
  OPTIONAL {{ ?item wdt:P18 ?img . }}
  OPTIONAL {{ ?item wdt:P569 ?birth . }}
}}
GROUP BY ?item
"""

# «Di questi, chi ha una pagina in almeno una delle wiki indicate?». Usata
# dall'importazione di una squadra in una lega con `search_wikipedia_langs`:
# la stessa regola della ricerca (HUMAN_SEARCH_QUERY), applicata a persone già
# note. `{sites}` è una lista di `<https://xx.wikipedia.org/>`.
SITELINK_FILTER_QUERY = """
SELECT DISTINCT ?item WHERE {{
  VALUES ?item {{ {values} }}
  VALUES ?site {{ {sites} }}
  ?article schema:about ?item ;
           schema:isPartOf ?site .
}}
"""
