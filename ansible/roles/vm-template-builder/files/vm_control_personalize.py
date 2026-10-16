"""Role configuration for ops and air using the common retained root finalizer."""
import hashlib
import json
import os
from pathlib import Path
import re

import vm_infrastructure_personalize as base
import vm_infrastructure_storage as storage


def validate(value):
    if set(value) != {'kind', 'box', 'role', 'root_partition', 'source_files', 'accounts',
                      'infrastructure_creation', 'infrastructure_storage'}:
        raise RuntimeError('infrastructure finalizer inputs are incomplete')
    if value['kind'] != 'klokast.vm-service-finalize.v1' or value['role'] not in ('ops', 'air'):
        raise RuntimeError('invalid infrastructure finalizer role')
    public = base.validate(value['infrastructure_creation'])
    data = value['infrastructure_storage']
    storage.contract(data)
    if (public['role'] != value['role'] or public['box'] != value['box'] or
            data['role'] != value['role'] or data['box'] != value['box'] or
            data['accounts'] != value['accounts'] or value['root_partition'] not in ('', '3') or
            not re.fullmatch('[0-9a-f]{24}', data.get('operation', '')) or
            any(type(data.get(k)) is not bool for k in ('migrate', 'initialize'))):
        raise RuntimeError('infrastructure finalizer target or storage differs')
    if not isinstance(value['source_files'], dict) or set(value['source_files']) != {'etc/passwd', 'etc/group'}:
        raise RuntimeError('infrastructure finalizer requires source account evidence')
    if any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in value['source_files'].values()):
        raise RuntimeError('invalid infrastructure source checksum')
    return value


def reconstruct(value, source):
    public = value['infrastructure_creation']; data = value['infrastructure_storage']
    users = {p[0]: [int(p[2]), int(p[3])] for row in (source/'etc/passwd').read_text().splitlines()
             if (p := row.split(':'))[0] in value['accounts']}
    if users != value['accounts']:
        raise RuntimeError('source infrastructure numeric accounts changed')
    base.finalize(public, hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest())
    shadow = {r.split(':')[0]: r.split(':')[1] for r in (source/'etc/shadow').read_text().splitlines()}
    for name in value['accounts']:
        base.run(['chpasswd', '-e'], input=name + ':' + shadow[name] + '\n')
    if value['role'] == 'ops':
        base.put('etc/klokast/controller-ha.json', json.dumps({
            'schema_version': 1, 'configured': True, 'box': value['box'],
            'hostname': value['box']+'-ops', 'role': 'standby', 'active_box': data['active_box']})+'\n')
        base.put('etc/klokast/deployment.json', '{"schema_version":1,"lifecycle":"development"}\n')
        for name in ('home/smith/.ssh', 'home/smith/private', 'home/smith/private/klokast'):
            p=Path('/')/name; p.mkdir(parents=True,exist_ok=True); p.chmod(0o700)
            os.chown(p,*value['accounts']['smith'])
    else:
        # Account creation installs only image-owned skeleton files. A home bind
        # must not hide unexpected data already present in a candidate image.
        home=Path('/home/agent')
        for path in home.iterdir():
            skeleton=Path('/etc/skel')/path.name
            if path.is_symlink() or not path.is_file() or not skeleton.is_file() or path.read_bytes()!=skeleton.read_bytes():
                raise RuntimeError('runner image contains unexpected home state')
            path.unlink()
        base.run(['rc-update','del','sshd','default'])
    Path('/home/neo/.ssh/authorized_keys').unlink(missing_ok=True)
    identity=storage.finalize(data,source)
    return {'infrastructure_storage':identity}
