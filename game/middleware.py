"""Middleware che richiede l'autenticazione per tutto il sito.

Le rotte pubbliche sono solo quelle elencate in `PUBLIC_PATHS` /
`PUBLIC_PREFIXES`: login, registrazione, reset password, static, PWA
manifest e service worker, pagina offline.
"""
from django.conf import settings
from django.shortcuts import redirect


PUBLIC_PATHS = {
    '/manifest.webmanifest',
    '/sw.js',
    '/offline/',
    '/favicon.ico',
    '/robots.txt',
}

PUBLIC_PREFIXES = (
    '/accounts/',          # allauth + django.contrib.auth
    '/static/',
    '/media/',
)


class LoginRequiredEverywhereMiddleware:
    """Reindirizza gli utenti anonimi alla pagina di login per qualunque URL non pubblico."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not _is_public(request.path):
            login_url = getattr(settings, 'LOGIN_URL', '/accounts/login/')
            return redirect(f'{login_url}?next={request.path}')
        return self.get_response(request)


def _is_public(path):
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)
