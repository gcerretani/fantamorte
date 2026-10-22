from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


def password_login_enabled(request=None):
    """Login con password attivo? `PASSWORD_LOGIN_ENABLED=False` lo spegne
    solo se c'è almeno un provider social/OIDC con cui entrare: altrimenti
    nessuno potrebbe più accedere."""
    if settings.PASSWORD_LOGIN_ENABLED:
        return True
    cached = getattr(request, '_fm_password_login', None)
    if cached is not None:
        return cached
    from allauth.socialaccount.adapter import get_adapter
    enabled = not get_adapter(request).list_providers(request)
    if request is not None:
        request._fm_password_login = enabled
    return enabled


class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    """Rispetta ACCOUNT_SIGNUP_ENABLED per il signup via form (email+password).

    Non impedisce il login di account gia' esistenti, solo la creazione di
    nuovi account da /accounts/signup/. Indipendente dal signup via OAuth,
    vedi ClosedSignupSocialAccountAdapter.
    """

    def is_open_for_signup(self, request):
        # Senza login con password, anche la registrazione via form non ha
        # senso: ci si iscrive dal provider (signup social, interruttore a parte).
        return settings.ACCOUNT_SIGNUP_ENABLED and password_login_enabled(request)


class ClosedSignupSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Interruttore indipendente per il signup via OAuth (Google/GitHub),
    cioe' la creazione automatica dell'account al primo login social.

    Non ha alcun effetto sul login: se l'utente ha gia' un account (creato in
    precedenza o collegato manualmente), l'autenticazione via OAuth funziona
    comunque, a prescindere da SOCIALACCOUNT_SIGNUP_ENABLED. Indipendente da
    ACCOUNT_SIGNUP_ENABLED: si puo' chiudere il signup via form lasciando
    aperto quello via OAuth o viceversa.
    """

    def is_open_for_signup(self, request, sociallogin):
        return settings.SOCIALACCOUNT_SIGNUP_ENABLED
