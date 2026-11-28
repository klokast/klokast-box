"""Read-only health checks of a shared VM VPN gateway, for `platform-vpn-egress health`.

The checks reuse the existing status playbooks. They request no relay test. The
traffic check sends one request at a time through the explicit proxy, as the
controller downloads do; see doc/vpn-egress.md#gateway-local-feedback.
"""
import calendar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import ops_vpn_proxy

# Mihomo downloads the subscription every 86400 seconds. Two missed intervals are a warning.
SUBSCRIPTION_STALE = 2 * 86400
# Public endpoints that return a fixed answer without consuming an API quota.
TRAFFIC = (('GitHub API', 'https://api.github.com/rate_limit', 200),
           ('Google', 'https://www.google.com/generate_204', 204))
TRAFFIC_TIMEOUT = 20
PLAYBOOKS = ('69-vpn-egress-status.yml', '69-vpn-egress-provider.yml')


def check(name, state, detail):
    return {'name': name, 'state': state, 'detail': detail}


def ansible_results(host, variables, *, repo):
    """Run the status playbooks with the JSON callback. Return the result of each task on the host."""
    with tempfile.TemporaryDirectory(prefix='vpn-egress-health-') as directory:
        path = Path(directory) / 'vars.json'
        path.write_text(json.dumps(variables)); path.chmod(0o600)
        env = dict(os.environ, ANSIBLE_CONFIG=str(repo / 'ansible/ansible.cfg'), ANSIBLE_STDOUT_CALLBACK='json')
        result = subprocess.run(['ansible-playbook', '-i', str(repo / 'ansible/execution-inventory/hosts'),
                                 *[str(repo / 'ansible/playbooks' / name) for name in PLAYBOOKS],
                                 '--limit', host, '-e', '@' + str(path)],
                                cwd=repo, env=env, text=True, capture_output=True, timeout=300)
    # The JSON callback prints one document for each playbook.
    reports, text, decoder = [], result.stdout.strip(), json.JSONDecoder()
    try:
        while text:
            report, end = decoder.raw_decode(text); reports.append(report); text = text[end:].strip()
    except ValueError: reports = []
    if not reports:
        raise RuntimeError('the status playbooks returned no result (exit status %d)' % result.returncode)
    tasks = {}
    for report in reports:
        for play in report.get('plays', []):
            for task in play.get('tasks', []):
                if host in task.get('hosts', {}): tasks[task['task']['name']] = task['hosts'][host]
        if report.get('stats', {}).get(host, {}).get('unreachable'):
            raise RuntimeError('Ansible cannot reach ' + host)
    return tasks


def service_checks(tasks, now):
    """Turn the status playbook results into checks."""
    service = tasks.get('Read proxy status')
    if service is None or service.get('failed'):
        return [check('Proxy service', 'failed', 'the vpn-egress service is not started')]
    found = [check('Proxy service', 'ok', 'started')]
    provider = tasks.get('Read or update the native subscription')
    if provider is None or provider.get('failed'):
        # The playbook withholds private details; its message says where to look.
        detail = (provider or {}).get('stderr') or (provider or {}).get('msg') or 'not read'
        return found + [check('Subscription', 'failed', str(detail).strip()[-300:])]
    value = json.loads(provider['stdout'])
    relays = value.get('health') or []
    alive = [relay for relay in relays if relay.get('alive')]
    try: updated = calendar.timegm(time.strptime(value['updated_at'][:19], '%Y-%m-%dT%H:%M:%S'))
    except (TypeError, ValueError): updated = None
    age = 'unknown age' if updated is None else 'updated %s UTC, %.0f hours ago' % (
        time.strftime('%Y-%m-%d %H:%M', time.gmtime(updated)), (now - updated) / 3600)
    stale = updated is None or now - updated > SUBSCRIPTION_STALE or not relays
    found.append(check('Subscription', 'warning' if stale else 'ok', '%d relays; %s' % (len(relays), age)))
    tested = [relay for relay in relays if relay.get('history')]
    found.append(check('Relays alive', 'ok' if alive or not tested else 'warning',
                       '%d of %d at the last native test round' % (len(alive), len(relays)) if tested else
                       'no native test round since the last restart or update'))
    selected = next((relay for relay in relays if relay.get('name') == value.get('selected_server')), {})
    found.append(check('Selected relay', 'warning' if selected.get('history') and not selected.get('alive') else 'ok',
                       str(value.get('selected_server')) + (' (dead at the last test)' if selected.get('history') and
                                                            not selected.get('alive') else '')))
    return found


def profile_check(status):
    if status['replacement_needed_for_profile']:
        return check('Routing profile', 'warning', 'the installed profile differs from Instance; a gateway replacement applies it')
    url = (status['installed_routing_profile'] or {}).get('domain-list-url')
    return check('Routing profile', 'ok', 'equals Instance; domain list ' + (url or 'none: all web traffic is direct'))


def traffic_checks(box):
    """Send one request at a time through the explicit proxy from the same-box controller."""
    if socket.gethostname().split('.')[0] != box + '-ops':
        return [check('Proxy traffic', 'skipped', 'only the ' + box + '-ops controller can open the proxy tunnel')]
    try:
        with ops_vpn_proxy.tunnel(box) as (port, _process):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler(
                {'http': 'http://127.0.0.1:%d' % port, 'https': 'http://127.0.0.1:%d' % port}))
            found = []
            for name, url, expected in TRAFFIC:
                start = time.monotonic()
                try:
                    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'klokast-status'}),
                                     timeout=TRAFFIC_TIMEOUT) as response:
                        code = response.status
                except urllib.error.HTTPError as error: code = error.code
                except (OSError, ValueError) as error:
                    found.append(check('Proxy traffic: ' + name, 'failed', 'no answer: ' + type(error).__name__)); continue
                found.append(check('Proxy traffic: ' + name, 'ok' if code == expected else 'failed',
                                   'HTTP %d in %.1f s' % (code, time.monotonic() - start)))
            return found
    except ops_vpn_proxy.NotDeclared:
        return [check('Proxy traffic', 'skipped', box + '-ops is not a declared client')]
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        return [check('Proxy traffic', 'failed', str(error)[:300])]


def report(box, status, variables, *, repo):
    """Return the health checks of the declared gateway of the box."""
    if not status['declared']:
        return {'checks': [check('Declaration', 'failed', 'Instance declares no VPN gateway on ' + box)]}
    found = [check('Declaration', 'ok', 'clients: ' + (', '.join(status['clients']) or 'none')), profile_check(status)]
    try: found += service_checks(ansible_results(box + '-vpn-egress', variables, repo=repo), time.time())
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {'checks': found + [check('Gateway status', 'failed', str(error)[:300])]}
    if any(item['state'] == 'failed' for item in found):
        found.append(check('Proxy traffic', 'skipped', 'the gateway service is not ready'))
    else: found += traffic_checks(box)
    return {'checks': found}
