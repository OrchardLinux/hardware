"""Exact HTTPS URLs and downgrade-safe redirects for every download path."""
from urllib.parse import urlsplit
import urllib.request


def validate_url(value):
    try:
        url = urlsplit(value)
        valid = (url.scheme == 'https' and url.hostname and url.username is None and
                 url.password is None and not url.fragment and '\\' not in value and
                 not any(ord(c) <= 32 or ord(c) == 127 for c in value))
        port = url.port
        valid = valid and (port is None or 0 < port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Expected exact HTTPS URL without credentials or fragment')


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def open_url(url, timeout=60):
    validate_url(url.full_url if isinstance(url, urllib.request.Request) else url)
    response = urllib.request.build_opener(HTTPSRedirect()).open(url, timeout=timeout)
    try:
        validate_url(response.geturl())
    except Exception:
        response.close()
        raise
    return response
