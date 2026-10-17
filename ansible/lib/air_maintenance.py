"""Active-controller reservations using the existing VM operation lock."""
import json
import re
import subprocess

import platform_source

HELPER = '/usr/local/bin/klokast-air-workspace'


def remote(box, action, *, operation=None, ttl=1800, reservation=None, session=None):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', box):
        raise RuntimeError('invalid runner box')
    argv = ['tailscale', 'ssh', 'neo@' + box + '-air', 'doas', '-n', HELPER, action]
    if operation is not None:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', operation):
            raise RuntimeError('invalid runner maintenance operation ID')
        argv += ['--operation', operation, '--ttl', str(ttl)]
    if reservation:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', reservation):
            raise RuntimeError('invalid planned reservation ID')
        argv += ['--reservation', reservation]
    if session:
        import uuid
        if str(uuid.UUID(session))!=session:raise RuntimeError('invalid handoff discussion UUID')
        argv+=['--session',session]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('runner maintenance failed on ' + box + ': ' + result.stderr.strip()[-500:])
    return json.loads(result.stdout)


def usable_alternative(box):
    """Verify the runner and its local router/VPN dependencies before a stop."""
    import vm_control_probe
    import vm_service_probe
    view = platform_source.snapshot()
    errors = []
    for name in sorted(view['instance']['airunners']):
        if not name.endswith('-air') or name == box + '-air':
            continue
        other = name[:-4]
        try:
            status = remote(other, 'status')
            if not status['available'] or status['maintenance']:
                raise RuntimeError('runner is unavailable or reserved')
            vm_control_probe.probe(other, 'air')
            authenticated=remote(other,'ready')
            if authenticated.get('authenticated') is not True:
                raise RuntimeError('runner OpenAI authentication is not ready')
            # The managed runner's gateway belongs to its box. Health checks
            # include the router's firewall, routing and WAN dependencies.
            result = subprocess.run(['ansible/bin/platform-check', '--box', other, '--target', 'router'],
                cwd=platform_source.REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
            if result.returncode:
                raise RuntimeError('router health check failed')
            vpn = view['instance']['boxes'][other].get('vpn-egress')
            if vpn:
                vm_service_probe.probe(other, 'vpn-egress')
            return other
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(name + ': ' + str(error))
    raise RuntimeError('replacement would remove the last verified usable runner; ' + '; '.join(errors))


def before_stop(box, operation, reservation=None):
    alternative = usable_alternative(box)
    # This is the final remote check. begin serializes with managed launcher
    # clients and publishes pending status before any VM shutdown.
    remote(box, 'begin', operation=operation, reservation=reservation)
    return alternative


def release(box, operation):
    return remote(box, 'complete', operation=operation)
