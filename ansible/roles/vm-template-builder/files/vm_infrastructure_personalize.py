"""Approved image personalization used by creation and reconstruction."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess


def run(argv, *, input=None):
    result = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError('guest finalization command failed: ' + argv[0] + ': ' + result.stderr[-1000:])
    return result.stdout.strip()


def validate(value):
    replacement = value.get('replacement')
    resolvers = value.get('bootstrap_dns', ['1.1.1.1', '8.8.8.8'])
    try:
        if (not isinstance(resolvers, list) or len(resolvers) != 2 or
                any(not isinstance(v, str) or not ipaddress.IPv4Address(v).is_global for v in resolvers) or
                resolvers[0] == resolvers[1]):
            raise ValueError('invalid DNS policy')
    except ValueError:
        raise RuntimeError('bootstrap DNS requires two distinct public IPv4 resolvers') from None
    base = {k: v for k, v in value.items() if k not in ('replacement', 'bootstrap_dns', 'routing_profile', 'vpn_inputs')}
    if (set(base) != {'kind', 'box', 'role', 'address', 'gateway', 'bridge', 'bootstrap_source', 'public_key', 'agent_uid', 'agent_gid'} or
            value['kind'] != 'klokast.infrastructure-config.v1' or value['role'] not in ('air', 'ops', 'vpn-egress') or
            not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', value['box'])):
        raise RuntimeError('invalid infrastructure guest configuration')
    if value['bridge'] != {'air': 'br-usr', 'ops': 'br-ops', 'vpn-egress': 'br-dmz'}[value['role']]:
        raise RuntimeError('infrastructure guest bridge differs from its Platform zone')
    address = ipaddress.IPv4Interface(value['address'])
    if ipaddress.IPv4Address(value['gateway']) not in address.network:
        raise RuntimeError('guest gateway is outside its assigned subnet')
    ipaddress.IPv4Address(value['bootstrap_source'])
    if (not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+(?: [^\r\n]+)?', value['public_key']) or
            any(type(value[k]) is not int or not 1001 <= value[k] <= 60000 for k in ('agent_uid', 'agent_gid'))):
        raise RuntimeError('invalid bootstrap public key or runner numeric ownership')
    if 'vpn_inputs' in value:
        pointer=value['vpn_inputs']
        if (value['role']!='vpn-egress' or not isinstance(pointer,dict) or
                set(pointer)!={'input_snapshot','inputs_sha256'} or
                not re.fullmatch('[0-9a-f]{24}',pointer['input_snapshot']) or
                not re.fullmatch('[0-9a-f]{64}',pointer['inputs_sha256'])):
            raise RuntimeError('invalid frozen VPN creation input assignment')
    if replacement is not None:
        if (value['role'] != 'ops' or not isinstance(replacement, dict) or
                set(replacement) != {'active_box', 'old_root_partition', 'accounts'} or
                not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', replacement['active_box']) or
                replacement['active_box'] == value['box'] or replacement['old_root_partition'] not in ('', '3')):
            raise RuntimeError('replacement requires the peer active controller and a recorded root layout')
        accounts = replacement['accounts']
        if (not isinstance(accounts, dict) or set(accounts) != {'smith', 'minion'} or
                any(not isinstance(v, list) or len(v) != 2 or any(type(i) is not int or not 1001 <= i <= 60000 for i in v)
                    for v in accounts.values()) or accounts['smith'][0] == accounts['minion'][0] or
                accounts['smith'][1] == accounts['minion'][1]):
            raise RuntimeError('replacement account numeric identities are invalid')
    return value


def put(name, content, mode=0o644):
    path = Path('/') / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(mode)


def account(name, uid, gid, wheel=False, shell='/bin/ash'):
    try:
        current = pwd.getpwnam(name)
    except KeyError:
        run(['addgroup', '-g', str(gid), name])
        run(['adduser', '-D', '-u', str(uid), '-G', name, '-s', shell, name])
        current = pwd.getpwnam(name)
    if (current.pw_uid, current.pw_gid, current.pw_dir) != (uid, gid, '/home/' + name):
        raise RuntimeError('existing guest account ownership differs: ' + name)
    # BusyBox adduser -D uses "!". OpenSSH on Linux rejects that account even
    # for public keys. "*" disables passwords without disabling key-only SSH.
    # The root account remains locked and is never passed to this function.
    run(['chpasswd', '-e'], input=name + ':*\n')
    if wheel:
        run(['addgroup', name, 'wheel'])


def finalize(value, digest):
    marker = json.loads(Path('/etc/klokast-template.json').read_text())
    if marker['profile'] != value['role'] + '-alpine-v1':
        raise RuntimeError('cloned image profile differs from infrastructure role')
    previous = Path('/etc/klokast-infrastructure.json')
    if previous.exists() and json.loads(previous.read_text()) != {'config_sha256': digest, 'configuration': value}:
        raise RuntimeError('refusing to change an existing guest assignment')
    # Raw ext4 roots grow inside the VM; dom0 never interprets the filesystem.
    run(['resize2fs', '/dev/xvda'])
    name = value['box'] + '-' + value['role']
    account('neo', 1000, 1000, wheel=True)
    if value['role'] == 'air':
        account('agent', value['agent_uid'], value['agent_gid'], shell='/bin/bash')
    elif value['role'] == 'ops':
        accounts = value.get('replacement', {}).get('accounts', {'smith': [1001, 1001]})
        account('smith', *accounts['smith'], wheel=True)
        if 'minion' in accounts: account('minion', *accounts['minion'])
    put('etc/hostname', name + '\n')
    put('etc/hosts', '127.0.0.1 localhost ' + name + '\n::1 localhost\n')
    put('etc/network/interfaces', 'auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet static\n'
        '    address ' + value['address'] + '\n    gateway ' + value['gateway'] + '\n')
    resolvers = value.get('bootstrap_dns', ['1.1.1.1', '8.8.8.8'])
    dns = ''.join('nameserver ' + server + '\n' for server in resolvers)
    put('etc/resolv.conf', dns)
    put('etc/network/if-up.d/10-klokast-dns', '#!/bin/sh\nset -eu\n'
        '[ "${IFACE:-}" = eth0 ] || exit 0\n'
        '/usr/sbin/resolvconf -u\n'
        '/usr/sbin/resolvconf -a eth0.klokast <<\'DNS\'\n' + dns + 'DNS\n', 0o755)
    put('etc/doas.d/doas.conf', 'permit nopass :wheel\n', 0o600)
    put('home/neo/.ssh/authorized_keys', value['public_key'] + '\n', 0o600)
    Path('/home/neo/.ssh').chmod(0o700)
    for path in (Path('/home/neo/.ssh'), Path('/home/neo/.ssh/authorized_keys')):
        os.chown(path, 1000, 1000)
    put('etc/ssh/sshd_config.d/10-klokast-key-only.conf', 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n'
        'PubkeyAuthentication yes\nPermitRootLogin no\nAllowUsers neo\n')
    # This temporary underlay SSH exception is removed after fresh enrollment.
    put('etc/nftables.nft', '''#!/usr/sbin/nft -f
flush ruleset
table inet klokast_vm_filter {
 chain input {
  type filter hook input priority 100; policy drop;
  ct state invalid drop
  ct state { established, related } accept
  iifname "lo" accept
  iifname "tailscale0" accept
  ip protocol icmp accept
  ip6 nexthdr icmpv6 accept
  udp dport 41641 accept
  ip saddr ''' + value['bootstrap_source'] + ''' tcp dport 22 accept comment "platform-bootstrap-ssh"
 }
 chain forward { type filter hook forward priority 100; policy drop; }
 chain output { type filter hook output priority 100; policy accept; }
}
''')
    for service in ('networking', 'nftables', 'sshd', 'tailscale'):
        run(['rc-update', 'add', service, 'default'])
    put('etc/klokast-infrastructure.json', json.dumps({'config_sha256': digest, 'configuration': value}, sort_keys=True) + '\n', 0o600)


