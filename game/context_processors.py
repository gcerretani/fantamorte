"""Context processor: espone alcune impostazioni pubbliche ai template."""
from django.conf import settings


def _active_nav(request):
    """Destinazione attiva della bottom nav, dal nome della URL corrente.

    Le sottopagine di leghe/squadre/persone/decessi appartengono tutte alla
    sezione "Leghe": il tab resta acceso anche navigando in profondità.
    """
    match = getattr(request, 'resolver_match', None)
    name = match.url_name if match else None
    if not name:
        return ''
    if name == 'home':
        return 'home'
    if name == 'stats':
        return 'stats'
    if name == 'profile':
        return 'profile'
    if name == 'notifications':
        return 'notifications'
    if name.startswith(('league', 'team', 'person', 'death')) or name in (
        'add_person', 'remove_person', 'substitute_member',
    ):
        return 'leghe'
    return ''


def public_settings(request):
    # Memoizzato sulla request: la reverse OneToOne user.profile costa una
    # query e questo processor gira per ogni render di template.
    if not hasattr(request, '_fm_profile'):
        request._fm_profile = (
            getattr(request.user, 'profile', None)
            if request.user.is_authenticated else None
        )
    profile = request._fm_profile
    theme_preference = profile.theme_preference if profile else 'auto'

    # Badge notifiche non-lette (COUNT leggera, solo per autenticati).
    unread = 0
    if request.user.is_authenticated:
        from .models import Notification
        unread = Notification.objects.filter(user=request.user, is_read=False).count()

    return {
        'VAPID_PUBLIC_KEY': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'PWA_APP_NAME': getattr(settings, 'PWA_APP_NAME', 'Fantamorte'),
        'PWA_THEME_COLOR': getattr(settings, 'PWA_APP_THEME_COLOR', '#171a20'),
        'user_profile': profile,
        'theme_preference': theme_preference,
        'active_nav': _active_nav(request),
        'unread_notifications_count': unread,
    }
