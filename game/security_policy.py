"""Security policy switches that must be enforced consistently at runtime."""

from django.conf import settings


def install():
    # django-allauth will render a confirmation form and require POST/CSRF to
    # start OAuth instead of initiating an external login from a GET request.
    settings.SOCIALACCOUNT_LOGIN_ON_GET = False
