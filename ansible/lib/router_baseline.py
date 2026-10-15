"""Record an inspected legacy router under existing controller authority.

Dom0 handles only protected records, opaque disks and Xen metadata. Publication
persists the observed Xen UUID without changing the running guest, disk or enrollment.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import router_generations as generations
import router_native as native
import router_records as records
from router_transaction import TransactionError

KIND = 'klokast.router-baseline.v1'


def proposal(box, operation, engine, instance, observation, guest):
    evidence = dict(observation=observation, guest=guest, instance_commit=instance)
    generation = generations.generation(generations.seal(dict(
        kind='klokast.router-generation.v1', box=box, role='router',
        generation_id=operation, origin='legacy', engine_commit=engine,
        alpine_branch=guest['alpine_branch'], disk=observation['disk'], boot=observation['boot'],
        xen=observation['xen'], packages=guest['packages'], kernel_release=guest['kernel'],
        accounts=guest['router_accounts'], configuration_files=guest['configuration_files'],
        evidence_sha256=generations.digest(evidence))), box)
    accepted = records.assignment(generations.seal(dict(
        kind='klokast.router-assignment.v1', box=box, role='router',
        current_sha256=generation['record_sha256'], previous_sha256=None,
        operation_id=operation, engine_commit=engine, policy_sha256=records.BASELINE_AUTHORITY_SHA256,
        evidence_sha256=generation['evidence_sha256'])), box)
    return generations.seal(dict(kind=KIND, box=box, operation_id=operation,
        engine_commit=engine, instance_commit=instance, observation=observation,
        guest=guest, generation=generation, accepted=accepted))


def validate(value, box):
    generations.check_seal(value)
    if (value.get('kind') != KIND or value.get('box') != box or
            not generations.matches('[0-9a-f]{40}', value.get('instance_commit')) or
            value != proposal(box, value['operation_id'], value['engine_commit'],
                              value['instance_commit'], value['observation'], value['guest'])):
        raise TransactionError('router baseline publication differs from its inspected evidence')
    before = value['observation']['topology']
    if (before.get('stage') != 'accepted' or before.get('box') != box or
            before.get('accepted_before') is not None or before.get('accepted_after') is not None or
            'baseline_sha256' in before or
            before['boot_after']['vif'] != value['generation']['xen']['vif'] or
            len(before['boot_after']['vif']) != 6 or
            any('bridge=br-ops,' in v for v in before['boot_after']['vif']) or
            value['guest']['xen_uuid'] != value['generation']['xen']['uuid'] or
            value['guest']['root_partition'] != '3' or value['guest']['persistent_storage'] is not None):
        raise TransactionError('router baseline requires the completed independent-controller topology and legacy root')
    return value


def topology_after(value):
    after = copy.deepcopy(value['observation']['topology'])
    after.update(accepted_after=value['accepted'], baseline_sha256=value['record_sha256'])
    after['boot_after']['uuid']=value['generation']['xen']['uuid']
    return after


def recovery_selection(state, baseline, accepted):
    """Permit both interrupted publication states; never infer another root."""
    validate(baseline, state['box'])
    if state != topology_after(baseline) or accepted not in (None, baseline['accepted']):
        raise TransactionError('router topology baseline or accepted assignment conflicts')
    return state['boot_after']


def available(store):
    for name in ('pending.json', 'cold-test.json', 'installation.json'):
        path = store.base/name
        if path.exists() or path.is_symlink():
            raise TransactionError('finish the recorded router operation before baseline reconciliation: '+name)
    common = store.base.parent/'klokast-vm-updates/active/router.json'
    if common.exists() or common.is_symlink():
        raise TransactionError('router already belongs to the common VM lifecycle')


def boot_artifact(path):
    path=Path(path);records.parents(path);records.secure(path,maximum=128*1024**2)
    return dict(path=str(path),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def observe(store, topology_path, *, xen=Path('/etc/xen'), host=None):
    available(store)
    host = host or native.Native(); deadline = host.monotonic()+120
    host.guard(store.box, deadline=deadline)
    saved = records.read(store.base/'baseline.json') if (store.base/'baseline.json').exists() else None
    accepted = store.accepted() if (store.base/'accepted.json').exists() else None
    topology = records.read(topology_path)
    if saved:
        validate(saved, store.box)
        if accepted not in (None, saved['accepted']) or topology not in (
                saved['observation']['topology'], topology_after(saved)):
            raise TransactionError('router baseline retry found conflicting publication records')
        original = saved['observation']['topology']
    else:
        if accepted is not None:
            raise TransactionError('router already has an accepted assignment')
        original = topology
    if (original.get('stage') != 'accepted' or original.get('box') != store.box or
            original.get('accepted_before') is not None or original.get('accepted_after') is not None):
        raise TransactionError('router baseline needs its accepted unassigned topology record')
    boot = native.literal_configuration(records.secure(xen/'router.cfg').read_text())
    allowed=[original['boot_after']]
    if saved:allowed.append(topology_after(saved)['boot_after'])
    if boot not in allowed:
        raise TransactionError('router boot definition differs from its accepted topology')
    link = xen/'auto/router.cfg'
    if (not link.is_symlink() or link.lstat().st_uid != records.ROOT_UID or
            os.readlink(link) not in ('../router.cfg', str(xen/'router.cfg'))):
        raise TransactionError('router baseline requires its exact managed autostart link')
    inventory=host.inventory(deadline=deadline)
    network=records.read(topology_path.parent/'network.json')
    ops=[d for d in inventory if d['config']['c_info']['name']=='ops']
    expected=network.get('boot_after',{}).get('vif',[])
    if (network.get('box')!=store.box or network.get('stage')!='accepted' or len(ops)!=1 or
            len(expected)!=2 or not expected[0].startswith('bridge=br-ops,mac=') or
            not expected[1].startswith('bridge=br-wan,mac=') or
            ['bridge='+n['bridge']+',mac='+n['mac'] for n in ops[0]['config']['nics']]!=expected):
        raise TransactionError('router baseline requires the live independent controller uplink')
    live = [d for d in inventory if d['config']['c_info']['name']=='router']
    if len(live)!=1: raise TransactionError('router baseline requires one running router')
    identity=live[0]['config']['c_info']['uuid']
    if boot.get('uuid',identity)!=identity or original['live_uuid']!=identity:
        raise TransactionError('router Xen identity changed since controller network migration')
    xen_identity={k:boot[k] for k in ('memory','vcpus','vif')};xen_identity['uuid']=identity
    rows=json.loads(native.command(['/sbin/lvs','--reportformat','json','--units','b','--nosuffix',
        '-o','lv_path,lv_uuid,lv_size','/dev/vg0/lv_router'],deadline))['report'][0]['lv']
    if len(rows)!=1: raise TransactionError('legacy router LV is ambiguous')
    disk=dict(path=rows[0]['lv_path'].strip(),uuid=rows[0]['lv_uuid'].strip(),bytes=int(rows[0]['lv_size'].strip()))
    artifacts={}
    for name,key in (('kernel','kernel'),('initramfs','ramdisk')):
        artifacts[name]=boot_artifact(boot[key])
    observation=dict(disk=disk,boot=artifacts,xen=xen_identity,topology=original)
    if saved and observation!=saved['observation']:
        raise TransactionError('router disk, boot artifacts or topology changed since baseline preparation')
    return observation,saved


def check_live(store, value, *, host=None, xen=Path('/etc/xen'), require_running=True):
    validate(value,store.box)
    host=host or native.Native();deadline=host.monotonic()+120
    generation=value['generation']
    expected=native.literal_configuration(generations.configuration(generation))
    boot=native.literal_configuration(records.secure(xen/'router.cfg').read_text())
    boot.setdefault('uuid',generation['xen']['uuid'])
    if boot!=expected: raise TransactionError('router baseline boot profile is unsupported')
    host.disk(generation['disk'],deadline=deadline)
    for item in generation['boot'].values():host.artifact(item,deadline=deadline)
    live=host.guest({'baseline':generation},deadline=deadline)
    if require_running and live is None or live is not None and live[0]!='baseline':
        raise TransactionError('router baseline guest is not running exclusively')


def recover_boot(store, topology_path, *, host=None, xen=Path('/etc/xen')):
    """Persist the inspected Xen UUID before autostart, even after partial publication."""
    path=store.base/'baseline.json'
    if not path.exists() and not path.is_symlink():return
    available(store)
    value=validate(records.read(path),store.box)
    topology=records.read(topology_path)
    accepted=store.accepted() if (store.base/'accepted.json').exists() else None
    if topology not in (value['observation']['topology'],topology_after(value)) or accepted not in (None,value['accepted']):
        raise TransactionError('router baseline boot recovery found conflicting publication records')
    check_live(store,value,host=host,xen=xen,require_running=False)
    records.atomic(xen/'router.cfg',generations.configuration(value['generation']).encode())
    native.command(['/usr/sbin/lbu','commit','-d'],(host or native.Native()).monotonic()+120,maximum_seconds=120)


def publish(store, topology_path, value, *, checkpoint=lambda stage:None, host=None, xen=Path('/etc/xen')):
    observation,saved=observe(store,topology_path,host=host,xen=xen)
    validate(value,store.box)
    if value['observation']!=observation or saved is not None and saved!=value:
        raise TransactionError('router baseline inputs changed; preserve the recorded publication')
    check_live(store,value,host=host,xen=xen)
    if saved is None:records.write(store.base/'baseline.json',value)
    checkpoint('prepared')
    record_path=store.base/'records'/(value['generation']['record_sha256']+'.json')
    if record_path.exists() or record_path.is_symlink():
        if records.read(record_path)!=value['generation']:raise TransactionError('immutable router baseline generation differs')
    else:records.write(record_path,value['generation'])
    checkpoint('generation')
    # The existing topology recovery reader accepts absent or matching assignment
    # only with this exact protected intent. The original topology stays in it.
    records.write(topology_path,topology_after(value))
    checkpoint('topology')
    recover_boot(store,topology_path,host=host,xen=xen)
    checkpoint('boot')
    if not (store.base/'accepted.json').exists():
        if store.adopt(value['generation'])!=value['accepted']:raise TransactionError('router baseline assignment differs')
    elif store.accepted()!=value['accepted']:raise TransactionError('router baseline assignment changed')
    checkpoint('accepted')
    return dict(state='complete',box=store.box,operation_id=value['operation_id'],
                assignment_sha256=value['accepted']['record_sha256'],
                topology_sha256=generations.digest(topology_after(value)))
