"""Finalize a stopped service candidate in a networkless Xen guest."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from xen_build_runtime import (attach_loop, boot_guest, checksum, resume_guest, detach_loop,
                               domain, loop_devices, read_slot, run, write)
import vm_update_engine as t

MIB = 1024**2


def disk_order(state):
    old, paths = t.configuration((t.BASE / 'operations' / state['operation_id'] / 'old.cfg').read_text(), state['role'], state['old_uuid'])
    if state['role'] == 'router' or state['role'] in ('bak','dmz','iot') and state['role']+'_creation' in state.get('requested_configuration',{}):
        new = [state['root_lv'], state['data_lv']]
    else:
        new = ['/dev/vg0/vmupd_' + state['operation_id'] + '_' + name for name in ('root', 'data')[:len(paths)]]
    if set(paths) != set(state['old_disks']) or set(new) != set(state['new_disks']):
        raise RuntimeError('service disk order differs from its recorded configuration')
    return paths, new


def release_slots(work):
    native = t.Native()
    for name in ('config', 'result'):
        path = work / (name + '.slot')
        attached = loop_devices(path)
        if len(attached) > 1: raise RuntimeError('service slot has multiple loop attachments')
        if attached:
            device = native.device(attached[0])
            for guest in native.inventory():
                for disk in guest.get('config', {}).get('disks', []):
                    if native.device(disk['pdev_path']) == device:
                        raise RuntimeError('a guest still holds the recorded service slot')
            native.check_unmounted({attached[0]: {'device':device}})
            detach_loop(path, attached[0])


def finalizer_result(work, state, digest):
    result = read_slot(work / 'result.slot')
    if (result.get('kind') != 'klokast.infrastructure-finalize.v1' or result.get('success') is not True or
            result.get('inputs_sha256') != digest or result.get('operation_id') != state['operation_id']):
        raise RuntimeError('service finalizer failed; inspect its protected result and console log')
    if state['role'] == 'router':
        request = t.read(work / 'request.json')
        if result.get('router_state') != request['router_state']:
            raise RuntimeError('router finalizer did not verify its retained-state contract')
        t.store(work / 'router-state-ready.json', {
            'operation_id': state['operation_id'], 'request_sha256': t.digest(request),
            'contract': request['router_state'], 'ready': True})
    state['stage'] = 'finalized'; write(work / 'service.json', state)


def resume_finalizer(work, state):
    saved = state.get('finalizer')
    if not saved: return False
    if checksum(work / 'finalize.cfg') != saved['config_sha256']:
        raise RuntimeError('recorded service finalizer configuration changed')
    if not resume_guest(work,saved['name'],saved['uuid'],
            {'operation_id':state['operation_id'],'inputs_sha256':saved['inputs_sha256']},
            kind='klokast.infrastructure-finalize.v1',timeout=1800):return False
    release_slots(work)
    finalizer_result(work,state,saved['inputs_sha256'])
    return True


def finalize(work, state):
    if resume_finalizer(work, state): return
    config = state['requested_configuration']
    old, new = disk_order(state)
    tx = t.Transaction(work)
    if tx.journal['stage'] != 'stopped' or tx.guest() is not None:
        raise RuntimeError('service state copy requires the exact stopped old generation')
    router = config['role'] == 'router'
    clean = router or config['role'] in ('bak','dmz','iot') and config['role']+'_creation' in config
    for source, target in ([] if clean else zip(old[1:], new[1:])):
        run(['dd', 'if=' + source, 'of=' + target, 'bs=4M', 'conv=fsync'], timeout=1800)
        if checksum(Path(source), state['old_disks'][source]['bytes']) != checksum(Path(target), state['new_disks'][target]['bytes']):
            raise RuntimeError('retained service disk copy did not verify')
    value = {k: config[k] for k in ('box', 'role', 'root_partition', 'source_files', 'accounts')}
    value['kind'] = 'klokast.vm-service-finalize.v1'
    for key in ('bootstrap_dns', 'routing_profile', 'vpn_routing', 'vpn_creation'):
        if not router and key in config: value[key] = config[key]
    if 'vpn_creation' in config:
        value['vpn_machine_id_sha256']=config['before']['identity']['machine_id']
    if router:
        value.update(router_state=config['router_state'], router_configuration=config['router_configuration'],
                     router_storage={'mode': 'migrate' if state['initialize_data'] else 'attach',
                                     'seed_id': state['operation_id']})
    elif clean:
        role=config['role']
        value[role+'_creation'] = dict(config[role+'_creation'], data_uuid=state['data_uuid'])
        value[role+'_storage'] = {'uuid':state['data_uuid'],'initialize':state['initialize_data'],
                                'persistent_before':bool(config['before'].get('persistent_storage'))}
        value[role+'_machine_id_sha256'] = config['before']['identity']['machine_id']
    content = json.dumps(value, sort_keys=True).encode(); digest = hashlib.sha256(content).hexdigest()
    if len(content) >= MIB:
        raise RuntimeError('frozen service configuration exceeds its finalization slot')
    loops = {}; name = 'finalize-service-' + state['operation_id']; identity = str(uuid.uuid4())
    if domain(name): raise RuntimeError('an interrupted service finalizer remains; inspect its recorded identity')
    release_slots(work)
    try:
        for key in ('config', 'result'):
            path = work / (key + '.slot')
            with path.open('wb') as stream:
                if key == 'config': stream.write(content + b'\0')
                stream.truncate(MIB); stream.flush(); os.fsync(stream.fileno())
            path.chmod(0o600); loops[key] = attach_loop(path, readonly=key == 'config')
        cfg = {'name': name, 'uuid': identity, 'type': 'pvh', 'memory': 4096, 'vcpus': 2,
               'kernel': str(work / 'kernel'), 'ramdisk': str(work / 'initramfs'), 'vif': [],
               'extra': 'console=hvc0 panic=1 root=/dev/xvda rootfstype=ext4 modules=ext4 rw init=/usr/local/libexec/klokast-service-finalize klokast_operation=' + state['operation_id'] + ' klokast_inputs=' + digest,
               'disk': ['phy:' + new[0] + ',xvda,w', 'phy:' + loops['config'] + ',xvdb,r',
                        'phy:' + loops['result'] + ',xvdc,w', 'phy:' + old[0] + ',xvdd,r'],
               'on_reboot': 'destroy', 'on_crash': 'destroy'}
        if clean: cfg['disk'].append('phy:'+state['data_lv']+',xvde,w')
        t.atomic(work / 'finalize.cfg', ('\n'.join(k + ' = ' + repr(v) for k, v in cfg.items()) + '\n').encode())
        state['finalizer'] = {'name':name,'uuid':identity,'inputs_sha256':digest,'config_sha256':checksum(work/'finalize.cfg')}
        write(work / 'service.json',state)
        boot_guest(work, name, identity, work / 'finalize.cfg', {'operation_id': state['operation_id'], 'inputs_sha256': digest},
                   kind='klokast.infrastructure-finalize.v1', timeout=1800)
        finalizer_result(work, state, digest)
    finally:
        if domain(name) is None:
            for key, device in loops.items(): detach_loop(work / (key + '.slot'), device)
