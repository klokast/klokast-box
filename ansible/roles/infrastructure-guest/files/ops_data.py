"""Attach one retained controller volume under the existing operation locks."""
import json
import os
from pathlib import Path
import uuid

SIZE = 1024**3
DEVICE = '/dev/vg0/lv_ops_data'


def require_no_dom0_reference(driver):
    device = Path(DEVICE).stat().st_rdev
    identity = f'{os.major(device)}:{os.minor(device)}'
    if any(row.split()[2] == identity for row in Path('/proc/self/mountinfo').read_text().splitlines()):
        raise RuntimeError('controller data volume is mounted in dom0; refuse attachment')
    if any((Path('/sys/dev/block') / identity / 'holders').iterdir()):
        raise RuntimeError('controller data volume has device-mapper holders; refuse attachment')
    for path in Path('/sys/block').glob('loop*/loop/backing_file'):
        backing = '/' + path.read_text().strip().lstrip('/')
        if Path(backing).exists() and driver.same_device(backing, DEVICE):
            raise RuntimeError('controller data volume has a loop reference; refuse attachment')


def require_exclusive(driver, current):
    for guest in json.loads(driver.run(['xl', 'list', '-l']).stdout):
        for disk in guest.get('config', {}).get('disks', []):
            device = disk.get('pdev_path')
            if device and driver.same_device(device, DEVICE) and guest.get('domid') != current['domid']:
                raise RuntimeError('controller data volume is attached to another live guest')


def verify(infra, value):
    if (value.get('path') != DEVICE or type(value.get('bytes')) is not int or value['bytes'] < SIZE or
            value.get('stage') not in ('allocated', 'attached', 'ready')):
        raise RuntimeError('controller data volume record is invalid')
    uuid.UUID(value['filesystem_uuid'])
    info = infra.lv_info(Path(DEVICE))
    if (info is None or info['lv_uuid'] != value.get('lv_uuid') or
            value['tag'] not in info['lv_tags'].split(',') or int(float(info['lv_size'])) not in {value['bytes'], value.get('resize_bytes', value['bytes'])}):
        raise RuntimeError('controller data LV identity differs; preserve it and reconcile')
    return info


def save(driver, state, pending):
    driver.write(driver.BASE / 'assignment.json', state)
    if pending is not None:
        # Accepted replay must keep the data attachment and revised boot file.
        pending.update(data_volume=state['data_volume'], boot_sha256=state['boot_sha256'])
        driver.write(driver.BASE / 'replacement.json', pending)


def attach(driver, box, complete=False, evidence=None, dry_run=False, size_gib=1):
    if type(size_gib) is not int or size_gib not in (1, 50):
        raise RuntimeError('controller data setup supports the existing size or the 50 GiB migration size')
    path = driver.BASE / 'assignment.json'
    state = driver.read(path)
    pending_path = driver.BASE / 'replacement.json'
    pending = driver.read(pending_path) if pending_path.exists() else None
    if (state.get('box') != box or state.get('role') != 'ops' or state.get('stage') != 'ready' or
            (pending and (pending['stage'] != 'accepted' or pending['uuid'] != state['uuid'] or
                          pending.get('reboot_verification', {}).get('status') in ('required', 'pending')))):
        raise RuntimeError('finish controller replacement and reboot verification before adding retained data')
    # Normalize the first-install assignment only from its protected files.
    state.setdefault('root_lv', '/dev/vg0/lv_ops')
    state.setdefault('root_partition', '')
    state.setdefault('work', str(driver.BASE))
    work = Path(state['work'])
    cfg = work / 'ops.cfg'
    state.setdefault('boot_sha256', driver.checksum(cfg))
    driver.require_disk(state)
    current = driver.domain('ops')
    if current is None: raise RuntimeError('retained data setup requires the running assigned controller')
    driver.require_identity(current, state['uuid'])
    value = state.get('data_volume')
    if value and value.get('lv_uuid'):
        verify(driver.infra, value)
        require_exclusive(driver, current)
    if value and value['stage'] == 'ready':
        driver.require_boot(state); driver.require_live(state)
        requested = max(value['bytes'], value.get('resize_bytes', 0), size_gib * SIZE)
        if requested > value['bytes'] or value.get('resize_bytes'):
            if value.get('resize_bytes', requested) != requested:
                raise RuntimeError('resume the recorded controller data resize')
            verify(driver.infra, value); require_no_dom0_reference(driver)
            if dry_run: return {'changed': True, 'data_volume': dict(value, resize_bytes=requested)}
            free = float(driver.run(['vgs', '--noheadings', '--units', 'b', '--nosuffix', '-o', 'vg_free', 'vg0']).stdout.strip())
            info = driver.infra.lv_info(Path(DEVICE))
            actual = int(float(info['lv_size']))
            if free < requested - actual: raise RuntimeError('insufficient capacity to grow controller retained storage')
            value['resize_bytes'] = requested; save(driver, state, pending)
            if actual < requested:
                driver.run(['lvextend', '--yes', '--size', str(requested)+'B', DEVICE])
            verify(driver.infra, value)
            driver.run(['vgcfgbackup', 'vg0']); driver.install_boot(work)
            # Keep the resize intent until the guest filesystem check completes.
            if complete:
                if driver.read(evidence) != {'box': box, 'filesystem_uuid': value['filesystem_uuid'], 'mounted': True}:
                    raise RuntimeError('controller resized data mount verification differs')
                value['bytes'] = requested; value.pop('resize_bytes', None)
                save(driver, state, pending)
            return {'changed': True, 'data_volume': value}
        if pending is not None and (pending.get('data_volume') != value or pending.get('boot_sha256') != state['boot_sha256']):
            if not dry_run: save(driver, state, pending)
        return {'changed': False, 'data_volume': value}
    if value is None:
        driver.require_boot(state); driver.require_live(state)
        if driver.infra.lv_info(Path(DEVICE)) is not None:
            raise RuntimeError('unowned controller data LV exists; refuse adoption or formatting')
        free = float(driver.run(['vgs', '--noheadings', '--units', 'b', '--nosuffix', '-o', 'vg_free', 'vg0']).stdout.strip())
        if free < size_gib * SIZE: raise RuntimeError('controller retained data requires sufficient free LVM capacity')
        boot = driver.config_values(cfg)
        live_boot = driver.config_values(driver.XEN / 'ops.cfg')
        if boot['disk'] != live_boot['disk'] or live_boot.get('uuid', state['uuid']) != state['uuid']:
            raise RuntimeError('controller boot attachment differs from its assignment')
        value = {'path': DEVICE, 'bytes': size_gib * SIZE, 'filesystem_uuid': str(uuid.uuid4()),
                 'tag': 'klokast-ops-data-' + uuid.uuid4().hex[:24], 'lv_uuid': None,
                 'stage': 'allocated', 'old_boot_sha256': state['boot_sha256'],
                 'old_live_boot_sha256': driver.checksum(driver.XEN / 'ops.cfg')}
        state['data_volume'] = value
        if dry_run: return {'changed': True, 'data_volume': value}
        save(driver, state, pending)
    if dry_run: return {'changed': False, 'data_volume': value}
    info = driver.infra.lv_info(Path(DEVICE))
    if info is None:
        if value['lv_uuid'] is not None or value['stage'] != 'allocated':
            raise RuntimeError('recorded retained controller volume disappeared')
        driver.run(['lvcreate', '--yes', '--size', str(value['bytes']) + 'B', '--name', 'lv_ops_data',
                    '--addtag', value['tag'], 'vg0'])
        info = driver.infra.lv_info(Path(DEVICE))
    if value['lv_uuid'] is None and value['tag'] in info['lv_tags'].split(','):
        value['lv_uuid'] = info['lv_uuid']; save(driver, state, pending)
    verify(driver.infra, value)
    require_no_dom0_reference(driver)
    require_exclusive(driver, current)
    attached = current['config'].get('disks', [])
    if any(d.get('pdev_path') == DEVICE and d.get('vdev') != 'xvdb' for d in attached):
        raise RuntimeError('controller retained data uses an unexpected Xen device')
    if not any(driver.same_device(d['pdev_path'], DEVICE) for d in attached if d.get('pdev_path')):
        if len(attached) != 1: raise RuntimeError('controller has an unknown disk attachment')
        driver.run(['xl', 'block-attach', str(current['domid']), 'phy:' + DEVICE + ',xvdb,w'])
    driver.require_live(state)
    boot = driver.config_values(cfg)
    expected = 'phy:' + DEVICE + ',xvdb,w'
    if expected not in boot['disk']:
        if driver.checksum(cfg) != value['old_boot_sha256']:
            raise RuntimeError('controller boot file changed during data attachment')
        boot['disk'].append(expected)
        temporary = cfg.with_name('.ops-data.cfg')
        with temporary.open('w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(''.join(key + ' = ' + repr(item) + '\n' for key, item in boot.items()))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, cfg)
    new_hash = driver.checksum(cfg)
    if driver.checksum(driver.XEN / 'ops.cfg') not in (value['old_live_boot_sha256'], new_hash):
        raise RuntimeError('live controller boot file changed during data attachment')
    state['boot_sha256'] = new_hash
    if value['stage'] == 'allocated': value['stage'] = 'attached'
    save(driver, state, pending)
    driver.run(['vgcfgbackup', 'vg0'])
    driver.install_boot(work)
    if complete:
        observed = driver.read(evidence)
        if observed != {'box': box, 'filesystem_uuid': value['filesystem_uuid'], 'mounted': True}:
            raise RuntimeError('controller retained data mount verification differs')
        value['stage'] = 'ready'; save(driver, state, pending)
    return {'changed': True, 'data_volume': value}
