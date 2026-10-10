"""Exact, replayable controller resource retirement under the dom0 operation locks."""
import copy
import json
import os
from pathlib import Path
import re


def generations(record):
    rows = []
    while record:
        if len(rows) >= 64 or not isinstance(record, dict):
            raise RuntimeError('controller history is invalid or too deep')
        rows.append(record)
        record = record.get('previous')
    return rows


def no_references(m, state):
    """Check aliases as devices, plus boot paths and filesystem/loop references."""
    disk, work = Path(state['root_lv']), Path(state['work'])
    guests = json.loads(m.run(['xl', 'list', '-l']).stdout)
    if not isinstance(guests, list) or not any(g.get('domid') == 0 for g in guests):
        raise RuntimeError('incomplete Xen inventory; preserve disk')
    for guest in guests:
        for device in guest.get('config', {}).get('disks', []):
            path = device.get('pdev_path')
            if not path or (disk.exists() and m.same_device(path, str(disk))) or path == str(disk):
                raise RuntimeError('live guest references retirement disk')
        if str(work) + '/' in json.dumps(guest):
            raise RuntimeError('live guest references retirement boot files')
    for path in m.XEN.glob('*.cfg'):
        m.safe_file(path, m.MIB)
        content = path.read_text()
        if str(disk) in content or str(work) + '/' in content:
            raise RuntimeError('installed Xen configuration references retirement resources')
    for path in Path('/sys/block').glob('loop*/loop/backing_file'):
        content = '/' + path.read_text().strip().lstrip('/')
        if content == str(disk) or content.startswith(str(work) + '/'):
            raise RuntimeError('loop device references retirement resources')
        if disk.exists() and Path(content).exists() and m.same_device(content, str(disk)):
            raise RuntimeError('loop device references retirement disk alias')
    if disk.exists():
        device = disk.stat().st_rdev
        identity = str(os.major(device)) + ':' + str(os.minor(device))
        if any(line.split()[2] == identity for line in Path('/proc/self/mountinfo').read_text().splitlines()):
            raise RuntimeError('mounted filesystem references retirement disk')
        if any((Path('/sys/dev/block') / identity / 'holders').iterdir()):
            raise RuntimeError('device mapper holder references retirement disk')
        attributes = m.run(['lvs', '--noheadings', '-o', 'lv_attr', str(disk)]).stdout.strip()
        if len(attributes) != 10 or attributes[5] != '-':
            raise RuntimeError('retirement disk remains open')


def disk_identity(m, state, absent=False):
    info = m.infra.lv_info(Path(state['root_lv']))
    if info is None:
        if absent: return
        raise RuntimeError('recorded retirement disk is absent before planning')
    if info['lv_uuid'] != state['lv_uuid']:
        raise RuntimeError('retirement LV name has a different UUID')
    tags = set(filter(None, info['lv_tags'].split(',')))
    if state.get('lv_tag'):
        if state['lv_tag'] not in tags:
            raise RuntimeError('retirement disk ownership tag differs')
    elif state.get('origin') != 'legacy-adoption' or not state.get('legacy_config_sha256') or tags:
        raise RuntimeError('retirement disk lacks protected ownership evidence')


def boot_files(m, state):
    m.safe_directory(Path(state['work']))
    result = []
    for name in ('kernel', 'initramfs', 'ops.cfg'):
        path = Path(state['work']) / name
        m.safe_file(path, 256 * m.MIB)
        expected = state['boot_sha256'] if name == 'ops.cfg' else state['artifacts'][name]['sha256']
        if m.checksum(path) != expected:
            raise RuntimeError('retirement boot checksum differs')
        result.append({'path': str(path), 'sha256': expected, 'bytes': path.stat().st_size})
    return result


def persist(m):
    status = m.run(['lbu', 'status']).stdout.strip()
    if any(not re.fullmatch(r'[ADU]\s+etc/lvm/(?:backup/vg0|archive/vg0_[A-Za-z0-9_-]+\.vg)', line)
           for line in status.splitlines()):
        raise RuntimeError('unrelated dom0 changes prevent LVM metadata persistence')
    if status: m.run(['lbu', 'commit', '-d'])
    if m.run(['lbu', 'status']).stdout.strip():
        raise RuntimeError('dom0 metadata changed during retirement persistence')


def plan(m, box):
    records = {}
    for name in ('assignment.json', 'replacement.json'):
        path = m.BASE / name
        if path.exists(): records[str(path)] = m.read(path)
    unknown, items, compacted = [], [], {}
    current = records.get(str(m.BASE / 'assignment.json'))
    pending = records.get(str(m.BASE / 'replacement.json'))
    if current and (current.get('box') != box or current.get('stage') != 'ready'):
        raise RuntimeError('controller assignment is not ready for retirement')
    if pending and (pending.get('stage') not in ('accepted', 'rolled-back') or
                    pending.get('reboot_verification', {}).get('status') == 'pending'):
        unknown.append({'resource': 'replacement', 'reason': 'incomplete operation retains all controller generations'})
    elif current:
        rows = generations(current)
        if pending and pending.get('stage') == 'accepted' and pending.get('lv_uuid') != current['lv_uuid']:
            raise RuntimeError('accepted operation differs from current assignment')
        # Rolled-back records retain their resources until explicitly reconciled.
        protected = {v['lv_uuid'] for v in generations(pending)} if pending and pending['stage'] == 'rolled-back' else set()
        eligible = rows[2:]
        try:
            for state in eligible:
                if state['lv_uuid'] in protected:
                    raise RuntimeError('rollback record retains an older controller disk')
                disk_identity(m, state); no_references(m, state)
                items.append({'state': state, 'files': boot_files(m, state)})
            if eligible:
                for path, record in records.items():
                    value = copy.deepcopy(record)
                    if record.get('stage') in ('ready', 'accepted'):
                        value['previous'].pop('previous', None)
                        compacted[path] = value
        except (RuntimeError, OSError, KeyError, ValueError) as error:
            items = []; compacted = {}
            unknown.append({'resource': 'controller-history', 'reason': str(error)})
    fixtures = []
    directory = m.BASE.parent / 'qualification'
    for work in sorted(directory.glob('*')):
        try:
            m.safe_directory(work)
            request = m.read(work / 'request.json')
            if request.get('box') != box or request.get('operation_id') != work.name or not re.fullmatch('[0-9a-f]{24}', work.name):
                raise RuntimeError('qualification request identity differs')
            if (work / 'cleanup.json').exists() and m.read(work / 'cleanup.json').get('complete') is True: continue
            if (work / 'result.json').exists() and m.read(work / 'result.json').get('test_disks_removed') is True: continue
            states = {}
            for name in ('assignment.json', 'replacement.json'):
                path = work / 'controller' / name
                if path.exists():
                    for state in generations(m.read(path)):
                        old = states.get(state['root_lv'])
                        if old and old['lv_uuid'] != state['lv_uuid']:
                            raise RuntimeError('qualification disk records conflict')
                        states[state['root_lv']] = copy.deepcopy(state)
            if request['legacy_disk'] not in states:
                raise RuntimeError('qualification has no protected legacy LV UUID; preserve unresolved fixture')
            planned = []
            for state in states.values():
                if state['root_lv'] == request['legacy_disk']:
                    if request['legacy_disk'] != '/dev/vg0/opsqual_' + work.name or request['tag'] != 'klokast-ops-qualification-' + work.name:
                        raise RuntimeError('qualification legacy namespace differs')
                    state['lv_tag'] = request['tag']
                elif (state.get('root_lv') != '/dev/vg0/ops_' + state.get('operation_id', '') or
                      state.get('lv_tag') != 'klokast-ops-' + state.get('operation_id', '')):
                    raise RuntimeError('qualification replacement namespace differs')
                if not Path(state['work']).is_relative_to(work / 'controller'):
                    raise RuntimeError('qualification boot path is outside its namespace')
                disk_identity(m, state); no_references(m, state)
                # A failed finalizer may never have produced a boot config.
                files = []
                for name in ('kernel', 'initramfs'):
                    path = Path(state['work']) / name
                    m.safe_file(path, 256 * m.MIB)
                    if m.checksum(path) != state['artifacts'][name]['sha256']:
                        raise RuntimeError('qualification boot checksum differs')
                    files.append({'path': str(path), 'sha256': m.checksum(path), 'bytes': path.stat().st_size})
                planned.append({'state': state, 'files': files})
            fixtures.append({'work': str(work), 'request_sha256': m.digest(request), 'items': planned})
        except (RuntimeError, OSError, KeyError, ValueError) as error:
            unknown.append({'resource': str(work), 'reason': str(error)})
    result = {'kind': 'klokast.ops-retirement.v1', 'box': box, 'items': items, 'fixtures': fixtures,
              'records': records, 'compacted': compacted, 'unknown_unchanged': unknown}
    result['plan_sha256'] = m.digest(result)
    return result


def apply(m, proposal):
    # The caller holds both the image build and controller operation lock.
    for path, value in proposal['records'].items():
        if m.read(Path(path)) not in (value, proposal['compacted'].get(path)):
            raise RuntimeError('controller record changed during retirement; preserve resources')
    all_items = proposal['items'] + [item for fixture in proposal['fixtures'] for item in fixture['items']]
    for item in all_items:
        state = item['state']
        disk_identity(m, state, absent=True); no_references(m, state)
        # Validate every remaining file before deleting this disk.
        for entry in item['files']:
            path = Path(entry['path'])
            if path.exists() or path.is_symlink():
                m.safe_file(path, 256 * m.MIB)
                if m.checksum(path) != entry['sha256'] or path.stat().st_size != entry['bytes']:
                    raise RuntimeError('planned boot artifact changed during retirement')
        if m.infra.lv_info(Path(state['root_lv'])) is not None:
            m.run(['lvremove', '--yes', state['root_lv']])
        for entry in item['files']:
            path = Path(entry['path']); path.unlink(missing_ok=True)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    persist(m)
    for fixture in proposal['fixtures']:
        work = Path(fixture['work'])
        if m.digest(m.read(work / 'request.json')) != fixture['request_sha256']:
            raise RuntimeError('qualification request changed during cleanup')
        m.write(work / 'cleanup.json', {'kind': 'klokast.ops-qualification-cleanup.v1', 'complete': True,
                'request_sha256': fixture['request_sha256'], 'plan_sha256': proposal['plan_sha256'],
                'qualification_success': False, 'disks_removed': [v['state']['lv_uuid'] for v in fixture['items']]})
    for path, value in proposal['compacted'].items(): m.write(Path(path), value)
    return {'changed': bool(all_items), 'complete': True, 'plan_sha256': proposal['plan_sha256'],
            'removed_disks': [v['state']['root_lv'] for v in all_items], 'unknown_unchanged': proposal['unknown_unchanged']}


def retire(m, box, dry_run=False):
    directory = m.BASE / 'retirement'; directory.mkdir(mode=0o700, exist_ok=True)
    m.safe_directory(directory)
    pending = []
    for path in directory.glob('*/plan.json'):
        if not (path.parent / 'complete.json').exists(): pending.append(path)
    if len(pending) > 1: raise RuntimeError('multiple incomplete retirement plans require inspection')
    proposal = m.read(pending[0]) if pending else plan(m, box)
    if proposal['box'] != box or proposal['plan_sha256'] != m.digest({k: v for k, v in proposal.items() if k != 'plan_sha256'}):
        raise RuntimeError('recorded retirement plan identity differs')
    if dry_run: return dict(proposal, changed=False)
    work = directory / proposal['plan_sha256']; work.mkdir(mode=0o700, exist_ok=True)
    if (work / 'complete.json').exists(): return dict(m.read(work / 'complete.json'), changed=False)
    if not (work / 'plan.json').exists(): m.write(work / 'plan.json', proposal)
    result = apply(m, proposal)
    m.write(work / 'complete.json', result)
    return result
