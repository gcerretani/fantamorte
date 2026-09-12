"""Validation and transport hardening for Web Push endpoints."""
import ipaddress
import socket
from urllib.parse import urlsplit

import requests
from django.conf import settings


class UnsafePushEndpoint(ValueError):
    pass


# Browser PushSubscription endpoints are issued by push services, not by the
# application user.  Restricting outbound delivery to known provider domains
# makes the hostname itself the SSRF boundary and removes the DNS-rebinding
# TOCTOU between validation and requests/urllib3 resolving the destination.
# Operators can extend (never implicitly weaken) this list with
# WEBPUSH_ALLOWED_HOSTS in Django settings. A leading dot means "this domain
# and its subdomains".
_DEFAULT_ALLOWED_PUSH_HOSTS = (
    'fcm.googleapis.com',
    'updates.push.services.mozilla.com',
    '.push.apple.com',
)


def _allowed_host_patterns():
    configured = getattr(settings, 'WEBPUSH_ALLOWED_HOSTS', None)
    if configured is None:
        return _DEFAULT_ALLOWED_PUSH_HOSTS
    if isinstance(configured, str):
        configured = [h.strip() for h in configured.split(',') if h.strip()]
    return tuple(str(h).strip().lower() for h in configured if str(h).strip())


def _host_is_allowed(host):
    for pattern in _allowed_host_patterns():
        if pattern.startswith('.'):
            root = pattern[1:]
            if host == root or host.endswith(pattern):
                return True
        elif host == pattern:
            return True
    return False


def _validate_ip(value):
    ip = ipaddress.ip_address(value.split('%', 1)[0])
    if not ip.is_global:
        raise UnsafePushEndpoint('Endpoint push non pubblico')


def validate_push_endpoint(endpoint, *, resolve=False):
    """Validate a Web Push endpoint against trusted push-service domains.

    The provider allow-list is the primary SSRF boundary. DNS/IP checks are
    defense in depth for literal addresses and provider resolution; requests
    may resolve again later, but an attacker cannot supply an arbitrary DNS
    zone because arbitrary hostnames are rejected before persistence/delivery.
    """
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 4096:
        raise UnsafePushEndpoint('Endpoint push non valido')
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise UnsafePushEndpoint('Endpoint push non valido') from exc
    if parsed.scheme != 'https' or not parsed.hostname:
        raise UnsafePushEndpoint('Endpoint push deve usare HTTPS')
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePushEndpoint('Credenziali non ammesse nell endpoint push')
    if port not in (None, 443):
        raise UnsafePushEndpoint('Porta endpoint push non ammessa')

    host = parsed.hostname.rstrip('.').lower()
    if not _host_is_allowed(host):
        raise UnsafePushEndpoint('Provider push non ammesso')

    try:
        _validate_ip(host)
        literal_ip = True
    except UnsafePushEndpoint:
        raise
    except ValueError:
        literal_ip = False

    if resolve and not literal_ip:
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafePushEndpoint('Endpoint push non risolvibile') from exc
        if not answers:
            raise UnsafePushEndpoint('Endpoint push non risolvibile')
        for answer in answers:
            _validate_ip(answer[4][0])
    return endpoint


class NoRedirectSession(requests.Session):
    """Requests session that never follows redirects for push delivery."""

    def request(self, method, url, **kwargs):
        kwargs['allow_redirects'] = False
        return super().request(method, url, **kwargs)
