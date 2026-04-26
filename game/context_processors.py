"""Context processor: espone alcune impostazioni pubbliche ai template."""
from django.conf import settings


def public_settings(request):
    profile = None
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
    theme_preference = profile.theme_preference if profile else 'auto'
    return {
        'VAPID_PUBLIC_KEY': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'PWA_APP_NAME': getattr(settings, 'PWA_APP_NAME', 'Fantamorte'),
        'PWA_THEME_COLOR': getattr(settings, 'PWA_APP_THEME_COLOR', '#212529'),
        'user_profile': profile,
        'theme_preference': theme_preference,
    }
