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


def run(argv, *, timeout=20, check=True, data=None):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL if data is None else None,
                            input=data, capture_output=True, text=True, timeout=timeout)
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
    run(['ip', '-n', 'router-probe-upstream', '-6', 'address', 'add', '2001:db8:1::1/64', 'dev', 'wanpeer'])
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


def dhcp_dns(client_id=2, *, seed_expiry=False, verify_expiry=False):
    if client_id not in (2, 3):
        raise RuntimeError('unsupported synthetic LAN client')
    mac = '02:00:00:00:03:0' + str(client_id)
    hostname = 'router-probe-client' if client_id == 2 else 'router-probe-latest'
    run(['ip', 'link', 'set', 'probeclient', 'address', mac])
    previous = None
    expiring = None
    lease_file = Path('/var/lib/misc/dnsmasq.leases')
    if lease_file.exists():
        rows = [line.split() for line in lease_file.read_text().splitlines()]
        previous = next((row[2] for row in rows if len(row) == 5 and row[1] == mac and
                         row[0].isdigit() and int(row[0]) > time.time()), None)
        expiring = next((int(row[0]) for row in rows if len(row) == 5 and
                         row[1] == '02:00:00:00:03:04' and row[0].isdigit()), None)
    if verify_expiry and expiring is None:
        raise RuntimeError('native expiry test lost the old synthetic lease')
    upstream = ['ip', 'netns', 'exec', 'router-probe-upstream', 'dnsmasq',
        '--keep-in-foreground', '--conf-file=/dev/null', '--interface=wanpeer',
        '--bind-interfaces', '--port=0', '--dhcp-range=198.19.0.50,198.19.0.100,5m',
        '--enable-ra', '--dhcp-range=2001:db8:1::50,2001:db8:1::100,slaac,64,5m',
        '--dhcp-option=3,198.19.0.1', '--dhcp-leasefile=' + str(WORK / 'upstream.leases'),
        '--pid-file=' + str(WORK / 'upstream.pid')]
    lan = ['dnsmasq', '--keep-in-foreground', '--conf-file=/etc/dnsmasq.conf',
           '--pid-file=' + str(WORK / 'lan.pid'),
           '--dhcp-host=02:00:00:00:03:04,router-probe-expiring,2m']
    with process('upstream', upstream) as server, process('lan', lan) as dns:
        wait_for(lambda: (WORK / 'upstream.pid').exists() and (WORK / 'lan.pid').exists(),
                 [server, dns], 'DHCP service readiness')
        with process('wan-client', ['dhcpcd', '--nobackground', '--timeout', '30', 'eth0']) as client:
            wait_for(lambda: all(Path(path).exists() for path in (
                '/var/lib/dhcpcd/eth0.lease', '/var/lib/dhcpcd/secret')) and
                '198.19.0.' in run(['ip', '-4', '-o', 'address', 'show', 'dev', 'eth0']).stdout,
                [server, dns, client], 'native WAN lease and SLAAC privacy secret', seconds=35)
            for path in ('/var/lib/dhcpcd/duid', '/var/lib/dhcpcd/secret', '/var/lib/dhcpcd/eth0.lease'):
                if not Path(path).is_file() or Path(path).stat().st_size == 0:
                    raise RuntimeError('native DHCP client did not create ' + path)
        hook = WORK / 'udhcpc-hook'
        hook.write_text('#!/bin/sh\nset -eu\ncase "$1" in\nbound|renew) '
                        'printf "%s\\n" "$ip" > /run/router-service-probe/client.address;;\nesac\n')
        hook.chmod(0o700)
        if verify_expiry:
            # Renew the old writer's lease before requesting a new client grant.
            retained = next((row[2] for row in rows if len(row) == 5 and row[1] == '02:00:00:00:03:02'), None)
            if retained is None:
                raise RuntimeError('native renewal test lost the old LAN client')
            run(['ip', 'link', 'set', 'probeclient', 'address', '02:00:00:00:03:02'])
            run(['busybox', 'udhcpc', '-i', 'probeclient', '-q', '-n', '-t', '3', '-T', '3',
                 '-x', 'hostname:router-probe-client', '-s', str(hook)], timeout=20)
            if (WORK / 'client.address').read_text().strip() != retained:
                raise RuntimeError('new dnsmasq did not renew the old writer LAN assignment')
            run(['ip', 'link', 'set', 'probeclient', 'address', mac])
        run(['busybox', 'udhcpc', '-i', 'probeclient', '-q', '-n', '-t', '3', '-T', '3',
             '-x', 'hostname:' + hostname, '-s', str(hook)], timeout=20)
        address = (WORK / 'client.address').read_text().strip()
        if not address.startswith('198.18.3.') or not 50 <= int(address.split('.')[-1]) <= 150:
            raise RuntimeError('native LAN DHCP client obtained an unexpected address')
        rows = [line.split() for line in Path('/var/lib/misc/dnsmasq.leases').read_text().splitlines()]
        if not any(len(row) == 5 and row[1:4] == [mac, address, hostname] for row in rows):
            raise RuntimeError('native dnsmasq did not persist its synthetic LAN assignment')
        if previous is not None and address != previous:
            raise RuntimeError('native dnsmasq did not retain the unexpired synthetic client assignment')
        answer = run(['busybox', 'nslookup', hostname, '198.18.3.1']).stdout
        if address not in answer:
            raise RuntimeError('native DNS service did not resolve the persisted LAN client')
        if seed_expiry:
            run(['ip', 'link', 'set', 'probeclient', 'address', '02:00:00:00:03:04'])
            run(['busybox', 'udhcpc', '-i', 'probeclient', '-q', '-n', '-t', '3', '-T', '3',
                 '-x', 'hostname:router-probe-expiring', '-s', str(hook)], timeout=20)
            rows = [line.split() for line in lease_file.read_text().splitlines()]
            expiry = next((int(row[0]) for row in rows if len(row) == 5 and row[1] ==
                           '02:00:00:00:03:04' and row[0].isdigit()), 0)
            if not 90 <= expiry - time.time() <= 125:
                raise RuntimeError('native dnsmasq did not create the bounded expiry fixture')
        if verify_expiry:
            wait_for(lambda: all('02:00:00:00:03:04' not in line for line in lease_file.read_text().splitlines()),
                     [server, dns], 'native expiry of the old LAN lease', seconds=150)
    result = {'wan_dhcp_identity': True, 'wan_lease': True, 'lan_lease': True, 'lan_dns': True}
    if seed_expiry:
        result['expiry_lease_created'] = True
    if verify_expiry:
        result.update(expired_lease_removed=True, old_client_renewed=True)
    return result


def tailscale_state():
    component = json.loads(Path('/etc/klokast-router-inputs.json').read_text())['tailscale']
    version = component['version']
    if (run(['tailscale', 'version']).stdout.splitlines()[0] != version or
            run(['tailscaled', '--version']).stdout.splitlines()[0] != version):
        raise RuntimeError('synthetic router has a different upstream Tailscale binary version')
    socket = str(WORK / 'tailscale.sock')
    cli = ['tailscale', '--socket=' + socket]
    daemon = ['tailscaled', '--tun=userspace-networking', '--port=0', '--socket=' + socket,
              '--state=/var/lib/tailscale/tailscaled.state']
    with process('tailscale', daemon) as child:
        wait_for(lambda: Path(socket).exists(), [child], 'offline Tailscale socket')
        if 'Daemon: ' + version not in run([*cli, 'version', '--daemon']).stdout.splitlines():
            raise RuntimeError('synthetic router is running a different Tailscale daemon version')
        # A refused local port cannot enroll a machine or leave this guest.
        run([*cli, 'up', '--login-server=https://127.0.0.1:1', '--hostname=router-probe-initial',
             '--ssh', '--accept-dns=false', '--timeout=2s'], check=False, timeout=10)
        prefs = json.loads(run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-initial':
            raise RuntimeError('offline Tailscale did not persist the synthetic preferences')
        state = Path('/var/lib/tailscale/tailscaled.state')
        if not state.is_file() or not state.stat().st_size:
            raise RuntimeError('offline Tailscale did not create its native state file')
        machine_key = json.loads(state.read_text()).get('_machinekey')
        if not machine_key:
            raise RuntimeError('offline Tailscale did not generate its machine key')
        # Tailscale intentionally keeps an unenrolled profile only in memory.
        # Its native development store API seeds a synthetic, logged-out profile
        # so the real daemon can exercise its persisted-profile reader/writer.
        # No node key or enrollment is invented. The machine key above is native.
        # Schema: tailscale v1.90.9 ipn/ipnlocal/profiles.go and ipn/prefs.go.
        user = {'ID': 4242, 'LoginName': 'router-probe@example.invalid'}
        profile = {'ID': '00aa', 'Key': 'profile-00aa', 'Name': 'router-probe',
                   'NodeID': 'nrouterprobe', 'UserProfile': user,
                   'ControlURL': 'https://127.0.0.1:1'}
        prefs.update(WantRunning=False, LoggedOut=True,
                     Config={'NodeID': profile['NodeID'], 'UserProfile': user})
        for key, value in (('profile-00aa', json.dumps(prefs)),
                           ('_profiles', json.dumps({'00aa': profile})),
                           ('_current-profile', 'profile-00aa')):
            run([*cli, 'debug', 'dev-store-set', '--danger', key, '-'], data=value)
    with process('tailscale-profile', daemon) as child:
        wait_for(lambda: Path(socket).exists(), [child], 'synthetic Tailscale profile')
        prefs = json.loads(run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-initial':
            raise RuntimeError('Tailscale did not read the stored synthetic profile')
        run([*cli, 'set', '--hostname=router-probe-latest'])
    with process('tailscale-restart', daemon) as child:
        wait_for(lambda: Path(socket).exists(), [child], 'restarted Tailscale socket')
        prefs = json.loads(run([*cli, 'debug', 'prefs']).stdout)
        if prefs.get('RunSSH') is not True or prefs.get('Hostname') != 'router-probe-latest':
            raise RuntimeError('restarted Tailscale did not retain the latest native preferences')
    if json.loads(state.read_text()).get('_machinekey') != machine_key:
        raise RuntimeError('Tailscale changed its native machine key across synthetic restarts')
    return {'offline_tailscale_state': True, 'upstream_daemon_version': True,
            'latest_preferences_after_restart': True,
            'native_machine_key_after_restart': True, 'synthetic_profile': True}


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
