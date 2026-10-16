"""Adopt only the exact completed infrastructure assignment into common updates."""
from pathlib import Path
import hashlib


def source(engine, box, role):
    if role not in ('ops', 'air'): raise RuntimeError('unsupported infrastructure source')
    base = engine.t.BASE.parent / 'klokast-infrastructure' / role
    assignment = engine.read(base / 'assignment.json')
    if assignment.get('box') != box or assignment.get('role') != role or assignment.get('stage') != 'ready':
        raise RuntimeError('infrastructure source requires its completed protected assignment')
    if role == 'ops' and (base / 'replacement.json').exists():
        pending = engine.read(base / 'replacement.json')
        if (pending.get('stage') != 'accepted' or pending.get('uuid') != assignment.get('uuid') or
                pending.get('reboot_verification', {}).get('status') != 'complete'):
            raise RuntimeError('finish the legacy controller replacement and reboot verification before migration')
    root = assignment.get('root_lv', '/dev/vg0/lv_' + role)
    work = Path(assignment.get('work', str(base)))
    config = work / (role + '.cfg')
    boot = engine.checksum(config)
    if assignment.get('boot_sha256', boot) != boot:
        raise RuntimeError('protected infrastructure boot checksum differs')
    if engine.t.secure(engine.t.XEN / (role + '.cfg')).read_bytes() != engine.t.secure(config).read_bytes():
        raise RuntimeError('installed infrastructure boot file differs from its assignment')
    for name in ('kernel','initramfs'):
        artifact=engine.t.Native().artifact(str(work/name))
        if artifact != assignment['artifacts'][name]:
            raise RuntimeError('protected infrastructure boot artifact differs')
    disks = {root: engine.owned(root)}
    if disks[root]['uuid'] != assignment['lv_uuid']:
        raise RuntimeError('infrastructure root LV identity differs')
    data = assignment.get('data_volume')
    if data:
        if role != 'ops' or data.get('stage') != 'ready' or data.get('resize_bytes'):
            raise RuntimeError('infrastructure data attachment is incomplete')
        disks[data['path']] = engine.owned(data['path'], {'uuid': data['lv_uuid'], 'bytes': data['bytes']}, data['tag'])
    parsed, paths = engine.t.configuration(config.read_text(), role, assignment['uuid'])
    if set(paths) != set(disks): raise RuntimeError('infrastructure disks differ from the protected assignment')
    return {'assignment_sha256': engine.t.digest(assignment), 'uuid': assignment['uuid'],
            'disks': disks, 'boot_sha256': boot,
            'data_uuid': data['filesystem_uuid'] if data else None}


def require(engine, box, role, config, old_uuid, disks, boot):
    expected = config.get('infrastructure_source')
    if expected is not None:
        if not config.get('migrate_root_data') or source(engine, box, role) != expected:
            raise RuntimeError('infrastructure migration source changed or was not selected explicitly')
        if expected['uuid'] != old_uuid or expected['disks'] != disks or expected['boot_sha256'] != boot:
            raise RuntimeError('infrastructure migration no longer selects its assigned disks and boot file')
    else:
        with engine.t.current_assignment(role, lock_held=True) as tx:
            stage = tx.journal['stage'] if tx else None
            side = 'new' if stage == 'complete' else 'old' if stage == 'recovered' else None
            if (side is None or tx.request[side + '_uuid'] != old_uuid or
                    tx.request[side + '_disks'] != disks or
                    hashlib.sha256(tx.assignment_content(side)).hexdigest() != boot):
                raise RuntimeError('infrastructure replacement requires its complete or recovered common assignment')
    if len(disks) == 2 and config['before']['retained_uuid'] is None:
        raise RuntimeError('infrastructure data attachment has no verified filesystem identity')
