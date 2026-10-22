from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django import template

register = template.Library()

# Larghezze delle thumbnail Commons, in px reali. Sono i "bucket" standard
# di Wikimedia: una larghezza arbitraria verrebbe comunque arrotondata lato
# server, e restare sui bucket massimizza gli hit di cache sulle CDN.
# Ogni taglia CSS dell'avatar prende il bucket ≥ 2× il suo lato (retina).
AVATAR_SIZES = {
    'xs': 120,   # 1.4rem (storia degli slot, inline nel testo)
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


def _thumb(person, url, width):
    """Thumbnail dalla cache locale (game/person_images.py) se possibile,
    altrimenti diretta da Commons."""
    if not url:
        return ''
    from game.person_images import proxy_url
    return proxy_url(person, width) or commons_thumb(url, width)


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
        'src': _thumb(person, url, width),
        'src2x': _thumb(person, url, width * 2) if size == 'lg' else '',
        'initials': person_initials(getattr(person, 'name_it', '')),
        # Tinta deterministica per le iniziali: stessa persona, stesso colore
        # su ogni pagina (6 varianti definite in fantamorte.css).
        'tint': (person.pk or 0) % 6,
    }


# Pagine dove la card «lutto» non si mostra: il feed (la notifica è lì) e
# le pagine tecniche.
MOURNING_SKIP_URLS = {'notifications'}


@register.inclusion_tag('game/_mourning.html', takes_context=True)
def mourning_overlay(context):
    """Card a tutto schermo per il decesso più recente nella tua rosa (#106).

    Si appoggia al feed persist-first: la sorgente è la `Notification`
    `death_team` non letta più recente; chiudere la card la segna come letta,
    quindi compare una volta sola e su un solo dispositivo. Nessuno stato
    nuovo nel DB.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    match = getattr(request, 'resolver_match', None)
    if match and match.url_name in MOURNING_SKIP_URLS:
        return {}
    from game.models import Notification, Team
    from game.scoring import compute_team_points_for_death

    notification = (
        Notification.objects.filter(
            user=user, kind=Notification.KIND_DEATH_TEAM, is_read=False,
            death__isnull=False, death__is_confirmed=True,
        )
        .select_related('death__person').order_by('-created_at').first()
    )
    if notification is None:
        return {}
    death = notification.death
    teams = (Team.objects.filter(manager=user, members__person=death.person)
             .select_related('league').distinct())
    scored = []
    for team in teams:
        points = compute_team_points_for_death(team, death)
        if points:
            scored.append({'team': team, 'points': points})
    return {
        'notification': notification,
        'death': death,
        'person': death.person,
        'scored': scored,
        'total_points': sum(s['points'] for s in scored),
    }


@register.simple_tag
def fm_field(bound_field):
    """Widget di un campo con le classi del design system (form-control,
    form-select, form-check-input + is-invalid). Per i form che non passano
    da game/forms.py, es. le pagine allauth renderizzate da
    templates/allauth/elements/fields.html."""
    from django.forms.widgets import CheckboxInput, RadioSelect, Select

    widget = bound_field.field.widget
    if isinstance(widget, CheckboxInput):
        base = 'form-check-input'
    elif isinstance(widget, RadioSelect):
        base = ''
    elif isinstance(widget, Select):
        base = 'form-select'
    else:
        base = 'form-control'
    classes = widget.attrs.get('class', '').split()
    if base and base not in classes:
        classes.append(base)
    if bound_field.errors:
        classes.append('is-invalid')
    return bound_field.as_widget(attrs={'class': ' '.join(classes)} if classes else None)


@register.simple_tag(takes_context=True)
def password_login(context):
    """True se il login con password è attivo (vedi adapters.password_login_enabled)."""
    from game.adapters import password_login_enabled
    return password_login_enabled(context.get('request'))


@register.inclusion_tag('game/_person_avatar.html')
def user_avatar(user, size='sm'):
    """Avatar di un manager (game/user_avatars.py), stessa resa dell'avatar
    persona: immagine sopra le iniziali, che restano se manca o non carica."""
    from game.user_avatars import avatar_url
    if size not in AVATAR_SIZES:
        size = 'sm'
    # Lato CSS ×2 per gli schermi retina: sm 2rem → 64 px, lg 4rem → 128 px.
    px = {'xs': 48, 'sm': 64, 'md': 96, 'lg': 160, 'xl': 400}[size]
    return {
        'size': size,
        'dead': False,
        'src': avatar_url(user, px),
        'src2x': '',
        'initials': person_initials(getattr(user, 'username', '')),
        'tint': (getattr(user, 'pk', 0) or 0) % 6,
    }
