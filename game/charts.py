"""Preparazione dati per i grafici: barre delle Statistiche e andamento punti.

Nessuna dipendenza esterna: i grafici sono barre CSS (vedi `_bar_chart.html` +
sezione ⑧ `fantamorte.css`), coerenti col design system self-hosted del
progetto. Questo modulo si limita a calcolare la percentuale di riempimento
di ogni barra relativa al massimo della serie — nessun accesso a modelli o
query, così resta riusabile sia dalla lega singola che dalle statistiche
globali.
"""
from datetime import date


def bar_chart(rows):
    """Aggiunge `pct` (0-100, riempimento relativo al massimo di `rows`) a
    ogni dict di `rows` (mutati in place). Ogni riga richiede almeno `value`
    (numero); `label` e `display` sono a cura del chiamante/template.

    Ritorna `rows` per comodità (``return charts.bar_chart(rows)``).
    """
    max_value = max((r['value'] for r in rows), default=0) or 1
    for r in rows:
        # Stringa già formattata (non un float): il template la inietta in un
        # `style="width: {{ pct }}%"`. Con LANGUAGE_CODE='it-it' Django
        # localizza i float in {{ }} con la virgola come separatore
        # decimale ("100,0"), CSS non valido che i browser scartano in
        # silenzio — la barra risulta invisibile o piena a caso.
        r['pct'] = f'{100 * r["value"] / max_value:.1f}'
    return rows


# ---------------------------------------------------------------------------
# Andamento punti (linea a gradini) — #105
# ---------------------------------------------------------------------------

MONTHS_SHORT = ['gen', 'feb', 'mar', 'apr', 'mag', 'giu',
                'lug', 'ago', 'set', 'ott', 'nov', 'dic']
STEP_W, STEP_H = 600, 200          # coordinate del viewBox SVG
STEP_PAD_TOP = 8
SERIES_TONES = 5                   # colori distinti in fantamorte.css


def step_chart(series, start, end, today):
    """Punti cumulati nel tempo come polilinee a gradini, in coordinate SVG.

    `series`: lista di dict `{label, events: [(date, points)], base,
    highlight}`; `base` è il punteggio di partenza (aggiustamenti manuali).
    L'asse x va da `start` al più piccolo tra `end` e `today`: una lega in
    corso si ferma a oggi invece di mostrare un futuro piatto.

    Ritorna None se non c'è niente da disegnare (nessun punto o periodo
    non ancora iniziato), altrimenti un dict pronto per `_step_chart.html`.
    Come `bar_chart`, i numeri finiti nel template sono stringhe già
    formattate: con LANGUAGE_CODE='it-it' un float diventerebbe "12,5".
    """
    stop = min(end, today)
    if stop <= start or not any(s['events'] for s in series):
        return None
    span = (stop - start).days or 1
    totals = [s.get('base', 0) + sum(p for _, p in s['events']) for s in series]
    peaks = [max(_running(s), default=0) for s in series]
    top = max(peaks + totals + [1])

    def x(d):
        d = min(max(d, start), stop)
        return STEP_W * (d - start).days / span

    def y(v):
        return STEP_PAD_TOP + (STEP_H - STEP_PAD_TOP) * (1 - max(v, 0) / top)

    lines = []
    for i, s in enumerate(series):
        value = s.get('base', 0)
        pts = [(0, y(value))]
        for d, p in sorted(s['events'], key=lambda e: e[0]):
            if d > stop:
                break
            pts.append((x(d), y(value)))
            value += p
            pts.append((x(d), y(value)))
        pts.append((STEP_W, y(value)))
        lines.append({
            'label': s['label'],
            'total': totals[i],
            'tone': i % SERIES_TONES,
            'highlight': s.get('highlight', False),
            'points': ' '.join(f'{px:.1f},{py:.1f}' for px, py in pts),
        })

    ticks = []
    month = date(start.year, start.month, 1)
    step = 1 if span <= 400 else 2
    while month <= stop:
        if month >= start:
            ticks.append({'x': f'{x(month):.1f}', 'pct': f'{100 * x(month) / STEP_W:.2f}',
                          'label': MONTHS_SHORT[month.month - 1]})
        for _ in range(step):
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return {
        'width': STEP_W, 'height': STEP_H, 'top': top, 'mid_y': f'{y(top / 2):.1f}',
        'top_y': f'{y(top):.1f}', 'lines': lines, 'ticks': ticks,
        'start': start, 'stop': stop,
    }


def _running(s):
    value = s.get('base', 0)
    for _, p in sorted(s['events'], key=lambda e: e[0]):
        value += p
        yield value
