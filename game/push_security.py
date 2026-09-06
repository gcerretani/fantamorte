"""Validation and transport hardening for Web Push endpoints."""
import ipaddress
import socket
from urllib.parse import urlsplit

import requests


class UnsafePushEndpoint(ValueError):
    pass


def _validate_ip(value):
    ip = ipaddress.ip_address(value.split('%', 1)[0])
    if not ip.is_global:
        raise UnsafePushEndpoint('Endpoint push non pubblico')


def validate_push_endpoint(endpoint, *, resolve=False):
    """Validate a Web Push endpoint without restricting providers.

    Persistence performs cheap structural/literal-IP validation. Before every
    outbound request ``resolve=True`` also verifies that every current DNS
    answer is globally routable. Redirects are disabled by the transport, so a
    public endpoint cannot bounce the request into the local network.
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
    if host == 'localhost' or host.endswith('.localhost'):
        raise UnsafePushEndpoint('Endpoint push locale non ammesso')

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
