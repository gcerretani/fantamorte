"""Preparazione dati per i grafici a barre delle pagine Statistiche.

Nessuna dipendenza esterna: i grafici sono barre CSS (vedi `_bar_chart.html` +
sezione ⑧ `fantamorte.css`), coerenti col design system self-hosted del
progetto. Questo modulo si limita a calcolare la percentuale di riempimento
di ogni barra relativa al massimo della serie — nessun accesso a modelli o
query, così resta riusabile sia dalla lega singola che dalle statistiche
globali.
"""


def bar_chart(rows):
    """Aggiunge `pct` (0-100, riempimento relativo al massimo di `rows`) a
    ogni dict di `rows` (mutati in place). Ogni riga richiede almeno `value`
    (numero); `label` e `display` sono a cura del chiamante/template.

    Ritorna `rows` per comodità (``return charts.bar_chart(rows)``).
    """
    max_value = max((r['value'] for r in rows), default=0) or 1
    for r in rows:
        r['pct'] = round(100 * r['value'] / max_value, 1)
    return rows
