"""Adopt only the exact completed infrastructure assignment into common updates."""
from pathlib import Path
import hashlib


def recovered_service(engine, box, role, assignment):
    """Resolve the accepted source of a recovered attempt without reseeding it."""
    if assignment.get('profile') is None and assignment.get('release_sha256') is None:
        return None  # Recovery before first migration has no accepted service.
    if assignment.get('profile') != role+'-alpine-v1':
        raise RuntimeError('recovered infrastructure source has another image profile')
    matches=[]
    for path in (engine.t.BASE/'operations').glob('*/service.json'):
        record=engine.read(path)
        if record.get('new_uuid') != assignment['vm_uuid']:continue
        work=path.parent;request=engine.read(work/'request.json');journal=engine.read(work/'journal.json')
        if (record.get('kind') != 'klokast.vm-service-update.v1' or
                request.get('kind') != engine.t.RETAINED_KIND or
                record.get('box') != box or record.get('role') != role or
                request.get('box') != box or request.get('role') != role or
                record.get('operation_id') != work.name or request.get('operation_id') != work.name or
                journal.get('stage') != 'complete' or journal.get('request_sha256') != engine.t.digest(request) or
                record.get('reboot_verification',{}).get('status') != 'complete' or
                request.get('new_uuid') != assignment['vm_uuid'] or
                record.get('new_disks') != assignment['disks'] or request.get('new_disks') != assignment['disks'] or
                request.get('release_sha256') != assignment['release_sha256'] or
                request.get('image_profile') != assignment['profile']):
            raise RuntimeError('recovered infrastructure source differs from its accepted service evidence')
        matches.append(record)
    if len(matches) != 1:
        raise RuntimeError('recovered infrastructure source requires one exact accepted service record')
    return matches[0]


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
