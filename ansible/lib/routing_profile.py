"""Pure validation and Mihomo routing settings selected by Instance."""
import copy
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit

FIELDS = {'domain-list-url', 'bootstrap-dns', 'vpn-dns'}


def public_ipv4(value):
    try:
        if not isinstance(value, str): return False
        address = ipaddress.IPv4Address(value)
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


def public_https(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s#]', value): return False
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if url.scheme != 'https' or url.username is not None or url.password is not None or url.port not in (None, 443): return False
        if re.fullmatch(r'[0-9.]+', host): return public_ipv4(host)
        if ':' in host or '.' not in host or len(host) > 253: return False
        for suffix in ('local', 'lan', 'localhost', 'ts.net', 'tailscale.com', 'internal', 'home.arpa'):
            if host == suffix or host.endswith('.'+suffix): return False
        return all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.'))
    except ValueError:
        return False


def validate(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('routing profile requires domain-list-url, bootstrap-dns and vpn-dns')
    for name, check in (('bootstrap-dns', public_ipv4), ('vpn-dns', public_https)):
        items = value[name]
        if not isinstance(items, list) or len(items) != 2 or not all(check(v) for v in items) or items[0] == items[1]:
            raise ValueError(name + ' requires two distinct public resolver addresses of the supported type')
    if value['domain-list-url'] is not None and not public_https(value['domain-list-url']):
        raise ValueError('domain-list-url requires a public HTTPS URL without credentials or fragments, or null')
    return copy.deepcopy(value)


def providers(profile):
    url = validate(profile)['domain-list-url']
    if url is None: return {}
    identity = hashlib.sha256(url.encode()).hexdigest()
    return {'selected': {'type': 'http', 'behavior': 'domain', 'format': 'yaml',
                         'url': url, 'path': './rules/'+identity+'.yaml',
                         'interval': 86400, 'proxy': 'VPN', 'size-limit': 4 * 1024 * 1024}}


def from_config(config):
    """Read only routing selection; never export subscription or API credentials."""
    entries = config['rule-providers']
    if len(entries) > 1: raise ValueError('installed gateway has ambiguous domain providers')
    url = next(iter(entries.values()))['url'] if entries else None
    endpoints = config['dns']['nameserver']
    if any(not isinstance(v, str) or not v.endswith('#VPN') for v in endpoints):
        raise ValueError('installed DNS does not use VPN transport')
    return validate({'domain-list-url': url, 'bootstrap-dns': config['dns']['proxy-server-nameserver'],
                     'vpn-dns': [v[:-4] for v in endpoints]})
