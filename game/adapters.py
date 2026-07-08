from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class ClosedSignupAccountAdapter(DefaultAccountAdapter):
    """Rispetta ACCOUNT_SIGNUP_ENABLED per il signup via form (email+password).

    Non impedisce il login di account gia' esistenti, solo la creazione di
    nuovi account da /accounts/signup/.
    """

    def is_open_for_signup(self, request):
        return settings.ACCOUNT_SIGNUP_ENABLED


class ClosedSignupSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Stesso interruttore per il signup via OAuth (Google/GitHub).

    Senza questo, con ACCOUNT_SIGNUP_ENABLED=False si chiuderebbe solo il
    form email+password ma chiunque potrebbe comunque registrarsi via
    OAuth una volta configurato un provider.
    """

    def is_open_for_signup(self, request, sociallogin):
        return settings.ACCOUNT_SIGNUP_ENABLED
