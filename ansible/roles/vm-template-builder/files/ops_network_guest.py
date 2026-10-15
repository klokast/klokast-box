#!/usr/bin/python3
"""Bounded, reversible controller uplink migration inside the selected ops VM."""
import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import ops_network

BASE = Path('/var/lib/klokast/network/ops-uplink')
MARKER = Path('/etc/klokast/ops-uplink.json')
OBSOLETE = ('etc/profile.d/vpn-egress.sh', 'etc/klokast/vpn-egress.json',
            'etc/network/if-up.d/91-klokast-ops-ipv6', 'etc/sysctl.d/91-klokast-ops-ipv6.conf')


def run(argv, *, check=True, timeout=40, env=None):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, env=env)
    if check and result.returncode:
        raise RuntimeError('ops network command failed: ' + ' '.join(argv[:3]) + ': ' + result.stderr[-500:])
    return result.stdout


def write(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.ops-uplink-' + str(os.getpid()))
    with temporary.open('w') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(content); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def save(state):
    write(BASE / 'transaction.json', json.dumps(state, sort_keys=True) + '\n')


def read():
    return json.loads((BASE / 'transaction.json').read_text())


@contextlib.contextmanager
def lock():
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (BASE / 'operation.lock').open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def identity():
    import hashlib
    files = ['/etc/ssh/ssh_host_ed25519_key.pub', '/home/smith/.ssh/github-klokast-instance.pub',
             '/home/smith/.ssh/authorized_keys']
    return {'files': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files},
            'tailscale_id': json.loads(run(['tailscale', 'status', '--json']))['Self']['ID'],
            'authority': json.loads(run(['/usr/local/sbin/klokast-controller-guard', '--status', '--json']))}


def clean_proxy():
    path = Path('/etc/gitconfig')
    if path.exists():
        import re
        content = re.sub(r'^# BEGIN KLOKAST VPN EGRESS\n.*?^# END KLOKAST VPN EGRESS\n?', '', path.read_text(), flags=re.M | re.S)
        write(path, content, 0o644)
    for name in OBSOLETE:
        (Path('/') / name).unlink(missing_ok=True)


def apply_files(value):
    for name, (content, mode) in ops_network.files(value).items():
        write(Path('/') / name, content, mode)
    clean_proxy()


def prepare(value):
    ops_network.validate(value)
    if socket.gethostname().split('.')[0] != value['box'] + '-ops':
        raise RuntimeError('controller migration target differs from this host')
    if MARKER.exists():
        if json.loads(MARKER.read_text()) != value: raise RuntimeError('installed controller network inputs differ')
        return {'stage': 'accepted', 'configuration': value}
    if (BASE / 'transaction.json').exists():
        old = read()
        if old['configuration'] != value: raise RuntimeError('resume the recorded network inputs before changing them')
        if old['stage'] != 'rolled-back': return old
    names = set(ops_network.files(value)) | set(OBSOLETE) | {'etc/gitconfig'}
    backups = {}
    for name in names:
        p = Path('/') / name
        if p.is_symlink(): raise RuntimeError('managed network path is a symlink: ' + name)
        backups[name] = {'content': base64.b64encode(p.read_bytes()).decode(), 'mode': p.stat().st_mode & 0o777} if p.exists() else None
    state = {'kind': 'klokast.ops-network.v1', 'stage': 'prepared', 'configuration': value,
             'identity': identity(), 'backups': backups,
             'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'deadline': int(time.time()) + 1200}
    save(state)
    # This one-shot boot guard restores old files before networking after an
    # interrupted migration. It holds no credentials and starts no daemon.
    write('/etc/init.d/ops-network-recovery', '#!/sbin/openrc-run\n'
          'description="Recover an interrupted controller network migration"\n'
          'depend() { need localmount; before networking; }\n'
          'start() { /usr/bin/python3 /usr/local/libexec/klokast/ops_network_guest.py boot-recover; }\n', 0o755)
    run(['rc-update', 'add', 'ops-network-recovery', 'boot'])
    # Permit the current route only until switch, while already protecting eth1.
    firewall = ops_network.firewall(value).replace(
        'type filter hook output priority 100; policy drop;',
        'type filter hook output priority 100; policy accept;')
    write(BASE / 'transition.nft', firewall)
    run(['/usr/sbin/nft', '-c', '-f', str(BASE / 'transition.nft')])
    run(['/usr/sbin/nft', '-f', str(BASE / 'transition.nft')])
    return state


def recovery_cleanup():
    run(['rc-update', 'del', 'ops-network-recovery', 'boot'], check=False)
    Path('/etc/init.d/ops-network-recovery').unlink(missing_ok=True)


def rollback(state, *, boot=False):
    if state['stage'] in ('accepted', 'rolled-back'): return state
    # ifdown needs the DHCP stanza to stop its client. Restore the old file
    # only after the uplink is down, or its DHCP client can recreate a route.
    if not boot:
        run(['/sbin/ifdown', 'eth1'], check=False)
    for name, saved in state['backups'].items():
        path = Path('/') / name
        if saved is None: path.unlink(missing_ok=True)
        else: write(path, base64.b64decode(saved['content']).decode(), saved['mode'])
    if not boot:
        run(['/usr/sbin/nft', '-f', '/etc/nftables.nft'])
        run(['/sbin/ip', '-4', 'addr', 'flush', 'dev', 'eth1'], check=False)
        run(['/sbin/ip', '-6', 'addr', 'flush', 'dev', 'eth1'], check=False)
        run(['/sbin/sysctl', '-q', '-w', 'net.ipv6.conf.eth0.disable_ipv6=0', 'net.ipv6.conf.eth0.accept_ra=1'])
        run(['/sbin/ip', '-4', 'route', 'replace', 'default', 'via', '192.168.125.254', 'dev', 'eth0'])
        run(['/usr/sbin/resolvconf', '-d', 'eth1.klokast'], check=False)
        run(['/etc/network/if-up.d/10-klokast-dns'], env=dict(os.environ, IFACE='eth0'))
        if Path('/etc/network/if-up.d/91-klokast-ops-ipv6').exists():
            run(['/etc/network/if-up.d/91-klokast-ops-ipv6'], env=dict(os.environ, IFACE='eth0'))
    state['stage'] = 'rolled-back'; save(state); recovery_cleanup()
    return state


def switch(state):
    if state['stage'] == 'accepted': return verify(state['configuration'], state['identity'])
    if state['stage'] == 'rolled-back': raise RuntimeError('prepare a retry after inspecting the rollback')
    if time.time() >= state['deadline']: raise RuntimeError('network migration deadline expired; roll back before retry')
    if not Path('/sys/class/net/eth1').exists(): raise RuntimeError('dom0 has not attached the controller uplink')
    if Path('/sys/class/net/eth1/address').read_text().strip() != state['configuration']['uplink']['mac']:
        raise RuntimeError('controller uplink MAC differs from its assigned interface')
    apply_files(state['configuration'])
    # Keep transition egress until DHCP succeeds. The final output policy below
    # closes the old route before acceptance.
    run(['/sbin/ifup', 'eth1'], check=False)
    deadline = min(time.monotonic() + 75, time.monotonic() + state['deadline'] - time.time())
    while not json.loads(run(['/sbin/ip', '-j', '-4', 'route', 'show', 'default', 'dev', 'eth1'])):
        if time.monotonic() >= deadline: raise RuntimeError('uplink has no DHCP default route; recovery is armed')
        time.sleep(1)
    run(['/sbin/sysctl', '-p', '/etc/sysctl.d/92-klokast-ops-uplink.conf'])
    run(['/sbin/ip', '-4', 'route', 'flush', 'default', 'dev', 'eth0'])
    run(['/sbin/ip', '-6', 'route', 'flush', 'dev', 'eth0'])
    run(['/usr/sbin/nft', '-f', '/etc/nftables.nft'])
    run(['/etc/network/if-up.d/10-klokast-dns'], env=dict(os.environ, IFACE='eth1'))
    state['stage'] = 'switched'; save(state)
    return verify(state['configuration'], state['identity'])


def verify(value, expected=None):
    ops_network.validate(value)
    defaults = {}
    for family in ('-4', '-6'):
        routes = json.loads(run(['/sbin/ip', '-j', family, 'route', 'show', 'default']))
        if any(row.get('dev') != 'eth1' for row in routes): raise RuntimeError('a controller default route still depends on another interface')
        defaults[family] = routes
    if not defaults['-4']: raise RuntimeError('controller uplink IPv4 default route is absent')
    if Path('/sys/class/net/eth1/address').read_text().strip() != value['uplink']['mac']:
        raise RuntimeError('uplink MAC differs')
    for name in ('/proc/sys/net/ipv4/ip_forward', '/proc/sys/net/ipv6/conf/all/forwarding'):
        if Path(name).read_text().strip() != '0': raise RuntimeError('controller forwarding must remain disabled')
    run(['/usr/sbin/nft', '-c', '-f', '/etc/nftables.nft'])
    for server in value['bootstrap_dns']: run(['nslookup', 'controlplane.tailscale.com', server], timeout=20)
    env = {k: v for k, v in os.environ.items() if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
    run(['curl', '--noproxy', '*', '--interface', 'eth1', '-sS', '--max-time', '20',
         '-o', '/dev/null', 'https://controlplane.tailscale.com/'], env=env)
    actual = identity() if expected is not None else None
    if expected is not None and actual != expected: raise RuntimeError('controller identity or authority changed during network migration')
    return {'stage': 'verified', 'defaults': defaults, 'identity': actual}


def watchdog():
    while True:
        with lock():
            state = read()
            if state['stage'] in ('accepted', 'rolled-back'): return
            if time.time() >= state['deadline']:
                rollback(state); return
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'switch', 'verify', 'accept', 'rollback', 'status', 'watchdog', 'boot-recover', 'converge'))
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0: raise RuntimeError('controller network changes require root through the active controller workflow')
    if args.action == 'watchdog': watchdog(); return
    with lock():
        if args.action == 'prepare': result = prepare(json.loads(args.config.read_text()))
        elif args.action == 'converge':
            if not MARKER.exists(): raise RuntimeError('run converge-ops-controller --network-only before normal controller convergence')
            value = json.loads(MARKER.read_text()); apply_files(value)
            run(['/usr/sbin/nft', '-f', '/etc/nftables.nft'])
            result = verify(value)
        elif args.action == 'verify':
            result = verify(json.loads(MARKER.read_text())) if MARKER.exists() else verify(read()['configuration'], read()['identity'])
        elif args.action == 'boot-recover':
            state = read(); result = rollback(state, boot=True) if state['stage'] != 'accepted' else state
        elif args.action == 'status': result = read() if (BASE / 'transaction.json').exists() else {'stage': 'absent'}
        else:
            state = read()
            if args.action == 'switch':
                try: result = switch(state)
                except Exception:
                    rollback(state)
                    raise
            elif args.action == 'rollback': result = rollback(state)
            else:
                result = verify(state['configuration'], state['identity'])
                write(MARKER, json.dumps(state['configuration'], sort_keys=True) + '\n')
                state['stage'] = 'accepted'; save(state); recovery_cleanup(); result['stage'] = 'accepted'
        # Never print backed-up file contents.
        print(json.dumps({k: v for k, v in result.items() if k != 'backups'}, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('ops-network: ' + str(error), file=sys.stderr); sys.exit(1)
