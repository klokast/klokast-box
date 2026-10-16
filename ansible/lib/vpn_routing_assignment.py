"""Read or adopt routing metadata in existing protected dom0 VM assignments.

Invoked through the controller's existing privileged remote terminal. This does
not authorize clients, start guests, or replace VM operation state.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys

BASE = Path('/mnt/dom0_data/klokast-infrastructure/vpn-egress')
TRANSACTIONS = Path('/mnt/dom0_data/klokast-vm-updates')


def read(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise RuntimeError('routing assignment path has unsafe ownership or permissions')
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeError('routing assignment must be a bounded regular file')
    return json.loads(path.read_text())


def initial(box):
    path = BASE / 'assignment.json'
    if not path.exists(): return None, None
    state = read(path)
    if (state.get('kind') != 'klokast.infrastructure-assignment.v1' or
            state.get('box') != box or state.get('role') != 'vpn-egress'):
        raise RuntimeError('routing assignment belongs to another VM')
    return path, state


def selection(box):
    path, state = initial(box)
    old_profile = state.get('routing_profile') if state else None
    result = {'state': 'absent' if state is None else state['stage'], 'profile': old_profile,
              'uuid': state.get('uuid') if state else None,
              'creation_configuration': state.get('configuration') if state else None,
              'creation_image': state.get('operation_id') if state else None}
    pointer = TRANSACTIONS / 'active/vpn-egress.json'
    if not pointer.exists(): return path, state, result
    pointer_value = read(pointer)
    op = pointer_value.get('operation_id', '')
    if not re.fullmatch('[0-9a-f]{24}', op): raise RuntimeError('invalid VPN operation pointer')
    work = TRANSACTIONS / 'operations' / op
    request = read(work / 'request.json')
    import hashlib
    if hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() != pointer_value.get('request_sha256'):
        raise RuntimeError('VPN operation pointer differs from its protected request')
    if request.get('box') != box or request.get('role') != 'vpn-egress':
        raise RuntimeError('VPN operation belongs to another VM')
    journal = read(work / 'journal.json')
    stage = journal['stage']
    service_path = work / 'service.json'
    service = read(service_path) if service_path.exists() else None
    if service and (service.get('kind') != 'klokast.vm-service-update.v1' or
                    service.get('operation_id') != op or service.get('box') != box or service.get('role') != 'vpn-egress'):
        raise RuntimeError('VPN service record differs from its operation')
    result.update(state=stage, operation_id=op, creation_configuration=None, creation_image=None)
    if service and stage in ('stopped', 'starting', 'booted', 'tested', 'accepted', 'complete'):
        profile = service['requested_configuration'].get('routing_profile', service.get('routing_profile'))
        result.update(profile=profile, uuid=service['new_uuid'])
        return service_path, service, result
    if service and stage in ('armed', 'adopted', 'recovered'):
        result['profile'] = service['requested_configuration'].get('before', {}).get('routing_profile', service.get('routing_profile', old_profile))
        result['uuid'] = service['old_uuid']
        return service_path, service, result
    return path, state, result


def air_bootstrap(box):
    pointer=TRANSACTIONS/'active/air.json'
    if pointer.exists() or pointer.is_symlink():
        sys.path.insert(0,'/usr/local/sbin')
        import vm_update_engine as engine
        with engine.current_assignment('air') as tx:
            if tx is None or tx.request['box'] != box:
                raise RuntimeError('runner common assignment belongs to another box')
            record=read(tx.work/'service.json')
            if record.get('box')!=box or record.get('role')!='air' or record.get('operation_id')!=tx.work.name:
                raise RuntimeError('runner common service record differs')
            config=record['requested_configuration']
            selected=config['infrastructure_creation'] if tx.journal['stage'] in ('stopped','starting','booted','tested','accepted','complete') else config['before']
            return {'bootstrap_dns':selected['bootstrap_dns']}
    work=BASE.parent/'air'
    if not (work/'assignment.json').exists(): return {'bootstrap_dns': None}
    state=read(work/'assignment.json')
    if (state.get('kind') != 'klokast.infrastructure-assignment.v1' or
            state.get('box') != box or state.get('role') != 'air'):
        raise RuntimeError('runner DNS assignment belongs to another VM')
    config=state.get('configuration')
    if config is None:
        # Older infrastructure assignments retain their exact input slot.
        path=work/'config.slot'
        for parent in (path, *path.parents):
            info=parent.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise RuntimeError('runner input slot has unsafe ownership')
        if not path.is_file() or path.stat().st_size > 1024*1024:
            raise RuntimeError('runner input slot is not bounded')
        raw=path.read_bytes().split(b'\0',1)[0]
        import hashlib
        if hashlib.sha256(raw).hexdigest()!=state['config_sha256']:
            raise RuntimeError('runner input slot differs from its assignment')
        config=json.loads(raw)
    # Missing DNS is the historical v1 finalizer's fixed default, not current policy.
    return {'bootstrap_dns': config.get('bootstrap_dns', ['1.1.1.1','8.8.8.8'])}


def main(value):
    box = value.get('box', '')
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or socket.gethostname().split('.')[0] != box+'-dom0' or os.geteuid() != 0:
        raise RuntimeError('routing assignment requires its selected dom0')
    action = value.get('action')
    if action == 'read-air': return air_bootstrap(box)
    if action not in ('read', 'adopt'): raise RuntimeError('unsupported routing assignment action')
    # Use the existing VM transaction lock when it exists. Never create a new lock namespace.
    lock = TRANSACTIONS / 'transaction.lock'
    if not lock.exists(): lock = BASE / 'operation.lock'
    descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW) if lock.exists() else None
    try:
        if descriptor is not None: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path, state, result = selection(box)
        if action == 'adopt':
            if result['state'] not in ('ready', 'complete', 'adopted', 'recovered') or state is None:
                raise RuntimeError('routing adoption requires an existing stable VM assignment')
            if result['uuid'] != value.get('uuid'):
                raise RuntimeError('routing adoption guest UUID differs from its assignment')
            profile = value['profile']
            if result['profile'] is not None and result['profile'] != profile:
                raise RuntimeError('installed routing profile is immutable; replace the VM to change it')
            if result['profile'] is None:
                state['routing_profile'] = profile
                import tempfile
                fd, name = tempfile.mkstemp(prefix='.routing-', dir=path.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(fd, 'w') as stream:
                        json.dump(state, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, path)
                    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try: os.fsync(parent)
                    finally: os.close(parent)
                finally:
                    if temporary.exists(): temporary.unlink()
                result['profile'] = profile
        return result
    finally:
        if descriptor is not None: os.close(descriptor)


if __name__ == '__main__':
    try:
        print(json.dumps(main(json.load(sys.stdin)), sort_keys=True))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print('VPN routing assignment: '+str(error), file=sys.stderr)
        raise SystemExit(1)
