DEATH_CHECK_QUERY = """
SELECT ?item WHERE {{
  VALUES ?item {{ {values} }}
  ?item wdt:P570 ?deathDate .
  FILTER(YEAR(?deathDate) = {year})
}}
"""

DEATH_DATE_QUERY = """
SELECT ?deathDate WHERE {{
  wd:{qid} wdt:P570 ?deathDate .
}}
LIMIT 1
"""

OSCAR_CHECK_QUERY = """
ASK {{
  wd:{qid} wdt:P166 ?award .
  ?award wdt:P31 wd:Q19020 .
}}
"""

PROPERTY_CHECK_QUERY = """
ASK {{
  wd:{qid} wdt:{prop} ?value .
}}
"""

PROPERTY_VALUE_CHECK_QUERY = """
ASK {{
  wd:{qid} wdt:{prop}/wdt:P31 wd:{value} .
}}
"""
