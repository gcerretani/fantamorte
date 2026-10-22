from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django import template

register = template.Library()

# Larghezze delle thumbnail Commons, in px reali. Sono i "bucket" standard
# di Wikimedia: una larghezza arbitraria verrebbe comunque arrotondata lato
# server, e restare sui bucket massimizza gli hit di cache sulle CDN.
# Ogni taglia CSS dell'avatar prende il bucket ≥ 2× il suo lato (retina).
AVATAR_SIZES = {
    'sm': 120,   # 2rem   (righe di lista compatte)
    'md': 120,   # 2.75rem (rose, timeline)
    'lg': 250,   # 4rem   (cronologia, card)
    'xl': 500,   # pagina persona
}


@register.filter
def commons_thumb(url, width):
    """Riscrive un URL `Special:FilePath` di Commons alla larghezza voluta.

    `person_sync` salva sempre `.../Special:FilePath/<file>?width=400`: il
    redirect di Commons accetta qualunque `width`, quindi cambiare il
    parametro basta per avere la thumbnail giusta. URL di altra forma
    (inseriti a mano dall'admin) passano invariati.
    """
    if not url or 'Special:FilePath' not in url:
        return url or ''
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != 'width']
    query.append(('width', str(int(width))))
    return urlunsplit(parts._replace(query=urlencode(query)))


def person_initials(name):
    words = [w.strip('()[]"\'.,') for w in (name or '').replace('-', ' ').split()]
    words = [w for w in words if w[:1].isalpha()]
    if not words:
        return '?'
    if len(words) == 1:
        return words[0][:1].upper()
    return (words[0][:1] + words[-1][:1]).upper()


@register.inclusion_tag('game/_person_avatar.html')
def person_avatar(person, size='md', dead=None):
    """Avatar persona: foto Commons alla taglia giusta o iniziali.

    `dead` forza lo stato (es. un membro sostituito mostrato nella storia
    dello slot); di default segue `person.is_dead`.
    """
    if size not in AVATAR_SIZES:
        size = 'md'
    width = AVATAR_SIZES[size]
    url = getattr(person, 'image_url', '') or ''
    return {
        'person': person,
        'size': size,
        'dead': person.is_dead if dead is None else dead,
        'src': commons_thumb(url, width) if url else '',
        'src2x': commons_thumb(url, width * 2) if url and size == 'lg' else '',
        'initials': person_initials(getattr(person, 'name_it', '')),
        # Tinta deterministica per le iniziali: stessa persona, stesso colore
        # su ogni pagina (6 varianti definite in fantamorte.css).
        'tint': (person.pk or 0) % 6,
    }
