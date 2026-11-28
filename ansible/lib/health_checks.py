"""Shared parts of the role health checks that `machine-status show` runs.

A health command prints one JSON line: {"checks": [check, ...]}. Each check is
a name, a state in STATES and one detail line. The checks only read state.
"""
import calendar
import json
import subprocess
import time

STATES = ('ok', 'warning', 'failed', 'skipped')
# A key that expires within this time needs a new enrollment soon.
KEY_EXPIRY_WARNING = 30 * 86400


def check(name, state, detail):
    return {'name': name, 'state': state, 'detail': detail}


def parse_time(value):
    try: return calendar.timegm(time.strptime(value[:19], '%Y-%m-%dT%H:%M:%S'))
    except (TypeError, ValueError): return None


def tailnet_devices():
    """Read the Tailnet devices through the existing controller wrapper."""
    return json.loads(subprocess.check_output(['doas', '-n', '/usr/local/sbin/ts-devices-list'], text=True,
                                              timeout=60)).get('devices', [])


def tailnet_identity(hostname, tags, devices, now):
    """Check the one Tailnet identity of a machine: its tags, its control connection and its key expiry."""
    found = [d for d in devices if str(d.get('hostname', '')).split('.')[0] == hostname]
    if len(found) != 1:
        return [check('Tailnet identity', 'failed', 'expected one ' + hostname + ' identity, found %d' % len(found))]
    device = found[0]
    result = [check('Tailnet identity', 'ok' if sorted(device.get('tags') or []) == sorted(tags) else 'failed',
                    'tags ' + ', '.join(device.get('tags') or ['none']))]
    seen = parse_time(device.get('lastSeen'))
    result.append(check('Tailscale control', 'ok' if device.get('connectedToControl') is True else 'failed',
                        ('connected' if device.get('connectedToControl') is True else 'not connected') +
                        ('' if seen is None else '; last seen ' + time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(seen)))))
    expires = parse_time(device.get('expires'))
    if device.get('keyExpiryDisabled') is True:
        result.append(check('Tailscale key', 'ok', 'key expiry disabled'))
    elif expires is None:
        result.append(check('Tailscale key', 'warning', 'unknown key expiry'))
    else:
        result.append(check('Tailscale key', 'failed' if expires <= now else 'warning' if expires - now < KEY_EXPIRY_WARNING
                            else 'ok', 'expires ' + time.strftime('%Y-%m-%d', time.gmtime(expires))))
    return result
