"""Bounded guest checks before and after one supervised VM replacement."""

import json
import re
import subprocess


ROOT_SCRIPT = '''set -eu
doas python3 - <<'PY'
import hashlib, json, os, pathlib, pwd, re, subprocess
P = pathlib.Path
def run(argv, timeout=20):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 65536:
        raise RuntimeError('guest check failed: ' + argv[0])
    return result.stdout
def sha(path):
    item = P(path)
    if not item.is_file() or item.is_symlink() or item.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError('machine identity file is missing or unsafe')
    return hashlib.sha256(item.read_bytes()).hexdigest()
def sha_optional(path):
    return sha(path) if P(path).exists() else None
def ranges(name):
    rows = [v.split(':') for v in P('/etc/' + name).read_text().splitlines() if v.startswith('neo:')]
    return [[int(v[1]), int(v[2])] for v in rows]
def packages():
    value = {}
    for block in P('/lib/apk/db/installed').read_text().strip().split('\\n\\n'):
        fields = {}
        for line in block.splitlines():
            if line.startswith(('P:', 'V:')):
                if line[0] in fields: raise RuntimeError('duplicate APK field')
                fields[line[0]] = line[2:]
        if set(fields) != {'P', 'V'} or fields['P'] in value: raise RuntimeError('ambiguous APK set')
        value[fields['P']] = fields['V']
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
tail = json.loads(run(['tailscale', 'status', '--json']))['Self']
run(['/usr/sbin/nft', '-c', '-f', '/etc/nftables.nft'])
user = pwd.getpwnam('neo')
result = {'hostname': os.uname().nodename, 'kernel': os.uname().release,
          'modules_present': P('/lib/modules/' + os.uname().release).is_dir(),
          'xen_uuid': P('/sys/hypervisor/uuid').read_text().strip(),
          'tailscale_id': tail['ID'], 'tailscale_public_key': tail['PublicKey'],
          'tailscale_online': tail['Online'],
          'ssh_host_sha256': {kind: sha('/etc/ssh/ssh_host_' + kind + '_key')
                              for kind in ('rsa', 'ecdsa', 'ed25519')},
          'runtime': {'uid': user.pw_uid, 'gid': user.pw_gid,
                      'subuid': ranges('subuid'), 'subgid': ranges('subgid')},
          'packages_sha256': packages(),
          'nftables_sha256': sha('/etc/nftables.nft'),
          'firewall_valid': bool(run(['/usr/sbin/nft', 'list', 'ruleset']).strip()),
          'config_sha256': {name: sha_optional('/' + name) for name in
                            ('etc/hostname','etc/hosts','etc/network/interfaces',
                             'etc/resolv.conf','etc/nftables.nft',
                             'etc/klokast/overlay-ipv6-input.nft',
                             'etc/klokast/app-resources/vm-input.nft',
                             'etc/containers/registries.conf',
                             'etc/containers/containers.conf.d/10-klokast-network.conf',
                             'etc/init.d/klokast-podman-runroot-cleanup')},
          'personalization': None, 'retained_uuid': None, 'retained_mounted': False,
          'retained_state_private': False}
record = P('/etc/klokast-personalization.json')
if record.exists():
    item = json.loads(record.read_text())
    result['personalization'] = {key: item.get(key) for key in
                                 ('operation_id','release_sha256','root_uuid','retained_uuid',
                                  'packages_unchanged','hostname')}
    line = run(['/bin/busybox','blkid','/dev/xvdb'])
    match = re.search(r'\\bUUID="([0-9a-f-]+)"', line)
    if not match or 'TYPE="ext4"' not in line: raise RuntimeError('retained disk is not ext4')
    result['retained_uuid'] = match.group(1)
    result['retained_mounted'] = any(fields[4] == '/srv/retained' and
                                     fields[2] == str(os.major(P('/dev/xvdb').stat().st_rdev)) + ':' +
                                                  str(os.minor(P('/dev/xvdb').stat().st_rdev))
                                     for fields in (line.split() for line in P('/proc/self/mountinfo').read_text().splitlines()))
    state = P('/srv/retained/platform-tailscale-state')
    result['retained_state_private'] = (state.is_file() and not state.is_symlink() and
                                        state.stat().st_mode & 0o777 == 0o600 and state.stat().st_size > 0)
print(json.dumps(result, sort_keys=True))
PY
'''

USER_SCRIPT = '''set -eu
python3 - <<'PY'
import json, subprocess
def run(argv):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30)
    if result.returncode or len(result.stdout) > 65536:
        raise RuntimeError('rootless Podman check failed')
    return result.stdout
info = json.loads(run(['podman', 'info', '--format', 'json']))
result = {'rootless_podman': info.get('host', {}).get('security', {}).get('rootless') is True}
for name, argv in {'containers': ['podman','ps','-a','--format','{{.ID}}'],
                   'images': ['podman','images','--format','{{.Id}}'],
                   'volumes': ['podman','volume','ls','--format','{{.Name}}']}.items():
    result[name] = [line for line in run(argv).splitlines() if line]
print(json.dumps(result, sort_keys=True))
PY
'''


def probe(host, timeout=90):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}-(?:dmz|iot)', host):
        raise RuntimeError('guest probe requires one fixed shared-VM name')
    result = {}
    for script in (ROOT_SCRIPT, USER_SCRIPT):
        command = subprocess.run(['tailscale', 'ssh', 'neo@' + host, 'sh', '-s'],
                                 input=script, capture_output=True, text=True, timeout=timeout)
        if command.returncode or len(command.stdout) > 65536:
            raise RuntimeError('guest identity or service probe failed: ' + host)
        value = json.loads(command.stdout)
        if not isinstance(value, dict) or set(result) & set(value):
            raise RuntimeError('guest probe returned ambiguous fields')
        result.update(value)
    return result
