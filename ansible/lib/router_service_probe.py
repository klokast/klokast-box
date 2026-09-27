"""Exercise native router services using only synthetic links inside Xen.

This qualification has no production VIF, state, enrollment, or authority. It
does not establish compatibility with a different release or full boot health.
"""
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

WORK = Path('/run/router-service-probe')


def guard():
    if (os.geteuid() != 0 or Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000' or
            {p.name for p in Path('/sys/class/net').iterdir()} != {'lo'} or
            Path('/etc/hostname').read_text() != 'boxa-router\n' or
            not {'klokast_router_personalize=1', 'klokast_router_test=1'} <=
                set(Path('/proc/cmdline').read_text().split())):
        raise RuntimeError('router service probe requires the isolated synthetic personalization boot')
    for path in ('/var/lib/tailscale/tailscaled.state', '/var/lib/dhcpcd/duid',
                 '/var/lib/dhcpcd/secret', '/var/lib/misc/dnsmasq.leases'):
        if Path(path).exists() or Path(path).is_symlink():
            raise RuntimeError('router service probe refuses existing service identity')


def run(argv, *, timeout=20, check=True):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('synthetic router probe command failed: ' + argv[0] +
                           ': ' + result.stderr[-800:])
    return result


@contextlib.contextmanager
def process(name, argv):
    with (WORK / (name + '.log')).open('x') as log:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
        try:
            yield child
        except Exception:
            # These logs belong only to the synthetic networkless fixture.
            # Keep bounded native diagnostics in the private Xen console log.
            print('Synthetic service diagnostic: ' + name + '\n' +
                  (WORK / (name + '.log')).read_text()[-2000:], flush=True)
            raise
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)


def wait_for(predicate, children, description, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        if any(child.poll() is not None for child in children):
            raise RuntimeError('synthetic service exited before ' + description)
        if time.monotonic() >= deadline:
            raise RuntimeError('synthetic service timed out before ' + description)
        time.sleep(0.1)


def network():
    run(['ip', 'link', 'set', 'lo', 'up'])
    run(['ip', 'netns', 'add', 'router-probe-upstream'])
    run(['ip', 'link', 'add', 'eth0', 'type', 'veth', 'peer', 'name', 'wanpeer'])
    run(['ip', 'link', 'set', 'wanpeer', 'netns', 'router-probe-upstream'])
    run(['ip', 'link', 'set', 'eth0', 'address', '02:00:00:00:00:01'])
    run(['ip', 'link', 'set', 'eth0', 'up'])
    run(['ip', '-n', 'router-probe-upstream', 'address', 'add', '198.19.0.1/24', 'dev', 'wanpeer'])
    run(['ip', '-n', 'router-probe-upstream', 'link', 'set', 'lo', 'up'])
    run(['ip', '-n', 'router-probe-upstream', 'link', 'set', 'wanpeer', 'up'])
    for number in range(1, 6):
        name = 'eth' + str(number)
        run(['ip', 'link', 'add', name, 'type', 'veth', 'peer', 'name', 'probeclient']
            if number == 3 else ['ip', 'link', 'add', name, 'type', 'dummy'])
        run(['ip', 'address', 'add', f'198.18.{number}.1/24', 'dev', name])
        run(['ip', 'link', 'set', name, 'up'])
    run(['ip', 'link', 'set', 'probeclient', 'address', '02:00:00:00:03:02'])
    run(['ip', 'link', 'set', 'probeclient', 'up'])


def dhcp_dns():
    upstream = ['ip', 'netns', 'exec', 'router-probe-upstream', 'dnsmasq',
        '--keep-in-foreground', '--conf-file=/dev/null', '--interface=wanpeer',
        '--bind-interfaces', '--port=0', '--dhcp-range=198.19.0.50,198.19.0.100,5m',
        '--dhcp-option=3,198.19.0.1', '--dhcp-leasefile=' + str(WORK / 'upstream.leases'),
        '--pid-file=' + str(WORK / 'upstream.pid')]
    lan = ['dnsmasq', '--keep-in-foreground', '--conf-file=/etc/dnsmasq.conf',
           '--pid-file=' + str(WORK / 'lan.pid')]
    with process('upstream', upstream) as server, process('lan', lan) as dns:
        wait_for(lambda: (WORK / 'upstream.pid').exists() and (WORK / 'lan.pid').exists(),
                 [server, dns], 'DHCP service readiness')
        with process('wan-client', ['dhcpcd', '--nobackground', '--timeout', '30', 'eth0']) as client:
            wait_for(lambda: Path('/var/lib/dhcpcd/eth0.lease').exists(),
                     [server, dns, client], 'native WAN lease', seconds=35)
            for path in ('/var/lib/dhcpcd/duid', '/var/lib/dhcpcd/secret', '/var/lib/dhcpcd/eth0.lease'):
                if not Path(path).is_file() or Path(path).stat().st_size == 0:
                    raise RuntimeError('native DHCP client did not create ' + path)
        hook = WORK / 'udhcpc-hook'
        hook.write_text('#!/bin/sh\nset -eu\ncase "$1" in\nbound|renew) '
                        'printf "%s\\n" "$ip" > /run/router-service-probe/client.address;;\nesac\n')
        hook.chmod(0o700)
        run(['busybox', 'udhcpc', '-i', 'probeclient', '-q', '-n', '-t', '3', '-T', '3',
             '-x', 'hostname:router-probe-client', '-s', str(hook)], timeout=20)
        address = (WORK / 'client.address').read_text().strip()
        if not address.startswith('198.18.3.') or not 50 <= int(address.split('.')[-1]) <= 150:
            raise RuntimeError('native LAN DHCP client obtained an unexpected address')
        rows = [line.split() for line in Path('/var/lib/misc/dnsmasq.leases').read_text().splitlines()]
        if not any(len(row) == 5 and row[1:4] == [
                '02:00:00:00:03:02', address, 'router-probe-client'] for row in rows):
            raise RuntimeError('native dnsmasq did not persist its synthetic LAN assignment')
        answer = run(['busybox', 'nslookup', 'router-probe-client', '198.18.3.1']).stdout
        if address not in answer:
            raise RuntimeError('native DNS service did not resolve the persisted LAN client')
    return {'wan_dhcp_identity': True, 'wan_lease': True, 'lan_lease': True, 'lan_dns': True}


def tailscale_state():
    socket = str(WORK / 'tailscale.sock')
    cli = ['tailscale', '--socket=' + socket]
    daemon = ['tailscaled', '--tun=userspace-networking', '--port=0', '--socket=' + socket,
              '--state=/var/lib/tailscale/tailscaled.state']
    with process('tailscale', daemon) as child:
        wait_for(lambda: Path(socket).exists(), [child], 'offline Tailscale socket')
        # A refused local port cannot enroll a machine or leave this guest.
        run([*cli, 'up', '--login-server=https://127.0.0.1:1', '--hostname=router-probe-initial',
             '--ssh', '--accept-dns=false', '--timeout=2s'], check=False, timeout=10)
        prefs = json.loads(run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-initial':
            raise RuntimeError('offline Tailscale did not persist the synthetic preferences')
        state = Path('/var/lib/tailscale/tailscaled.state')
        if not state.is_file() or not state.stat().st_size:
            raise RuntimeError('offline Tailscale did not create its native state file')
        run([*cli, 'set', '--hostname=router-probe-latest'])
    with process('tailscale-restart', daemon) as child:
        wait_for(lambda: Path(socket).exists(), [child], 'restarted Tailscale socket')
        prefs = json.loads(run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-latest':
            raise RuntimeError('restarted Tailscale did not retain the latest native preferences')
    return {'offline_tailscale_state': True, 'latest_preferences_after_restart': True}


def execute():
    guard()
    WORK.mkdir(mode=0o700)
    started = time.monotonic()
    try:
        network()
        run(['nft', '-f', '/etc/nftables.nft'])
        result = {**dhcp_dns(), **tailscale_state()}
        return {'kind': 'klokast.router-native-services.v1', 'success': True, 'tests': result,
                'seconds': round(time.monotonic() - started, 3),
                'production_identity': False, 'cross_release_compatibility': False}
    finally:
        run(['ip', 'netns', 'delete', 'router-probe-upstream'], check=False)
