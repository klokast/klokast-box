#!/usr/bin/python3
"""Publish a narrow router topology revision while retaining immutable history."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys

from xen_build_runtime import domain, run, safe_file, write
import ops_network_dom0 as controller

BASE = Path('/mnt/dom0_data/klokast-router-updates')
PATH = controller.BASE / 'router-network.json'


def modules():
    installed = json.loads(Path('/usr/local/lib/klokast/router-updates/current.json').read_text())
    engine = installed['engine_commit']
    if not re.fullmatch('[0-9a-f]{40}', engine): raise RuntimeError('router recovery engine selector is invalid')
    sys.path.insert(0, '/usr/local/lib/klokast/router-updates/' + engine)
    import router_records, router_generations, router_executor, router_native
    return router_records, router_generations, router_executor, router_native


def prepare(box, proof):
    if PATH.exists(): return controller.driver.read(PATH)
    if proof['stage'] != 'prepared': raise RuntimeError('router cleanup must be prepared before attachment changes')
    boot = controller.driver.config_values(Path('/etc/xen/router.cfg'))
    live = domain('router')
    if (not live or controller.vifs(live) != boot['vif'] or len(boot['vif']) != 7 or
            not boot['vif'][-1].startswith('bridge=br-ops,mac=')):
        raise RuntimeError('router must have its exact existing seven NICs before removing only eth6')
    state = {'box': box, 'stage': 'prepared', 'boot_before': boot,
             'proof': proof, 'accepted_before': None, 'generation_before': None,
             'live_uuid': live['config']['c_info']['uuid']}
    for name in ('pending.json', 'cold-test.json'):
        if (BASE / name).exists(): raise RuntimeError('finish the recorded router operation before network migration')
    if (BASE / 'accepted.json').exists():
        records, generations, executor, native = modules()
        store = records.Records(box)
        executor.boot_assignment(store, require_running=True)
        accepted = store.accepted(); generation = store.generation(accepted['current_sha256'])
        if generation['origin'] != 'legacy':
            raise RuntimeError('this migration supports the existing legacy router baseline only')
        if any(proof['files'].get(name, {}).get('before') != digest for name, digest in generation['configuration_files'].items()):
            raise RuntimeError('running router files differ from the accepted generation')
        state.update(accepted_before=accepted, generation_before=generation)
    write(PATH, state)
    return state


def commit(box, state, proof):
    if proof['stage'] != 'applied' or proof['files'] != {
            name: item for name, item in state['proof']['files'].items()}:
        raise RuntimeError('router cleanup evidence differs from the prepared exact changes')
    live = domain('router'); old = state['boot_before']
    if not live or live['config']['c_info']['uuid'] != state['live_uuid']:
        raise RuntimeError('router identity changed during topology migration')
    actual = controller.vifs(live)
    if actual == old['vif']:
        run(['xl', 'network-detach', str(live['domid']), old['vif'][-1].split(',mac=')[1]])
    elif actual != old['vif'][:-1]: raise RuntimeError('router workload NICs differ during migration')
    if controller.vifs(domain('router')) != old['vif'][:-1]: raise RuntimeError('router management NIC did not detach')
    desired = dict(old, vif=old['vif'][:-1])
    current = controller.driver.config_values(Path('/etc/xen/router.cfg'))
    if current not in (old, desired): raise RuntimeError('router boot file changed during migration')
    controller.text(Path('/etc/xen/router.cfg'), controller.render(desired))
    if state['accepted_before']:
        records, generations, executor, native = modules()
        store = records.Records(box)
        old_assignment = state['accepted_before']; before = state['generation_before']
        revised = copy.deepcopy(before); revised.pop('record_sha256')
        revised['xen']['vif'] = desired['vif']
        revised.pop('overlay_source_sha256', None)
        for name, item in proof['files'].items():
            if name not in revised['configuration_files']: continue
            if item['after'] is None: revised['configuration_files'].pop(name)
            else: revised['configuration_files'][name] = item['after']
        # A disabled overlay include can stay in the fileset. The legacy
        # generation format requires either all overlay files or none.
        revised['configuration_files'].pop('etc/klokast/overlay-ipv6.nft', None)
        revised['evidence_sha256'] = generations.digest(proof)
        revised = generations.generation(generations.seal(revised), box)
        accepted = dict(old_assignment); accepted.pop('record_sha256')
        accepted.update(current_sha256=revised['record_sha256'], evidence_sha256=generations.digest(proof))
        accepted = records.assignment(generations.seal(accepted), box)
        with store.lock():
            if store.pending() is not None: raise RuntimeError('router operation appeared during topology migration')
            if store.accepted() not in (old_assignment, accepted): raise RuntimeError('router assignment changed during migration')
            record_path = BASE / 'records' / (revised['record_sha256'] + '.json')
            if record_path.exists():
                if records.read(record_path) != revised: raise RuntimeError('immutable network revision differs')
            else: records.write(record_path, revised)
            records.write(BASE / 'accepted.json', accepted)
            executor.boot_assignment(store, require_running=True)
    run(['lbu', 'commit', '-d'])
    state['stage'] = 'accepted'; write(PATH, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'commit'))
    parser.add_argument('--box', required=True)
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != args.box + '-dom0': raise RuntimeError('wrong dom0 execution locus')
    safe_file(args.evidence, 1024 * 1024); proof = json.loads(args.evidence.read_text())
    with (controller.BASE / 'operation.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'prepare': state = prepare(args.box, proof)
        else: state = commit(args.box, controller.driver.read(PATH), proof)
        print(json.dumps({'box': args.box, 'stage': state['stage']}))


if __name__ == '__main__':
    try: main()
    except (RuntimeError, ValueError, OSError) as error:
        print('ops-router-dom0: ' + str(error), file=sys.stderr); sys.exit(1)
