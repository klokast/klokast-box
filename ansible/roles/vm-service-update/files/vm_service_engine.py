"""Native image preparation and finalization for protected service transactions."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from xen_build_runtime import checksum, domain, require_identity, run, safe_file, write
import vm_service_finalization as finalization
from vm_service_finalization import disk_order, finalize, release_slots
import vm_dmz_creation
import vm_local_images

import vm_update_engine as t
IMAGES = Path('/mnt/dom0_data/klokast-vm-templates/candidates')
MIB = 1024**2


def read(path):
    safe_file(path, 16 * MIB)
    return json.loads(path.read_text(), object_pairs_hook=t.unique)


def image(box, operation, receipt):
    value = read(receipt)
    if (value.get('kind') != 'klokast.vm-image-receipt.v1' or value.get('box') != box or
            value.get('operation_id') != operation or value.get('receipt_sha256') !=
            t.digest({k: v for k, v in value.items() if k != 'receipt_sha256'})):
        raise RuntimeError('service image receipt identity differs')
    vm_local_images.validate(box, operation, value['files'])
    candidate = read(IMAGES / operation / 'candidate.json')
    if candidate != value['files']['candidate.json']:
        raise RuntimeError('service candidate differs from the qualified receipt')
    for name, item in candidate['artifacts'].items():
        if name not in ('root', 'kernel', 'initramfs'): raise RuntimeError('unexpected service image artifact')
        path = IMAGES / operation / name; safe_file(path, 5 * 1024 * MIB)
        if checksum(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise RuntimeError('service image artifact differs: ' + name)
    return value


def owned(path, expected=None, tag=None):
    native = t.Native()
    info = native.lv(path)
    if expected and {k: info[k] for k in ('uuid', 'bytes')} != expected:
        raise RuntimeError('service disk was replaced or resized')
    if tag and run(['lvs', '--noheadings', '-o', 'lv_tags', path]).stdout.strip() != tag:
        raise RuntimeError('service disk ownership tag differs')
    return {k: info[k] for k in ('uuid', 'bytes')}


def pending(box):
    result = []
    for path in sorted((t.BASE / "operations").glob("*/service.json")):
        record = read(path)
        if record["box"] != box: raise RuntimeError("service operation belongs to another box")
        journal = read(path.parent / "journal.json") if (path.parent / "journal.json").exists() else {}
        if journal.get('stage') == 'complete' or (record.get('stage') == 'abandoned' and journal.get('stage') in (None,'recovered')):
            continue
        result.append(record)
    for role in ('bak','dmz','iot'):
        creation=vm_dmz_creation.assignment(read,t.BASE,box,role)
        if creation and not creation.get('service'):
            result.append(creation)
    return result


def status(box, role, *, lock_held=False):
    creation=vm_dmz_creation.assignment(read,t.BASE,box,role) if role in ('bak','dmz','iot') else None
    if not t.BASE.exists():
        return dict(assignment=None,pending=pending(box),installed=creation.get('service') if creation else None,
                    creation=creation,present=domain(role) is not None,
                    **({'router_source':legacy_source(box)} if role=='router' else {}))
    with t.current_assignment(role, lock_held=lock_held) as tx:
        assignment = t.assignment_report(tx) if tx else None
    installed = None
    if assignment and assignment.get('stage') == 'complete':
        path = t.BASE / 'operations' / assignment['operation_id'] / 'service.json'
        if path.exists(): installed = read(path)
    result = {'assignment': assignment, 'pending': pending(box), 'installed': installed or (creation.get('service') if creation else None),
              'creation':creation,'present':domain(role) is not None}
    if role == 'router' and not assignment:
        result['router_source'] = legacy_source(box)
    return result


def legacy_source(box):
    """Read only protected migration evidence; absence never authorizes adoption."""
    base = t.router_legacy_base()
    if any((base / name).exists() for name in ('pending.json','cold-test.json')):
        raise RuntimeError('finish the legacy router operation before common replacement')
    if not (base/'accepted.json').exists():
        raise RuntimeError('router has no protected accepted assignment; supervised baseline verification is required')
    accepted = read(base/'accepted.json')
    generation = read(base/'records'/(accepted['current_sha256']+'.json'))
    topology = read(t.BASE.parent/'klokast-infrastructure/ops/router-network.json')
    for record in (accepted,generation):
        if (record.get('box') != box or record.get('role') != 'router' or
                record.get('record_sha256') != t.digest({k:v for k,v in record.items() if k!='record_sha256'})):
            raise RuntimeError('router legacy assignment integrity differs')
    if (topology.get('stage') != 'accepted' or topology.get('box') != box or
            topology.get('accepted_after') != accepted or
            topology.get('boot_after',{}).get('vif') != generation['xen']['vif']):
        raise RuntimeError('router migration requires its latest accepted controller topology')
    return {'assignment_sha256':accepted['record_sha256'],'generation_sha256':generation['record_sha256'],
            'topology_sha256':t.digest(topology)}


def allocate(path, size, tag, recorded):
    rows = json.loads(run(['lvs', '--reportformat', 'json', '-o', 'lv_path', 'vg0']).stdout)['report'][0]['lv']
    exists = any(row['lv_path'].strip() == path for row in rows)
    if not exists:
        if recorded: raise RuntimeError('recorded service disk disappeared')
        run(['lvcreate', '--yes', '--size', str(size) + 'B', '--name', Path(path).name, '--addtag', tag, 'vg0'])
    return owned(path, recorded, tag)


def require_memory():
    info = {key.strip(): value.strip() for line in run(['xl','info']).stdout.splitlines()
            if ':' in line for key, value in [line.split(':', 1)]}
    if int(info.get('free_memory', '0')) < 4352:
        raise RuntimeError('insufficient free Xen memory for the isolated service copy')


def prepare(box, role, operation, config_path, receipt_path, inputs_sha256):
    work = t.BASE / 'operations' / operation
    record_path = work / 'service.json'
    if record_path.exists():
        state = read(record_path)
        if (state['box'] != box or state['role'] != role or state['inputs_sha256'] != inputs_sha256 or
                state['requested_configuration'] != read(config_path)):
            raise RuntimeError('service operation inputs differ from its frozen configuration')
    else:
        if pending(box): raise RuntimeError('an incomplete service update requires resume')
        config = read(config_path)
        if config['box'] != box or config['role'] != role: raise RuntimeError('service configuration target differs')
        receipt = image(box, config['image'], receipt_path)
        profile = t.ROUTER_PROFILE if role == 'router' else ('vpn-egress-alpine-v1' if role == 'vpn-egress' else 'shared-alpine-v1')
        if receipt['files']['inputs.json']['profile'] != profile: raise RuntimeError('service image has the wrong profile')
        current = domain(role)
        if not current: raise RuntimeError('service update requires the declared running guest')
        old_uuid = current['config']['c_info']['uuid']
        path = t.XEN / (role + '.cfg'); old_content = t.secure(path).read_bytes()
        old, disks = t.configuration(old_content.decode(), role, old_uuid)
        if not 1 <= len(disks) <= 2 or not old['disk'][0].endswith(',xvda,w'):
            raise RuntimeError('service update supports a root and optional retained disk')
        if len(disks) == 2 and not old['disk'][1].endswith(',xvdb,w'):
            raise RuntimeError('service retained disk must be xvdb')
        if role == 'vpn-egress' and 'vpn_creation' in config and len(disks) != 1:
            raise RuntimeError('VPN reconstruction refuses unexpected persistent storage')
        old_disks = {path: owned(path) for path in disks}
        native = t.Native()
        if {native.device(d['pdev_path']) for d in current['config']['disks']} != {native.device(d) for d in disks}:
            raise RuntimeError('live service disks differ from its installed configuration')
        if config['root_partition'] != ('3' if 'root=/dev/xvda3' in old.get('extra', '') else ''):
            raise RuntimeError('guest root layout differs from its Xen definition')
        auto = t.XEN / 'auto' / (role + '.cfg')
        if not auto.is_symlink() or auto.resolve() != path:
            raise RuntimeError('running service lacks its declared autostart link')
        clean = role in ('bak','dmz','iot') and role+'_creation' in config
        router = role == 'router'
        if router:
            t.router_contract(config.get('router_state'), box)
            if len(disks) == 1 and config.get('router_source') != legacy_source(box):
                raise RuntimeError('router source changed before allocation')
            if (len(disks) == 1) != (config.get('router_source') is not None):
                raise RuntimeError('router first replacement requires its legacy source; later roots reuse data')
            if len(disks) == 2 and old_disks[disks[1]]['bytes'] != 1024*MIB:
                raise RuntimeError('router retained disk must be exactly 1 GiB')
            if len(disks) == 1:
                t.router_source(dict(box=box,router_source=config['router_source'],
                    router_state=config['router_state'],engine_commit=config['engine_commit'],
                    old_uuid=old_uuid,old_disks=old_disks,
                    old_artifacts={old[k]:native.artifact(old[k]) for k in ('kernel','ramdisk')}))
        root_bytes = 8 * 1024 * MIB if clean else 4 * 1024 * MIB if router else old_disks[disks[0]]['bytes']
        if clean and not config.get('discard_root_data') and not config['before'].get('persistent_storage'):
            raise RuntimeError('first shared guest transition requires explicit --discard-root-data')
        if clean and (len(disks) == 2) != bool(config['before']['retained_uuid']):
            raise RuntimeError('shared guest retained filesystem and Xen data attachment differ')
        if receipt['files']['candidate.json']['artifacts']['root']['bytes'] > root_bytes:
            raise RuntimeError('qualified service image exceeds the existing root capacity')
        needed = (root_bytes + (50 * 1024 * MIB if len(disks) == 1 else 0)) if clean else sum(v['bytes'] for v in old_disks.values())
        if router: needed = root_bytes + (1024*MIB if len(disks) == 1 else 0)
        free = float(run(['vgs', '--noheadings', '--units', 'b', '--nosuffix', '-o', 'vg_free', 'vg0']).stdout.strip())
        if free < needed: raise RuntimeError('insufficient LVM capacity for independent service disks')
        require_memory()
        if run(['lbu','status']).stdout.strip():
            raise RuntimeError('persist or resolve unrelated dom0 changes before service replacement')
        work.mkdir(mode=0o700, exist_ok=True)
        t.secure(work, True)
        t.atomic(work / 'old.cfg', old_content)
        shutil.copyfile(receipt_path, work / 'receipt.json'); (work / 'receipt.json').chmod(0o600)
        state = {'kind': 'klokast.vm-service-update.v1', 'operation_id': operation, 'box': box, 'role': role,
                 'image': config['image'], 'requested_configuration': config, 'inputs_sha256': inputs_sha256,
                 'old_uuid': old_uuid, 'new_uuid': str(uuid.uuid4()), 'old_disks': old_disks,
                 'new_disks': {}, 'stage': 'allocated', 'reboot_verification': {'status': 'required'}}
        if clean or router:
            state.update(root_lv='/dev/vg0/vmupd_'+operation+'_root',
                         data_lv=disks[1] if len(disks)==2 else '/dev/vg0/vmupd_'+operation+'_data',
                         data_uuid=config['router_state']['filesystem_uuid'] if router else config['before']['retained_uuid'] or str(uuid.uuid4()),
                         initialize_data=len(disks)==1,
                         discarded_root=old_disks[disks[0]] if config.get('discard_root_data') else None)
        write(record_path, state)
    receipt = image(box, state['image'], work / 'receipt.json')
    old, disks = t.configuration((work / 'old.cfg').read_text(), role, state['old_uuid'])
    for path, expected in state['old_disks'].items(): owned(path, expected)
    config = state['requested_configuration']
    clean = config['role'] in ('bak','dmz','iot') and config['role']+'_creation' in config
    router = role == 'router'
    data_bytes = 1024*MIB if router else 50*1024*MIB
    targets = [(state['root_lv'], (4 if router else 8)*1024*MIB, 'root')] if clean or router else [
        ('/dev/vg0/vmupd_'+operation+'_'+name, state['old_disks'][path]['bytes'], name)
        for path,name in zip(disks, ('root','data'))]
    if clean or router:
        if state['initialize_data']:
            targets.append((state['data_lv'],data_bytes,'data'))
        else:
            state['new_disks'][state['data_lv']] = owned(state['data_lv'],state['old_disks'][state['data_lv']])
    for target, size, name in targets:
        state['new_disks'][target] = allocate(target, size,
            'klokast.vm-update.'+operation+'.'+name, state['new_disks'].get(target))
        write(record_path, state)
    if state['stage'] == 'allocated':
        root = '/dev/vg0/vmupd_' + operation + '_root'
        size = state['new_disks'][root]['bytes']
        if size % MIB: raise RuntimeError('service root must have whole MiB extents')
        # A new guest must not read residual blocks from a formerly freed LV.
        run(['dd','if=/dev/zero','of='+root,'bs=1M','count='+str(size//MIB),'conv=fsync'],timeout=900)
        run(['dd', 'if=' + str(IMAGES / state['image'] / 'root'), 'of=' + root, 'bs=4M', 'conv=fsync'], timeout=900)
        artifact = receipt['files']['candidate.json']['artifacts']['root']
        if checksum(Path(root), artifact['bytes']) != artifact['sha256']:
            raise RuntimeError('cloned service root differs from the qualified image')
        if (clean or router) and state['initialize_data']:
            run(['dd','if=/dev/zero','of='+state['data_lv'],'bs=1M','count='+str(data_bytes//MIB),'conv=fsync'],timeout=1800)
        for name in ('kernel', 'initramfs'):
            shutil.copyfile(IMAGES / state['image'] / name, work / name); (work / name).chmod(0o600)
        new = dict(old, uuid=state['new_uuid'], on_reboot='restart', on_poweroff='destroy', on_crash='destroy', kernel=str(work / 'kernel'), ramdisk=str(work / 'initramfs'),
                   disk=['phy:' + path + ',xvd' + letter + ',w' for path, letter in zip(disk_order(state)[1], 'ab')],
                   extra='console=hvc0 panic=1 root=/dev/xvda rootfstype=ext4 modules=ext4 rw')
        if 'vpn_creation' in config or clean:
            public = config[role+'_creation'] if clean else config['vpn_creation']
            mac = '02:' + ':'.join(hashlib.sha256((box+'-'+role).encode()).hexdigest()[i:i+2] for i in range(0,10,2))
            new.update(name=role, type='pvh', memory=4096 if clean else 1024, maxmem=4096 if clean else 1024, vcpus=2, vif=['bridge='+public['bridge']+',mac='+mac])
            new.pop('cpus', None)
        t.atomic(work / 'new.cfg', ('\n'.join(k + ' = ' + repr(v) for k, v in new.items()) + '\n').encode())
        request = {'kind': t.SERVICE_KIND, 'operation_id': operation, 'box': box, 'role': role,
                   'engine_commit': state['requested_configuration']['engine_commit'], 'policy_sha256': state['inputs_sha256'],
                   'old_uuid': state['old_uuid'], 'new_uuid': state['new_uuid'], 'autostart': True,
                   'old_disks': state['old_disks'], 'new_disks': state['new_disks'],
                   'release_sha256': receipt['files']['release-evidence.json']['release_sha256'],
                   'image_profile': receipt['files']['inputs.json']['profile'],
                   'replacement_seconds': 3600, 'recovery_seconds': 300}
        for side, cfg in (('old', old), ('new', new)):
            request[side + '_config_sha256'] = checksum(work / (side + '.cfg'))
            request[side + '_artifacts'] = {cfg[k]: t.Native().artifact(cfg[k]) for k in ('kernel', 'ramdisk')}
        if clean or router:
            request.update(kind=t.RETAINED_KIND,retained_disks={state['data_lv']:state['new_disks'][state['data_lv']]})
        if router:
            request.update(router_state=config['router_state'], router_source=config['router_source'])
        write(work / 'request.json', request)
        t.Native().persist()
        state['stage'] = 'prepared'; write(record_path, state)
    return state


def abandon(work, state):
    journal = read(work/'journal.json') if (work/'journal.json').exists() else None
    if state['stage'] == 'abandoned' and (journal or {}).get('stage') in (None,'recovered'):
        return state
    if journal:
        if journal['stage'] not in ('armed','stopping','stopped','recovered'):
            raise RuntimeError('cannot abandon a service generation after boot was attempted; resume it')
        if state.get('finalizer') and domain(state['finalizer']['name']) is not None:
            raise RuntimeError('the isolated copy is still running; resume it before abandoning the unbooted attempt')
        t.invoke(state['operation_id'],'recover')
        tx = t.Transaction(work)
        if tx.journal['stage'] != 'recovered' or tx.guest(('old',)) is None:
            raise RuntimeError('old service generation has not recovered')
    else:
        native = t.Native()
        current = domain(state['role'])
        if not current or current['config']['c_info']['uuid'] != state['old_uuid']:
            raise RuntimeError('unarmed service operation no longer has its original running guest')
        if (t.XEN/(state['role']+'.cfg')).read_bytes() != (work/'old.cfg').read_bytes():
            raise RuntimeError('unarmed service boot configuration changed')
        for path, expected in state['old_disks'].items(): owned(path, expected)
        new = {path:native.lv(path) for path in state['new_disks'] if path not in state['old_disks']}
        for path, expected in state['new_disks'].items(): owned(path, expected)
        native.check_unmounted(new)
        devices = {v['device'] for v in new.values()}
        for guest in native.inventory():
            if guest['config']['c_info'].get('uuid') == state['new_uuid']:
                raise RuntimeError('the unarmed candidate has unexpectedly booted')
            if any(native.device(d['pdev_path']) in devices for d in guest.get('config',{}).get('disks',[])):
                raise RuntimeError('an unarmed candidate disk is still in use')
        native.persist()
    # This retires only the unfinished attempt. No disk or image is deleted.
    state['stage'] = 'abandoned'; write(work/'service.json',state)
    return state

