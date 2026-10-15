#!/usr/bin/python3
"""Remove only the retired controller-facing router interface and its rules."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

BASE = Path('/var/lib/klokast/network/ops-uplink-router')
OVERLAY = {'etc/network/if-up.d/91-klokast-ops-ipv6', 'etc/sysctl.d/91-klokast-ops-ipv6.conf',
           'etc/dnsmasq.d/91-klokast-ops-ipv6.conf'}
FIXED = {'etc/network/interfaces', 'etc/dhcpcd.conf', 'etc/dnsmasq.conf', 'etc/nftables.nft',
         'etc/klokast/overlay-ipv6.nft', 'etc/klokast/app-resources/router-forward.nft'} | OVERLAY


def transform(name, text):
    if name in OVERLAY: return None
    if name == 'etc/klokast/overlay-ipv6.nft': return '# Ops uses its own uplink.\n'
    if name == 'etc/network/interfaces':
        # eth6 is the last, dedicated controller NIC. Do not renumber workloads.
        text = re.sub(r'(?m)^auto eth6\n', '', text)
        text = re.sub(r'(?m)^iface eth6[^\n]*\n(?:[ \t]+[^\n]*\n|\n)*', '', text)
        if re.search(r'\beth6\b', text): raise RuntimeError('unexpected eth6 networking stanza')
        return text
    if name == 'etc/dhcpcd.conf':
        return re.sub(r'(?m)^(denyinterfaces[^\n]*)\beth6\b ?', r'\1', text)
    if name == 'etc/dnsmasq.conf':
        return ''.join(line for line in text.splitlines(True) if line.strip() != 'interface=eth6')
    text = text.replace('flush ruleset', 'destroy table inet filter\ndestroy table ip nat')
    lines = []
    for line in text.splitlines(True):
        if re.search(r'(?:iifname|oifname) "eth6"', line): continue
        if '192.168.125.10' in line:
            raise RuntimeError('unexpected controller rule without its exact retired interface')
        # Shared nftables interface sets retain all workload elements.
        line = re.sub(r',\s*"eth6"|"eth6"\s*,\s*', '', line)
        if 'eth6' in line and not line.lstrip().startswith('#'):
            raise RuntimeError('unexpected controller nftables rule; preserve and inspect it')
        lines.append(line)
    return ''.join(lines)


def names(root=Path('/')):
    result = set(FIXED)
    for p in (root / 'etc/klokast/app-resources/router-forward.d').glob('*.nft'):
        result.add(str(p.relative_to(root)))
    return result


def sha(text): return hashlib.sha256(text.encode()).hexdigest()


def put(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.ops-network')
    with tmp.open('w') as stream:
        os.fchmod(stream.fileno(), mode); stream.write(content); stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=45)
    if p.returncode: raise RuntimeError('router network command failed: ' + ' '.join(argv[:3]))


def snapshot():
    path = BASE / 'transaction.json'
    if path.exists(): return json.loads(path.read_text())
    files = {}
    for name in sorted(names()):
        p = Path('/') / name
        if p.is_symlink(): raise RuntimeError('router configuration path is a symlink: ' + name)
        if p.exists():
            before = p.read_text(); after = transform(name, before)
            files[name] = {'before': before, 'after': after, 'mode': p.stat().st_mode & 0o777}
    value = {'kind': 'klokast.ops-router-network.v1', 'stage': 'prepared', 'files': files}
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    put(path, json.dumps(value, sort_keys=True) + '\n')
    return value


def apply(state):
    for name, item in state['files'].items():
        p = Path('/') / name
        actual = p.read_text() if p.exists() else None
        if actual not in (item['before'], item['after']):
            raise RuntimeError('router configuration changed after snapshot: ' + name)
    try:
        for name, item in state['files'].items():
            p = Path('/') / name
            if item['after'] is None: p.unlink(missing_ok=True)
            else: put(p, item['after'], item['mode'])
        run(['/usr/sbin/nft', '-c', '-f', '/etc/nftables.nft'])
        run(['/usr/sbin/dnsmasq', '--test'])
        run(['/usr/sbin/nft', '-f', '/etc/nftables.nft'])
        run(['/sbin/rc-service', 'dnsmasq', 'restart'])
    except Exception:
        for name, item in state['files'].items(): put(Path('/') / name, item['before'], item['mode'])
        run(['/usr/sbin/nft', '-f', '/etc/nftables.nft'])
        run(['/sbin/rc-service', 'dnsmasq', 'restart'])
        raise
    state['stage'] = 'applied'; put(BASE / 'transaction.json', json.dumps(state, sort_keys=True) + '\n')
    return state


def evidence(state):
    hashes = {}
    for name, item in state['files'].items():
        actual = (Path('/') / name).read_text() if (Path('/') / name).exists() else None
        expected = item['after'] if state['stage'] == 'applied' else item['before']
        if actual != expected: raise RuntimeError('router network file differs from the recorded phase: ' + name)
        hashes[name] = {'before': sha(item['before']), 'after': None if item['after'] is None else sha(item['after'])}
    return {'kind': state['kind'], 'stage': state['stage'], 'files': hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'apply', 'verify'))
    parser.add_argument('--box', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != args.box + '-router':
        raise RuntimeError('run router network cleanup on its selected router as root')
    state = snapshot()
    if args.action == 'apply': state = apply(state)
    print(json.dumps(evidence(state), sort_keys=True))


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('ops-router-network: ' + str(error), file=sys.stderr); sys.exit(1)
