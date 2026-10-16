#!/usr/bin/python3
"""Attach the fixed ops uplink and publish its protected boot revision.

The active controller stages this helper under the existing installation lock.
Dom0 never reads a guest filesystem or receives controller credentials.
"""
import argparse
import copy
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys

import ops_network
from xen_build_runtime import checksum, domain, run, safe_file, write

loader = importlib.machinery.SourceFileLoader('ops_replacement_native', str(Path(__file__).with_name('ops-controller-replacement')))
spec = importlib.util.spec_from_loader(loader.name, loader)
driver = importlib.util.module_from_spec(spec)
loader.exec_module(driver)
BASE = driver.BASE
RECORD = BASE / 'network.json'


def text(path, content):
    temporary = path.with_name('.' + path.name + '.network')
    with temporary.open('w') as stream:
        os.fchmod(stream.fileno(), 0o600); stream.write(content); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def vifs(guest):
    return ['bridge=' + n['bridge'] + ',mac=' + n['mac'] for n in guest['config']['nics']]


def render(boot):
    return ''.join(key + ' = ' + repr(value) + '\n' for key, value in boot.items())


def status():
    return driver.read(RECORD) if RECORD.exists() else {'stage': 'absent'}


def prepare(box, value):
    driver.require_legacy_owner()
    ops_network.validate(value)
    state = status()
    if state['stage'] not in ('absent', 'rolled-back'):
        if state['configuration'] != value: raise RuntimeError('resume the exact recorded controller network migration')
        return state
    current = driver.read(BASE / 'assignment.json')
    pending = driver.read(BASE / 'replacement.json') if (BASE / 'replacement.json').exists() else None
    if (current.get('box') != box or current.get('stage') != 'ready' or
            pending and (pending['stage'] != 'accepted' or pending.get('reboot_verification', {}).get('status') in ('required', 'pending'))):
        raise RuntimeError('finish the controller replacement and reboot verification before network migration')
    driver.require_disk(current); driver.require_boot(current)
    live = driver.require_live(current)
    if not live: raise RuntimeError('controller must be running before network migration')
    boot = driver.config_values(Path(current['work']) / 'ops.cfg')
    if len(boot['vif']) != 1 or not boot['vif'][0].startswith('bridge=br-ops,mac=') or vifs(live) != boot['vif']:
        raise RuntimeError('controller has an unexpected network attachment')
    for bridge in ('br-wan', 'br-ops'):
        if not (Path('/sys/class/net') / bridge / 'bridge').is_dir(): raise RuntimeError('required dom0 bridge is absent: ' + bridge)
    desired = copy.deepcopy(boot)
    desired['vif'].append('bridge=br-wan,mac=' + value['uplink']['mac'])
    state = {'kind': 'klokast.ops-network.v1', 'stage': 'prepared', 'box': box, 'configuration': value,
             'assignment_before': current, 'replacement_before': pending,
             'boot_before': boot, 'boot_after': desired,
             'live_boot_before': (driver.XEN / 'ops.cfg').read_text(),
             'protected_boot_before': (Path(current['work']) / 'ops.cfg').read_text()}
    write(RECORD, state)
    return state


def attach(state):
    live = driver.require_live(state['assignment_before'])
    if not live: raise RuntimeError('controller is not running')
    actual = vifs(live)
    if actual == state['boot_before']['vif']:
        run(['xl', 'network-attach', str(live['domid']), *state['boot_after']['vif'][-1].split(',')])
    elif actual != state['boot_after']['vif']:
        raise RuntimeError('unexpected controller interface; refuse attachment')
    if vifs(domain('ops')) != state['boot_after']['vif']: raise RuntimeError('controller uplink attachment did not match')
    state['stage'] = 'attached'; write(RECORD, state)
    return state


def commit(state):
    driver.require_legacy_owner()
    live = driver.require_live(state['assignment_before'])
    if not live or vifs(live) != state['boot_after']['vif']: raise RuntimeError('live controller network differs before commit')
    current = driver.read(BASE / 'assignment.json')
    # Keep the original disk, UUID and boot artifacts. Only this explicit
    # configuration revision may change the recorded NICs and boot checksum.
    if current != state['assignment_before'] and current.get('network') != state['configuration']:
        raise RuntimeError('controller assignment changed during network migration')
    old = state['assignment_before']; work = Path(old['work']); path = work / 'ops.cfg'
    if driver.config_values(path) not in (state['boot_before'], state['boot_after']):
        raise RuntimeError('controller boot file changed during network migration')
    text(path, render(state['boot_after']))
    current['boot_sha256'] = checksum(path)
    current['network'] = state['configuration']
    # The finalization input remains historical; replacement selects this
    # network recipe anew and accepted replay uses the revised boot hash.
    write(BASE / 'assignment.json', current)
    pending = state['replacement_before']
    if pending:
        pending = dict(pending, boot_sha256=current['boot_sha256'], network=state['configuration'])
        write(BASE / 'replacement.json', pending)
    driver.install_boot(work)
    state['stage'] = 'committed'; write(RECORD, state)
    return state


def rollback(state):
    driver.require_legacy_owner()
    if state['stage'] == 'accepted': raise RuntimeError('accepted network requires a new reviewed migration, not automatic rollback')
    live = driver.require_live(state['assignment_before'])
    if not live: raise RuntimeError('controller is not running; inspect before rollback')
    actual = vifs(live)
    if actual == state['boot_after']['vif']:
        run(['xl', 'network-detach', str(live['domid']), state['configuration']['uplink']['mac']])
    elif actual != state['boot_before']['vif']: raise RuntimeError('unexpected interface prevents exact rollback')
    old = state['assignment_before']; work = Path(old['work'])
    text(work / 'ops.cfg', render(state['boot_before']))
    # Preserve the exact original bytes because its protected hash binds them.
    # Stored boot-before was parsed, so retain the original source separately.
    if 'protected_boot_before' in state: text(work / 'ops.cfg', state['protected_boot_before'])
    if checksum(work / 'ops.cfg') != old['boot_sha256']: raise RuntimeError('original controller boot checksum differs')
    write(BASE / 'assignment.json', old)
    if state['replacement_before']: write(BASE / 'replacement.json', state['replacement_before'])
    text(driver.XEN / 'ops.cfg', state['live_boot_before']); run(['lbu', 'commit', '-d'])
    state['stage'] = 'rolled-back'; write(RECORD, state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'prepare', 'attach', 'commit', 'accept', 'rollback'))
    parser.add_argument('--box', required=True)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    if (not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', args.box) or os.geteuid() != 0 or
            socket.gethostname().split('.')[0] != args.box + '-dom0'):
        raise RuntimeError('controller network helper must run as root on the selected dom0')
    run(['mountpoint', '-q', '/mnt/dom0_data'])
    with (BASE / 'operation.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'status': state = status()
        elif args.action == 'prepare':
            safe_file(args.config, 65536)
            state = prepare(args.box, driver.read(args.config))
        else:
            state = status()
            if state.get('box') != args.box: raise RuntimeError('controller network migration is not prepared')
            if args.action == 'attach': state = attach(state) if state['stage'] == 'prepared' else state
            elif args.action == 'commit': state = commit(state) if state['stage'] != 'accepted' else state
            elif args.action == 'rollback': state = rollback(state)
            else:
                if state['stage'] not in ('committed', 'accepted'): raise RuntimeError('commit the verified network before acceptance')
                state['stage'] = 'accepted'; write(RECORD, state)
        print(json.dumps({'stage': state['stage'], 'box': args.box}))


if __name__ == '__main__':
    try: main()
    except (RuntimeError, ValueError, OSError) as error:
        print('ops-network-dom0: ' + str(error), file=sys.stderr); sys.exit(1)
