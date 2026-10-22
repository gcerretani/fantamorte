"""Immagine dei manager: Gravatar, foto dell'account collegato o iniziali (#121).

La sorgente la sceglie l'utente dal profilo (`UserProfile.avatar_source`).
La foto social viene copiata in `UserProfile.social_avatar_url` a ogni
collegamento o login social (signal allauth sotto), così classifiche e
podio leggono un campo invece di una query per manager.

Gravatar si chiede con `d=404`: se l'email non ha un Gravatar il browser
riceve 404, il listener `error` di fantamorte.js toglie l'immagine e restano
le iniziali, come per le foto delle persone. Hash SHA-256 dell'email
normalizzata (Gravatar lo accetta al posto del vecchio MD5).
"""
import hashlib
import logging

from allauth.socialaccount.signals import (
    social_account_added, social_account_removed, social_account_updated,
)
from django.dispatch import receiver

from .models import UserProfile

logger = logging.getLogger(__name__)

GRAVATAR_URL = 'https://www.gravatar.com/avatar/{digest}?s={px}&d=404'


def gravatar_url(email, px):
    email = (email or '').strip().lower()
    if not email:
        return ''
    digest = hashlib.sha256(email.encode()).hexdigest()
    return GRAVATAR_URL.format(digest=digest, px=int(px))


def _profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def avatar_url(user, px):
    """URL dell'immagine dell'utente alla dimensione `px`, o '' (iniziali)."""
    if user is None or not getattr(user, 'is_active', True):
        return ''
    profile = _profile(user)
    source = profile.avatar_source if profile else UserProfile.AVATAR_AUTO
    social = profile.social_avatar_url if profile else ''
    if source == UserProfile.AVATAR_NONE:
        return ''
    if source == UserProfile.AVATAR_SOCIAL:
        return social
    if source == UserProfile.AVATAR_GRAVATAR:
        return gravatar_url(user.email, px)
    return social or gravatar_url(user.email, px)


def _picture_from_extra_data(extra_data):
    """Foto dai dati salvati del provider: claim `picture` (OpenID Connect,
    che allauth non espone come avatar, e Google) o `avatar_url` (GitHub).
    Userinfo prima dell'id_token."""
    data = extra_data or {}
    for part in (data.get('userinfo'), data.get('id_token'), data):
        if not isinstance(part, dict):
            continue
        for key in ('picture', 'avatar_url'):
            if isinstance(part.get(key), str) and part[key].startswith('https://'):
                return part[key]
    return ''


def sync_social_avatar(user):
    """Copia sul profilo la foto del primo account collegato che ne ha una."""
    profile = _profile(user)
    if profile is None:
        return
    url = ''
    for account in user.socialaccount_set.all():
        try:
            url = account.get_avatar_url() or ''
        except Exception:
            # Provider non più configurato (app rimossa): si ripiega sui dati
            # salvati, sotto.
            logger.info('Avatar dal provider non disponibile per %s', account, exc_info=True)
            url = ''
        url = url or _picture_from_extra_data(account.extra_data)
        if url:
            break
    url = url[:1000]
    if url != profile.social_avatar_url:
        profile.social_avatar_url = url
        profile.save(update_fields=['social_avatar_url'])


@receiver(social_account_added)
@receiver(social_account_updated)
def _on_social_account_changed(request, sociallogin, **kwargs):
    sync_social_avatar(sociallogin.user)


@receiver(social_account_removed)
def _on_social_account_removed(request, socialaccount, **kwargs):
    sync_social_avatar(socialaccount.user)
