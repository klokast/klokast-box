"""Read-only health checks of a native runner, for `airunner health`.

The workspace check uses the existing runner maintenance status command. The
web check runs one request at a time on the runner itself, so it uses the same
path as the agent tools: the redirection through the same-box VPN gateway when
the runner is a declared VPN client, else the direct router path.
"""
import subprocess
import time

import air_maintenance
from health_checks import check, tailnet_devices, tailnet_identity

TAG = 'tag:airunner'
# Public endpoints that return a fixed answer without consuming an API quota.
WEB = (('GitHub API', 'https://api.github.com/rate_limit', 200),
       ('Google', 'https://www.google.com/generate_204', 204))
DISK_WARNING = 85  # percent used
# Sequential requests. Arguments are fixed public URLs; a failed request prints 000.
PROBE = r'''df -Pk / /srv/retained | awk 'NR > 1 {print "disk", $6, $5}'
for url in "$@"; do
    curl -sS -o /dev/null --max-time 20 -w 'web %{http_code} %{time_total}\n' "$url" 2>/dev/null || echo 'web 000 0'
done
'''


def workspace_checks(value):
    result = [check('Workspace', 'ok' if value.get('available') else 'failed',
                    'ready' if value.get('available') else '; '.join(value.get('readiness_errors') or ['not ready']))]
    held = value.get('reservation') or {}
    result.append(check('Maintenance', 'warning' if value.get('maintenance') else 'ok',
                        'closed by ' + air_maintenance.closed_by(held) if value.get('maintenance') else 'open'))
    sessions = value.get('sessions') or []
    result.append(check('Sessions', 'ok', '%d tmux session(s)%s; %d attached client(s)' % (
        len(sessions), ', main present' if value.get('workspace') else ', no main session', value.get('attached', 0))))
    threads, busy = value.get('codex_threads') or [], value.get('busy_pids') or []
    result.append(check('Agent work', 'ok', '%d busy process(es), %d active Codex thread(s)' % (len(busy), len(threads))))
    return result


def probe_checks(output, path):
    """Turn the runner probe output into disk and web checks."""
    lines = [line.split() for line in output.splitlines()]
    result = []
    for fields in (f for f in lines if f[:1] == ['disk']):
        used = int(fields[2].rstrip('%'))
        result.append(check('Disk ' + fields[1], 'warning' if used >= DISK_WARNING else 'ok', '%d%% used' % used))
    answers = [f for f in lines if f[:1] == ['web']]
    if len(answers) != len(WEB): raise RuntimeError('the runner returned an incomplete web check')
    for (name, _url, expected), (_web, code, seconds) in zip(WEB, answers):
        ok = int(code) == expected
        result.append(check('Web traffic: ' + name, 'ok' if ok else 'failed',
                            ('HTTP %s in %.1f s' % (code, float(seconds)) if code != '000' else 'no answer') + ' ' + path))
    return result


def report(box, registry):
    hostname = box + '-air'
    if hostname not in registry.get('airunners', []):
        return {'checks': [check('Declaration', 'failed', 'Instance declares no ' + hostname)]}
    gateway = hostname in (registry['boxes'][box].get('vpn_egress') or {}).get('clients', [])
    path = 'through ' + box + '-vpn-egress' if gateway else 'direct'
    found = [check('Declaration', 'ok', 'declared runner; web traffic ' + path)]
    try: found += workspace_checks(air_maintenance.remote(box, 'status'))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        found.append(check('Workspace', 'failed', str(error)[-300:]))
    try: found += tailnet_identity(hostname, [TAG], tailnet_devices(), time.time())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        found.append(check('Tailnet identity', 'failed', 'cannot read the Tailnet devices: ' + str(error)[-200:]))
    try:
        result = subprocess.run(['tailscale', 'ssh', 'neo@' + hostname, 'sh', '-s', '--', *[url for _n, url, _c in WEB]],
                                input=PROBE, text=True, capture_output=True, timeout=90)
        if result.returncode: raise RuntimeError(result.stderr.strip()[-300:] or 'the runner probe failed')
        found += probe_checks(result.stdout, path)
    except (RuntimeError, ValueError, OSError, IndexError, subprocess.SubprocessError) as error:
        found.append(check('Web traffic', 'failed', str(error)[-300:]))
    return {'checks': found}
