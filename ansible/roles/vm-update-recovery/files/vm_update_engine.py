"""VM transaction stages, recovery and assignment inspection."""
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import math
import os
from pathlib import (Path)
import re
import secrets
import signal
import socket
import stat
import time
import uuid
from vm_update_native import (BASE, XEN, OP, SERVICE_KIND, RETAINED_KIND, SERVICE_KINDS, ROLES, POST_BOOT, HEX, LV, PRE_ACCEPT, TERMINAL, HELPER, Refused, canonical, digest, unique, secure, syncdir, atomic, store, read, command, configuration, Native)
from vm_update_native import ROUTER_PROFILE


def router_contract(value, box):
    if (not isinstance(value, dict) or set(value) != {'kind', 'box', 'filesystem_uuid', 'accounts'} or
            value.get('kind') != 'klokast.router-retained-state.v1' or value.get('box') != box or
            not isinstance(value.get('filesystem_uuid'), str) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['filesystem_uuid']) or
            not isinstance(value.get('accounts'), dict) or
            set(value['accounts']) != {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'} or
            any(type(v) is not int or not 1 <= v <= 65534 for v in value['accounts'].values())):
        raise Refused('router retained-state contract is incomplete')
    return value


def router_legacy_base():
    return BASE.parent / 'klokast-router-updates'


def router_source(request):
    """Bind the first common operation to the exact immutable legacy assignment."""
    source = request['router_source']
    base = router_legacy_base()
    for name in ('pending.json', 'cold-test.json'):
        if (base / name).exists() or (base / name).is_symlink():
            raise Refused('finish the legacy router operation before common replacement')
    if source is None:
        return
    readiness = json.loads(command(['/usr/local/sbin/router-update-transaction', 'common-contract', '--box', request['box']]))
    if readiness != {'kind': 'klokast.router-common-contract.v1', 'engine_commit': request['engine_commit']}:
        raise Refused('install the matching legacy handoff guard before common router replacement')
    accepted = read(base / 'accepted.json')
    generation = read(base / 'records' / (source['generation_sha256'] + '.json'))
    for value, expected in ((accepted, source['assignment_sha256']), (generation, source['generation_sha256'])):
        if (value.get('record_sha256') != expected or
                digest({k: v for k, v in value.items() if k != 'record_sha256'}) != expected or
                value.get('box') != request['box'] or value.get('role') != 'router'):
            raise Refused('router migration source differs from protected legacy evidence')
    if (accepted.get('current_sha256') != source['generation_sha256'] or
            generation.get('xen', {}).get('uuid') != request['old_uuid'] or
            generation.get('accounts') != request['router_state']['accounts'] or
            request['old_disks'] != {generation['disk']['path']: {
                k: generation['disk'][k] for k in ('uuid', 'bytes')}} or
            request['old_artifacts'] != {v['path']: {k: v[k] for k in ('sha256', 'bytes')}
                                         for v in generation['boot'].values()}):
        raise Refused('router migration does not select the accepted legacy root')
    initial = base / 'installation.json'
    if initial.exists() and read(initial).get('stage') != 'verified':
        raise Refused('finish router first installation before migrating its state')
    topology = read(BASE.parent / 'klokast-infrastructure/ops/router-network.json')
    if (topology.get('stage') != 'accepted' or topology.get('box') != request['box'] or
            source.get('topology_sha256') != digest(topology) or
            topology.get('accepted_after') != accepted or
            topology.get('boot_after', {}).get('vif') != generation['xen']['vif']):
        raise Refused('router replacement requires the exact accepted controller network topology')

class Transaction:
    def __init__(self, work, backend=None, clock=time.time):
        self.work, self.native, self.clock = work, backend or Native(), clock
        self.request = read(work / 'request.json')
        r = self.request
        fields = {'kind', 'operation_id', 'box', 'role', 'engine_commit', 'policy_sha256',
                  'old_uuid', 'old_config_sha256', 'old_disks', 'old_artifacts', 'autostart'}
        adoption = isinstance(r, dict) and r.get('kind') == 'klokast.vm-adoption.v1'
        if adoption:
            fields.update({'qualification_sha256', 'source_evidence_sha256'})
        else:
            fields.update({'new_uuid', 'new_config_sha256', 'new_disks', 'new_artifacts', 'release_sha256'})
        if isinstance(r, dict) and r.get('kind') in {'klokast.vm-switch.v2', 'klokast.vm-switch.v3'}:
            fields.add('controller_timeout_seconds')
            if type(r.get('controller_timeout_seconds')) is not int or r['controller_timeout_seconds'] != 90:
                raise Refused('controller liveness requires the fixed 90-second timeout')
        if isinstance(r, dict) and r.get('kind') in {'klokast.vm-switch.v3', *SERVICE_KINDS}:
            fields.update({'replacement_seconds', 'recovery_seconds'})
            if any(type(r.get(key)) is not int or not 60 <= r[key] <= 86400 or r[key] % 60
                   for key in ('replacement_seconds', 'recovery_seconds')):
                raise Refused('transaction budgets must be positive whole minutes within one day')
        if isinstance(r, dict) and r.get('kind') in SERVICE_KINDS:
            fields.add('image_profile')
            profile = ROUTER_PROFILE if r.get('role') == 'router' else ('vpn-egress-alpine-v1' if r.get('role') == 'vpn-egress' else 'shared-alpine-v1')
            if r.get('image_profile') != profile:
                raise Refused('service image profile differs from its role')
        if isinstance(r, dict) and r.get('role') == 'router':
            fields.update({'router_state', 'router_source'})
            if r.get('kind') != RETAINED_KIND:
                raise Refused('router requires the common retained-disk transaction')
            router_contract(r.get('router_state'), r.get('box'))
            source = r.get('router_source')
            if source is not None and (not isinstance(source, dict) or
                    set(source) not in ({'assignment_sha256', 'generation_sha256'},
                                       {'assignment_sha256', 'generation_sha256', 'topology_sha256'}) or
                    any(not isinstance(v, str) or not HEX.fullmatch(v) for v in source.values())):
                raise Refused('router migration lacks its exact legacy source')
        if isinstance(r, dict) and r.get('kind') == RETAINED_KIND:
            fields.add('retained_disks')
            if r.get('role') not in {'bak', 'dmz', 'iot', 'router'}:
                raise Refused('persistent service disks require a shared guest or router role')
        self.replacement_seconds = r.get('replacement_seconds', 1800) if isinstance(r, dict) else 1800
        self.recovery_seconds = r.get('recovery_seconds', 1800) if isinstance(r, dict) else 1800
        if (not isinstance(r, dict) or set(r) != fields or r['kind'] not in
                {'klokast.vm-switch.v1', 'klokast.vm-switch.v2', 'klokast.vm-switch.v3', *SERVICE_KINDS, 'klokast.vm-adoption.v1'} or
                r['operation_id'] != work.name or not OP.fullmatch(work.name) or
                not isinstance(r['box'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', r['box']) or
                r['role'] not in ROLES or type(r['autostart']) is not bool or
                (r['role'] == 'vpn-egress' and r['kind'] != SERVICE_KIND) or
                not isinstance(r['engine_commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', r['engine_commit'])):
            raise Refused('invalid or unsupported transaction request')
        for key in (('policy_sha256', 'old_config_sha256', 'qualification_sha256', 'source_evidence_sha256')
                    if adoption else ('policy_sha256', 'old_config_sha256', 'new_config_sha256', 'release_sha256')):
            if not isinstance(r[key], str) or not HEX.fullmatch(r[key]):
                raise Refused('transaction has an invalid checksum')
        for key in (('old_uuid',) if adoption else ('old_uuid', 'new_uuid')):
            if not isinstance(r[key], str) or str(uuid.UUID(r[key])) != r[key]:
                raise Refused('transaction has an invalid domain UUID')
        if not adoption and r['old_uuid'] == r['new_uuid']:
            raise Refused('old and candidate Xen UUIDs must differ')
        self.configs = {}
        for side in (('old',) if adoption else ('old', 'new')):
            content = secure(work / (side + '.cfg')).read_bytes()
            if hashlib.sha256(content).hexdigest() != r[side + '_config_sha256']:
                raise Refused('staged Xen configuration checksum differs')
            config, paths = configuration(content.decode(), r['role'], r[side + '_uuid'])
            if side == 'new' and config.get('uuid') != r['new_uuid']:
                raise Refused('candidate configuration requires its exact recorded UUID')
            disks = r[side + '_disks']
            if not isinstance(disks, dict) or set(disks) != set(paths):
                raise Refused('disk identities differ from the Xen configuration')
            for expected in disks.values():
                if (not isinstance(expected, dict) or set(expected) != {'uuid', 'bytes'} or
                        not isinstance(expected['uuid'], str) or not re.fullmatch(r'[A-Za-z0-9-]{6,128}', expected['uuid']) or
                        type(expected['bytes']) is not int or expected['bytes'] <= 0):
                    raise Refused('logical volume identity is incomplete')
            self.configs[side] = config
            artifacts = r[side + '_artifacts']
            if not isinstance(artifacts, dict) or set(artifacts) != {config['kernel'], config['ramdisk']}:
                raise Refused('boot artifact identities differ from the Xen configuration')
            for item in artifacts.values():
                if (not isinstance(item, dict) or set(item) != {'sha256', 'bytes'} or
                        not isinstance(item['sha256'], str) or not HEX.fullmatch(item['sha256']) or
                        type(item['bytes']) is not int or not 0 < item['bytes'] <= 512 * 1024 * 1024):
                    raise Refused('boot artifact identity is incomplete')
        if r['kind'] == RETAINED_KIND:
            retained = r['retained_disks']
            # Mapping order comes from the checked Xen configuration, not JSON order.
            old_paths = configuration((work/'old.cfg').read_text(), r['role'], r['old_uuid'])[1]
            new_paths = configuration((work/'new.cfg').read_text(), r['role'], r['new_uuid'])[1]
            if (not isinstance(retained, dict) or len(retained) != 1 or
                    len(new_paths) != 2 or len(old_paths) not in (1, 2) or
                    retained != {new_paths[1]: r['new_disks'][new_paths[1]]} or
                    new_paths[0] in r['old_disks'] or old_paths[0] in retained or
                    (len(old_paths) == 2 and (old_paths[1] != new_paths[1] or
                     r['old_disks'][old_paths[1]] != retained[new_paths[1]]))):
                raise Refused('persistent data identity differs or overlaps an OS root')
            for side in ('old', 'new'):
                expected = ['xvda', 'xvdb'][:len(self.configs[side]['disk'])]
                if [entry.split(',')[1] for entry in self.configs[side]['disk']] != expected:
                    raise Refused('persistent data requires root xvda and data xvdb')
            if r['role'] == 'router' and ((len(old_paths) == 1) != (r['router_source'] is not None) or
                    next(iter(retained.values()))['bytes'] != 1024**3):
                raise Refused('router requires one 1 GiB data LV and an explicit first-migration source')
            if r['role'] == 'router':
                vifs = self.configs['old'].get('vif')
                if (not isinstance(vifs, list) or not vifs or vifs != self.configs['new'].get('vif') or
                        any(not isinstance(vif, str) or 'bridge=br-ops' in vif.split(',') for vif in vifs)):
                    raise Refused('router replacement must preserve topology after ops has an independent uplink')
        elif not adoption and set(r['old_disks']) & set(r['new_disks']):
            raise Refused('old and candidate writable disks must be disjoint')
        self.journal = read(work / 'journal.json') if (work / 'journal.json').exists() else None
        if self.journal and (self.journal.get('request_sha256') != digest(r) or
                             self.journal.get('stage') not in PRE_ACCEPT | TERMINAL or
                             type(self.journal.get('deadline')) not in {int, float} or
                             not math.isfinite(self.journal['deadline']) or
                             self.journal.get('recovery_seconds') != self.recovery_seconds):
            raise Refused('transaction journal differs from its immutable request')
        if self.journal and r['kind'] in {'klokast.vm-switch.v2', 'klokast.vm-switch.v3'}:
            if (self.journal.get('controller_timeout_seconds') != 90 or
                    type(self.journal.get('controller_seen_at')) not in {int, float} or
                    not math.isfinite(self.journal['controller_seen_at'])):
                raise Refused('transaction has invalid controller liveness evidence')
        elif self.journal and any(k in self.journal for k in ('controller_seen_at', 'controller_timeout_seconds')):
            raise Refused('legacy transaction cannot acquire controller liveness fields')
        if self.journal and 'old_shutdown_pending' in self.journal and type(self.journal['old_shutdown_pending']) is not bool:
            raise Refused('transaction shutdown state is invalid')
        previous = self.journal.get('previous_assignment') if self.journal else None
        if previous is not None:
            if (not isinstance(previous, dict) or
                    set(previous) != {'operation_id', 'request_sha256', 'release_sha256', 'profile'} or
                    not isinstance(previous['operation_id'], str) or not OP.fullmatch(previous['operation_id']) or
                    previous['operation_id'] == work.name or
                    not isinstance(previous['request_sha256'], str) or not HEX.fullmatch(previous['request_sha256']) or
                    ((previous['release_sha256'] is None) != (previous['profile'] is None)) or
                    (previous['release_sha256'] is not None and
                     (not isinstance(previous['release_sha256'], str) or not HEX.fullmatch(previous['release_sha256']) or
                      previous['profile'] not in ('shared-alpine-v1', 'vpn-egress-alpine-v1', ROUTER_PROFILE)))):
                raise Refused('previous accepted assignment identity is invalid')

    def artifacts(self, side):
        for path, expected in self.request[side + '_artifacts'].items():
            if self.native.artifact(path) != expected:
                raise Refused('recorded boot artifact was changed: ' + Path(path).name)

    def record(self, stage, **fields):
        value = dict(self.journal or {})
        value.update(fields, stage=stage, request_sha256=digest(self.request), updated_at=self.clock())
        store(self.work / 'journal.json', value)
        self.journal = value

    def disks(self, sides=('old', 'new')):
        actual = {}
        for side in sides:
            for path, expected in self.request[side + '_disks'].items():
                item = self.native.lv(path)
                if {k: item[k] for k in ('uuid', 'bytes')} != expected:
                    raise Refused('logical volume was replaced or resized during the transaction')
                actual[path] = item
        if len({v['device'] for v in actual.values()}) != len(actual):
            raise Refused('transaction disk device identities overlap')
        self.native.check_unmounted(actual)
        return actual

    def guest(self, sides=('old', 'new')):
        actual = self.disks(sides)
        found = None
        for record in self.native.inventory():
            if record['domid'] == 0:
                continue
            config = record['config']; info = config['c_info']
            disk_records = config.get('disks')
            if not isinstance(disk_records, list):
                raise Refused('Xen did not report guest disk attachments')
            used = {d.get('pdev_path') for d in disk_records}
            # Native disk paths can be device-mapper aliases. Resolve device
            # numbers before allowing any VM to attach a transaction disk.
            devices = set()
            for path in used:
                if not isinstance(path, str) or not path.startswith('/dev/'):
                    raise Refused('guest has an unsupported disk attachment')
                devices.add(self.native.device(path))
            overlaps = devices & {v['device'] for v in actual.values()}
            if info['name'] == self.request['role']:
                allowed_uuids = {self.request['old_uuid']}
                if 'new_uuid' in self.request:
                    allowed_uuids.add(self.request['new_uuid'])
                if found or info['uuid'] not in allowed_uuids:
                    raise Refused('target domain identity changed')
                side = 'old' if info['uuid'] == self.request['old_uuid'] else 'new'
                if side not in sides:
                    raise Refused('running guest uses the wrong accepted disk generation')
                expected = {actual[p]['device'] for p in self.request[side + '_disks']}
                if devices != expected:
                    raise Refused('target live disks differ from its recorded identity')
                mappings = {(self.native.device(d['pdev_path']), d.get('vdev'), d.get('readwrite'))
                            for d in disk_records}
                expected_mappings = set()
                for entry in self.configs[side]['disk']:
                    path, vdev, _mode = entry.removeprefix('phy:').split(',')
                    expected_mappings.add((actual[path]['device'], vdev, 1))
                if len(disk_records) != len(expected_mappings) or mappings != expected_mappings:
                    raise Refused('target live disk device names or write modes differ from its definition')
                found = record
            elif overlaps:
                raise Refused('another Xen guest holds a transaction disk')
        return found

    def assignment_content(self, side):
        config = dict(self.configs[side], uuid=self.request[side + '_uuid'])
        release = self.release_identity(side)
        provenance = {'kind': 'klokast.vm-boot-provenance.v1',
                      'operation_id': self.request['operation_id'], 'request_sha256': digest(self.request),
                      'engine_commit': self.request['engine_commit'],
                      'profile': release['profile'],
                      'selection': 'accepted' if side == 'new' else 'recorded-previous',
                      'release_sha256': release['release_sha256'],
                      'source_config_sha256': self.request[side + '_config_sha256']}
        # Alpine xendomains extracts double-quoted names with sed. Render
        # name explicitly so autostart recognizes an already recovered guest.
        return ('# klokast-provenance: ' + canonical(provenance) + '\n' +
                '\n'.join(key + ' = ' + (json.dumps(value) if key == 'name' else repr(value))
                          for key, value in config.items()) + '\n').encode()

    def release_identity(self, side):
        if side == 'new':
            return {'release_sha256': self.request['release_sha256'], 'profile': self.request.get('image_profile', 'shared-alpine-v1')}
        previous = (self.journal or {}).get('previous_assignment') or {}
        return {key: previous.get(key) for key in ('release_sha256', 'profile')}

    def assignment(self, side):
        # Persist the selected config before adding its normal autostart link.
        self.artifacts(side)
        atomic(XEN / (self.request['role'] + '.cfg'), self.assignment_content(side))
        link = XEN / 'auto' / (self.request['role'] + '.cfg')
        if link.is_symlink():
            if os.readlink(link) not in {'../' + link.name, str(XEN / link.name)}:
                raise Refused('autostart link has an unexpected destination')
            link.unlink()
        elif link.exists():
            raise Refused('autostart path is not a managed symbolic link')
        if self.runtime_intent()['runtime_state'] == 'running':
            link.symlink_to('../' + link.name)
        syncdir(link.parent)
        self.native.persist()

    def runtime_intent(self):
        path = self.work / 'runtime.json'
        if not path.exists() and not path.is_symlink():
            return {'runtime_state': 'running' if self.request['autostart'] else 'stopped'}
        value = read(path)
        if (not isinstance(value, dict) or
                set(value) != {'kind', 'request_sha256', 'intent_sha256', 'runtime_state'} or
                value['kind'] != 'klokast.vm-runtime-intent.v1' or
                value['request_sha256'] != digest(self.request) or
                not isinstance(value['intent_sha256'], str) or not HEX.fullmatch(value['intent_sha256']) or
                value['runtime_state'] not in {'running', 'stopped'} or
                not self.journal or self.journal['stage'] not in {'adopted', 'complete', 'recovered'}):
            raise Refused('runtime intent differs from the completed assignment')
        return value

    def arm(self):
        if self.request['kind'] == 'klokast.vm-adoption.v1':
            raise Refused('adoption record cannot arm a replacement')
        if self.journal is not None:
            raise Refused('operation has already been armed; do not replay it')
        if self.request['role'] == 'router':
            router_source(self.request)
            if self.request['router_source'] is None and not (BASE / 'active/router.json').exists():
                raise Refused('router data attachment requires a prior common assignment')
        pointer = BASE / 'active' / (self.request['role'] + '.json')
        previous_assignment = None
        for existing in sorted((BASE / 'active').iterdir()):
            if existing.name not in {role + '.json' for role in ROLES}:
                raise Refused('unexpected active transaction pointer')
            previous = read(existing)
            if set(previous) != {'operation_id', 'request_sha256'} or not OP.fullmatch(previous['operation_id']):
                raise Refused('active transaction pointer is invalid')
            older = Transaction(BASE / 'operations' / previous['operation_id'], self.native, self.clock)
            if (previous['request_sha256'] != digest(older.request) or
                    existing.name != older.request['role'] + '.json'):
                raise Refused('active transaction pointer does not match its request')
            if not older.journal or older.journal['stage'] not in {'adopted', 'complete', 'recovered'}:
                raise Refused('another guest transaction is not complete')
            if older.request['role'] == self.request['role']:
                side = 'new' if older.journal['stage'] == 'complete' else 'old'
                if (self.request['old_uuid'] != older.request[side + '_uuid'] or
                        self.request['old_disks'] != older.request[side + '_disks'] or
                        self.request['old_artifacts'] != older.request[side + '_artifacts'] or
                        self.request['old_config_sha256'] != hashlib.sha256(older.assignment_content(side)).hexdigest() or
                        self.request['autostart'] != (older.runtime_intent()['runtime_state'] == 'running')):
                    raise Refused('old generation differs from the protected previous assignment')
                if self.request['role'] == 'router' and older.request['router_state'] != self.request['router_state'] and side == 'new':
                    raise Refused('router replacement cannot change its retained disk identity or accounts')
                previous_assignment = dict(previous, **older.release_identity(side))
        active = secure(XEN / (self.request['role'] + '.cfg')).read_bytes()
        if hashlib.sha256(active).hexdigest() != self.request['old_config_sha256']:
            raise Refused('live Xen definition changed before arm')
        if self.request['role'] == 'router' and self.request['router_source'] is not None:
            generation = read(router_legacy_base() / 'records' / (self.request['router_source']['generation_sha256'] + '.json'))
            if self.configs['old'].get('vif') != generation['xen']['vif']:
                raise Refused('router migration must preserve the accepted network topology')
        guest = self.guest()
        if guest is None or guest['config']['c_info']['uuid'] != self.request['old_uuid']:
            raise Refused('arm requires the exact running old guest')
        self.artifacts('old'); self.artifacts('new')
        # The recovery record is durable before autostart or guest state changes.
        observed = self.clock()
        liveness = {'controller_seen_at': observed, 'controller_timeout_seconds': 90} if self.request['kind'] in {'klokast.vm-switch.v2', 'klokast.vm-switch.v3'} else {}
        self.record('armed', deadline=observed + self.replacement_seconds, recovery_seconds=self.recovery_seconds,
                    previous_assignment=previous_assignment, **liveness)
        store(pointer, {'operation_id': self.request['operation_id'], 'request_sha256': digest(self.request)})
        watcher = self.native.watch(self.request['operation_id'], self.work)
        self.record('armed', watcher=watcher)

    def adopt_old(self):
        """Record a running old generation without changing its disks or guest.

        The signed controller executor validates authority before staging the
        request. An interrupted publication is repaired at boot.
        """
        if self.request['kind'] not in {'klokast.vm-adoption.v1', 'klokast.vm-switch.v2'} or not self.request['autostart']:
            raise Refused('adoption requires a running guest and the current recovery contract')
        if self.journal is not None:
            raise Refused('adoption operation has already been recorded')
        pointer = BASE / 'active' / (self.request['role'] + '.json')
        if pointer.exists() or pointer.is_symlink():
            raise Refused('guest already has an active disk assignment')
        for existing in (BASE / 'active').iterdir():
            if existing.name not in {role + '.json' for role in ROLES}:
                raise Refused('unexpected active transaction pointer')
            value = read(existing)
            if (set(value) != {'operation_id', 'request_sha256'} or
                    not isinstance(value['operation_id'], str) or
                    not OP.fullmatch(value['operation_id'])):
                raise Refused('another assignment pointer is invalid')
            older = Transaction(BASE / 'operations' / value['operation_id'], self.native, self.clock)
            if (value['request_sha256'] != digest(older.request) or
                    existing.name != older.request['role'] + '.json' or
                    not older.journal or older.journal['stage'] not in {'adopted', 'complete', 'recovered'}):
                raise Refused('another guest transaction is not complete')
        active = secure(XEN / (self.request['role'] + '.cfg')).read_bytes()
        if hashlib.sha256(active).hexdigest() != self.request['old_config_sha256']:
            raise Refused('live Xen definition changed before adoption')
        link = XEN / 'auto' / (self.request['role'] + '.cfg')
        if not link.is_symlink() or os.readlink(link) not in {'../' + link.name, str(XEN / link.name)}:
            raise Refused('running old guest lacks its recorded autostart link')
        guest = self.guest(('old',))
        if guest is None or guest['config']['c_info']['uuid'] != self.request['old_uuid']:
            raise Refused('adoption requires the exact running old guest')
        self.artifacts('old')
        observed = self.clock()
        fields = {'deadline': observed + self.replacement_seconds, 'recovery_seconds': self.recovery_seconds,
                  'previous_assignment': None}
        if self.request['kind'] in {'klokast.vm-switch.v2', 'klokast.vm-switch.v3'}:
            fields.update(controller_seen_at=observed, controller_timeout_seconds=90)
        self.record('adopted', **fields)
        store(pointer, {'operation_id': self.request['operation_id'],
                        'request_sha256': digest(self.request)})
        self.assignment('old')

    def step(self, action):
        if not self.journal:
            raise Refused('transaction is not armed')
        if action in {'recover', 'boot-recover'}:
            return self.recover()
        if (self.journal['stage'] in PRE_ACCEPT and self.clock() >= self.journal['deadline'] and
                not (self.request['kind'] in SERVICE_KINDS and self.journal['stage'] in POST_BOOT)):
            raise Refused('replacement exceeded its recorded budget; local recovery is required')
        if controller_lost(self.journal, self.clock()):
            raise Refused('controller liveness expired; local recovery is required')
        if action == 'heartbeat':
            if (self.request['kind'] not in {'klokast.vm-switch.v2', 'klokast.vm-switch.v3'} or
                    self.journal['stage'] not in PRE_ACCEPT - {'recovering'}):
                raise Refused('heartbeat requires a live pre-acceptance v2 transaction')
            self.record(self.journal['stage'], controller_seen_at=self.clock())
        elif action == 'stop' and self.journal['stage'] == 'armed':
            if not self.native.watcher_alive(self.journal.get('watcher'), self.request['operation_id']):
                raise Refused('bounded local recovery is not running; old guest remains in place')
            self.record('stopping', old_shutdown_pending=True)
            guest = self.guest()
            if guest:
                if guest['config']['c_info']['uuid'] != self.request['old_uuid']:
                    raise Refused('candidate appeared before old guest stopped')
                self.native.stop(guest, True)
            if self.guest() is not None:
                raise Refused('old guest still holds its disks')
            self.record('stopped', old_shutdown_pending=False)
        elif action == 'start' and self.journal['stage'] == 'stopped':
            if self.guest() is not None:
                raise Refused('a guest appeared before candidate start')
            self.artifacts('new')
            if self.request['role'] == 'router':
                proof = read(self.work / 'router-state-ready.json')
                if proof != {'request_sha256': digest(self.request), 'contract': self.request['router_state'],
                             'operation_id': self.work.name, 'ready': True}:
                    raise Refused('router candidate lacks completed retained-state finalization')
            self.record('starting')
            # Service updates cross their write boundary before normal boot.
            # Historical formats retain their original pre-acceptance rollback.
            if self.request['kind'] in SERVICE_KINDS:
                self.assignment('new')
            self.native.start(self.work / 'new.cfg')
            guest = self.guest()
            if guest is None or guest['config']['c_info']['uuid'] != self.request['new_uuid']:
                raise Refused('candidate did not start with the recorded identity')
            self.record('booted')
        elif action == 'start' and self.request['kind'] in SERVICE_KINDS and self.journal['stage'] in {'starting', 'booted'}:
            # A lost create response is not permission to select the old disks.
            self.assignment('new')
            if self.guest(('new',)) is None:
                self.native.start(self.work / 'new.cfg')
            if self.guest(('new',)) is None:
                raise Refused('recorded service generation did not start')
            self.record('booted')
        elif action == 'tested' and self.journal['stage'] == 'booted':
            # A controller-side executor is responsible for the test evidence.
            # This internal command is root-only, not an approval interface.
            evidence = read(self.work / 'checks.json')
            # The selected profile has no application workload. Its controller
            # record proves current no-application qualification instead of
            # falsely recording an omitted application compatibility test.
            required = {'boot', 'kernel_modules', 'tailscale', 'rootless_podman', 'firewall',
                        'no_application_qualification', 'data', 'application_images_unchanged', 'quarantine'}
            if self.request['kind'] in SERVICE_KINDS:
                required = {'boot', 'kernel_modules', 'identity', 'firewall', 'retained_state',
                            'workloads_unchanged', 'service', 'reboot'}
            if self.request['role'] == 'router':
                required = {'boot', 'kernel_modules', 'identity', 'retained_state', 'firewall',
                            'forwarding', 'isolation', 'dhcp_dns', 'wan', 'management', 'reboot'}
            if (set(evidence) != {'request_sha256', 'checks'} or evidence['request_sha256'] != digest(self.request) or
                    set(evidence['checks']) != required or any(v is not True for v in evidence['checks'].values())):
                raise Refused('controller compatibility evidence is incomplete')
            self.record('tested', checks_sha256=digest(evidence))
        elif action == 'accept' and self.journal['stage'] == 'tested':
            evidence = read(self.work / 'checks.json')
            if digest(evidence) != self.journal['checks_sha256']:
                raise Refused('test evidence changed before acceptance')
            guest = self.guest()
            if guest is None or guest['config']['c_info']['uuid'] != self.request['new_uuid']:
                raise Refused('candidate is absent before acceptance')
            self.artifacts('new')
            # Durable acceptance MUST precede assignment publication and all
            # production writes. Recovery never rolls this record backward.
            self.record('accepted')
            self.assignment('new')
        elif action == 'complete' and self.journal['stage'] == 'accepted':
            if self.guest(('new',)) is None:
                raise Refused('accepted guest is absent; completion requires verification')
            self.assignment('new')
            self.record('complete')
        else:
            raise Refused('operation cannot skip, repeat, or reverse a transaction stage')

    def recover(self):
        if not self.journal:
            raise Refused('cannot recover an unarmed operation')
        stage = self.journal['stage']
        if self.request['kind'] in SERVICE_KINDS and stage in POST_BOOT:
            self.guest(('new',))
            self.assignment('new')
            return 'preserve-service-generation'
        if stage in {'accepted', 'complete'}:
            # A crash can interrupt config publication after acceptance.
            # Old LVs can later be retired. The selected LVs must still have
            # their recorded identities before Xen can autostart them.
            self.guest(('new',))
            self.assignment('new')
            return 'preserve-production-data'
        if stage in {'adopted', 'recovered'}:
            self.guest(('old',))
            self.assignment('old')
            return stage
        if stage == 'recovery-failed':
            raise Refused('recovery previously failed; keep the VM fenced for inspection')
        started = self.journal.get('recovery_started_at', self.clock())
        pending_shutdown = self.journal.get('old_shutdown_pending', stage == 'stopping')
        self.record('recovering', recovery_started_at=started, old_shutdown_pending=pending_shutdown)
        try:
            if self.clock() >= started + self.recovery_seconds:
                raise Refused('local recovery exceeded its recorded budget')
            guest = self.guest()
            if guest and guest['config']['c_info']['uuid'] == self.request['new_uuid']:
                self.native.stop(guest, False)
            guest = self.guest()
            if guest and guest['config']['c_info']['uuid'] != self.request['old_uuid']:
                raise Refused('candidate still holds disks during recovery')
            if pending_shutdown:
                # An interrupted xl shutdown can finish after this method
                # returns. Do not declare that still-running old VM recovered
                # and then lose it to the outstanding shutdown request.
                if guest:
                    self.native.stop(guest, True)
                guest = self.guest()
                if guest is not None:
                    raise Refused('old guest shutdown is still pending; recovery cannot restart it')
                # Persist completion before starting old disks. A reboot or
                # process crash cannot turn the new start into another stop.
                self.record('recovering', old_shutdown_pending=False)
            self.assignment('old')
            if guest is None:
                self.native.start(XEN / (self.request['role'] + '.cfg'))
            guest = self.guest()
            if guest is None or guest['config']['c_info']['uuid'] != self.request['old_uuid']:
                raise Refused('old guest did not restart with the recorded identity')
            if self.clock() >= started + self.recovery_seconds:
                raise Refused('local recovery exceeded its recorded budget')
            self.record('recovered')
            return 'recovered'
        except Exception:
            self.record('recovery-failed')
            raise


@contextmanager
def operation_lock(work, *, directory=None):
    # Serialize short disk transitions on the box, including role-pointer
    # publication. The watchdog never holds this lock while sleeping.
    descriptor = os.open((directory or BASE) / 'transaction.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise Refused('unsafe operation lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def budget(seconds):
    """Bound the whole operation, including lock waits and native commands."""
    def expired(signum, frame):
        raise Refused('local transaction time budget expired')
    started = time.monotonic()
    outer_seconds, outer_interval = signal.getitimer(signal.ITIMER_REAL)
    previous = signal.signal(signal.SIGALRM, expired)
    # A nested transition must not restart the outer command's budget after
    # it has already spent time waiting for the box lock.
    effective = min(seconds, outer_seconds) if outer_seconds else seconds
    signal.setitimer(signal.ITIMER_REAL, max(0.001, effective))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if outer_seconds:
            remaining = max(0.001, outer_seconds - (time.monotonic() - started))
            signal.setitimer(signal.ITIMER_REAL, remaining, outer_interval)


def require_dom0():
    if os.geteuid() != 0 or 'control_d' not in Path('/proc/xen/capabilities').read_text():
        raise Refused('run as root inside the approved dom0 workflow')
    secure(BASE, True)
    secure(BASE / 'operations', True)
    secure(BASE / 'active', True)
    # A mountpoint alone is insufficient: a tmpfs or a bind mount would lose
    # recovery records at reboot. Require the declared ordinary ext4 LVM mount.
    mounts = [line.split(' - ', 1) for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    matches = [(left.split(), right.split()) for left, right in mounts
               if left.split()[4] == '/mnt/dom0_data']
    if len(matches) != 1:
        raise Refused('persistent dom0 data volume is not mounted exactly once')
    mount, filesystem = matches[0]
    if (mount[3] != '/' or 'rw' not in mount[5].split(',') or filesystem[0] != 'ext4' or
            not LV.fullmatch(filesystem[1])):
        raise Refused('recovery storage requires the writable ext4 dom0 data LV')
    major, minor = map(int, mount[2].split(':'))
    device = os.makedev(major, minor)
    if (Native().lv(filesystem[1])['device'] != device or
            any(path.stat().st_dev != device for path in (BASE, BASE / 'operations', BASE / 'active'))):
        raise Refused('recovery records are not on the mounted persistent data LV')


def fence_autostart():
    """Fence managed services; an unconverted router and ops keep recovering."""
    for role in sorted(ROLES):
        if role == 'router' and not ((BASE / 'active/router.json').exists() or (BASE / 'active/router.json').is_symlink()):
            continue
        path = XEN / 'auto' / (role + '.cfg')
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            # Retain unexpected file bytes outside the autostart directory.
            path.rename(XEN / (role + '.update-quarantine-' + secrets.token_hex(8)))
    syncdir(XEN / 'auto')


def controller_lost(state, now):
    """Dom0 timestamps only. Backward clock movement fails closed."""
    if state['stage'] not in PRE_ACCEPT or 'controller_timeout_seconds' not in state:
        return False
    return not 0 <= now - state['controller_seen_at'] < state['controller_timeout_seconds']


@contextmanager
def current_assignment(role, native=None, *, lock_held=False):
    if role not in ROLES:
        raise Refused('assignment requires a supported shared VM role')
    pointer = BASE / 'active' / (role + '.json')
    with budget(180), (nullcontext() if lock_held else operation_lock(BASE)):
        if not pointer.exists() and not pointer.is_symlink():
            yield None
            return
        selection = read(pointer)
        if (not isinstance(selection, dict) or set(selection) != {'operation_id', 'request_sha256'} or
                not isinstance(selection['operation_id'], str) or not OP.fullmatch(selection['operation_id'])):
            raise Refused('current boot assignment pointer is invalid')
        work = secure(BASE / 'operations' / selection['operation_id'], True)
        tx = Transaction(work, native)
        if (tx.request['role'] != role or selection['request_sha256'] != digest(tx.request) or
                socket.gethostname().split('.')[0] != tx.request['box'] + '-dom0' or not tx.journal):
            raise Refused('current boot assignment differs from its operation or box')
        yield tx
        if read(pointer) != selection:
            raise Refused('current boot assignment changed during inspection')


def assignment_report(tx):
    stage = tx.journal['stage']
    role = tx.request['role']
    side = 'new' if stage in {'accepted', 'complete'} else ('old' if stage in {'adopted', 'recovered'} else None)
    release = tx.release_identity(side) if side else {'release_sha256': None, 'profile': None}
    result = {'kind': 'klokast.vm-boot-assignment.v1', 'role': role, 'managed': True,
              'operation_id': tx.work.name, 'request_sha256': digest(tx.request),
              'stage': stage, 'selection': 'accepted' if side == 'new' else ('recorded-previous' if side else 'pending'),
              'release_sha256': release['release_sha256'],
              'engine_commit': tx.request['engine_commit'],
              'profile': release['profile']}
    if side:
        tx.artifacts(side)
        guest = tx.guest((side,))
        expected = tx.assignment_content(side)
        path = XEN / (role + '.cfg')
        current = secure(path).read_bytes() if path.exists() or path.is_symlink() else None
        link = XEN / 'auto' / (role + '.cfg')
        autostart = tx.runtime_intent()['runtime_state'] == 'running'
        valid_link = link.is_symlink() and os.readlink(link) in {'../' + link.name, str(XEN / link.name)}
        autostart_drift = not valid_link if autostart else link.exists() or link.is_symlink()
        result.update(vm_uuid=tx.request[side + '_uuid'],
                      boot_configuration=tx.configs[side], disks=tx.request[side + '_disks'],
                      artifacts=tx.request[side + '_artifacts'], autostart=autostart,
                      runtime='running' if guest else 'stopped',
                      configuration_sha256=hashlib.sha256(expected).hexdigest(),
                      configuration_drift=current != expected, autostart_drift=bool(autostart_drift))
    return result


def assignment_status(role, native=None):
    """Read the protected selection and drift; never approve observed settings."""
    with current_assignment(role, native) as tx:
        if tx is None:
            return {'kind': 'klokast.vm-boot-assignment.v1', 'role': role, 'managed': False}
        return assignment_report(tx)


def router_owner(native=None):
    """Read the common boot selection, including an unaccepted started candidate."""
    with current_assignment('router', native) as tx:
        if tx is None:
            return {'owner': 'legacy-router', 'managed': False}
        for name in ('pending.json', 'cold-test.json'):
            path = router_legacy_base() / name
            if path.exists() or path.is_symlink():
                raise Refused('router has conflicting legacy and common operations')
        stage = tx.journal['stage']
        side = 'new' if stage in POST_BOOT else 'old' if stage == 'recovered' else None
        ready = False
        if side is not None:
            tx.disks((side,)); tx.artifacts(side); tx.guest((side,))
            link = XEN / 'auto/router.cfg'
            enabled = tx.runtime_intent()['runtime_state'] == 'running'
            correct_link = (link.is_symlink() and os.readlink(link) in ('../router.cfg', str(XEN / 'router.cfg')))
            ready = (secure(XEN / 'router.cfg').read_bytes() == tx.assignment_content(side) and
                     (correct_link if enabled else not (link.exists() or link.is_symlink())))
        return {'owner': 'common-vm', 'managed': True, 'operation_id': tx.work.name,
                'stage': stage, 'selection': side, 'boot_ready': ready}


def replacement_source_status(role, native=None):
    """Read accepted disks and protected personalization; never adopt observations."""
    with current_assignment(role, native) as tx:
        if tx is None:
            raise Refused('recurring maintenance requires an accepted assignment')
        assignment = assignment_report(tx)
        if (assignment['stage'] != 'complete' or assignment['selection'] != 'accepted' or
                assignment['runtime'] != 'running' or not assignment['autostart'] or
                assignment['configuration_drift'] or assignment['autostart_drift']):
            raise Refused('recurring maintenance requires a complete running assignment without drift')
        operation = tx.work.name
        root = Path('/mnt/dom0_data/klokast-vm-supervised')
        for path in (root, root / operation, root / operation / 'personalize', root / operation / 'finalize'):
            secure(path, True)
        work = root / operation
        prepared = read(work / 'prepared.json')
        personalized = read(work / 'personalize/request.json')
        personal_result = read(work / 'personalize/result.json')
        personal_receipt = personal_result.get('receipt', {})
        final = read(work / 'finalize/result.json')
        release = read(work / 'release.json')
        machine = personalized.get('request', {})
        receipt = final.get('receipt', {})
        if (prepared.get('operation_id') != operation or prepared.get('box') != tx.request['box'] or
                prepared.get('role') != role or prepared.get('receipt_sha256') !=
                digest({k: v for k, v in prepared.items() if k != 'receipt_sha256'}) or
                personalized.get('mode') != 'personalize' or personalized.get('operation_id') != operation or
                machine.get('kind') != 'klokast.vm-personalize.v2' or machine.get('operation_id') != operation or
                machine.get('box') != tx.request['box'] or machine.get('role') != role or
                machine.get('root_uuid') != prepared.get('root_uuid') or
                machine.get('retained_uuid') != prepared.get('data_uuid') or
                final.get('success') is not True or final.get('mode') != 'finalize' or
                final.get('operation_id') != operation or receipt.get('copy_verified') is not True or
                receipt.get('kind') != 'klokast.vm-retained-final-result.v3' or
                receipt.get('receipt_sha256') != digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}) or
                machine.get('retained_receipt_sha256') != receipt.get('receipt_sha256') or
                release.get('release_sha256') != assignment['release_sha256'] or
                release.get('release_sha256') != digest({k: v for k, v in release.items() if k != 'release_sha256'}) or
                machine.get('release_sha256') != release['release_sha256'] or
                machine.get('packages') != release.get('packages') or
                machine.get('inputs_sha256') != release.get('inputs_sha256')):
            raise Refused('accepted generation lacks matching protected data and personalization records')
        def personal_digest(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
        # Use completed personalization evidence, including account and boot
        # configuration hashes. Never return the request's password hash.
        file_names = set(machine['files']) | {
            'etc/passwd', 'etc/group', 'etc/shadow', 'etc/subuid', 'etc/subgid',
            'etc/fstab', 'etc/conf.d/tailscale', 'etc/doas.d/doas.conf',
            'etc/klokast/app-resources/vm-input.d/000-empty.nft',
            *('etc/ssh/ssh_host_' + key + '_key' for key in ('rsa', 'ecdsa', 'ed25519'))}
        measured = personal_receipt.get('files')
        if (personal_result.get('success') is not True or personal_result.get('mode') != 'personalize' or
                personal_result.get('operation_id') != operation or
                personal_result.get('request_sha256') != personal_digest(personalized) or
                personal_receipt.get('kind') != 'klokast.vm-personalization-result.v2' or
                personal_receipt.get('request_sha256') != personal_digest(machine) or
                personal_receipt.get('packages_unchanged') is not True or
                any(personal_receipt.get(key) != machine[key] for key in
                    ('operation_id', 'release_sha256', 'inputs_sha256', 'engine_commit',
                     'root_uuid', 'retained_uuid', 'runtime', 'retained_receipt_sha256')) or
                personal_receipt.get('hostname') != tx.request['box'] + '-' + role or
                not isinstance(measured, dict) or set(measured) != file_names or
                any(not isinstance(row, dict) or set(row) != {'sha256', 'mode'} or
                    not isinstance(row['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']) or
                    type(row['mode']) is not int or row['mode'] not in {0o600, 0o644, 0o755}
                    for row in measured.values()) or
                any(measured[path]['sha256'] != hashlib.sha256(content.encode()).hexdigest()
                    for path, content in machine['files'].items())):
            raise Refused('accepted generation lacks a matching completed personalization result')
        generated_files = {path: row['sha256'] for path, row in measured.items()}
        generated_files['etc/klokast-personalization.json'] = hashlib.sha256(
            (json.dumps(personal_receipt, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()).hexdigest()
        expected_disks = {prepared[key]['path']: {field: prepared[key][field] for field in ('uuid', 'bytes')}
                          for key in ('root', 'data')}
        mappings = [dict(path=prepared[key]['path'], vdev=vdev, mode='w')
                    for key, vdev in (('root', 'xvda'), ('data', 'xvdb'))]
        expected_config = ['phy:' + row['path'] + ',' + row['vdev'] + ',w' for row in mappings]
        if assignment['disks'] != expected_disks or assignment['boot_configuration']['disk'] != expected_config:
            raise Refused('accepted OS and retained disks differ from their prepared generation')
        # No password hash or private machine bytes leave this root reader.
        return {'kind': 'klokast.vm-replacement-source.v1', 'role': role,
                'dom0': socket.gethostname().split('.')[0], 'observed_at': int(time.time()),
                'operation_id': operation, 'request_sha256': assignment['request_sha256'],
                'vm_uuid': assignment['vm_uuid'], 'configuration_sha256': assignment['configuration_sha256'],
                'disks': assignment['disks'], 'disk_mappings': {row['path']: row['vdev'] for row in mappings},
                'artifacts': assignment['artifacts'], 'autostart': True, 'runtime': 'running',
                'release': release, 'machine': {key: machine[key] for key in
                    ('root_uuid', 'retained_uuid', 'runtime', 'retained_receipt_sha256')},
                'files_sha256': generated_files}


def source_status(role, native=None):
    """Bind an unmanaged running guest to its exact disk and boot artifacts.

    This read-only receipt is adoption evidence, not an assignment. A current
    transaction pointer makes the old-source path invalid even if it is stale.
    """
    if role not in ROLES:
        raise Refused('source inspection requires a supported shared VM role')
    native = native or Native()
    with budget(180), operation_lock(BASE):
        pointer = BASE / 'active' / (role + '.json')
        if pointer.exists() or pointer.is_symlink():
            raise Refused('source inspection requires an unmanaged guest')
        secure(XEN, True)
        secure(XEN / 'auto', True)
        path = secure(XEN / (role + '.cfg'))
        content = path.read_bytes()
        inventory = native.inventory()
        matches = [record for record in inventory if record['domid'] != 0 and
                   record['config']['c_info']['name'] == role]
        if len(matches) != 1:
            raise Refused('source inspection requires exactly one running guest')
        guest = matches[0]
        identity = guest['config']['c_info']['uuid']
        config, paths = configuration(content.decode(), role, identity)
        disks = {disk: native.lv(disk) for disk in paths}
        if len({item['device'] for item in disks.values()}) != len(disks):
            raise Refused('source disk device identities overlap')
        native.check_unmounted(disks)
        expected = {}
        for entry in config['disk']:
            disk, vdev, _mode = entry.removeprefix('phy:').split(',')
            expected[native.device(disk), vdev, 1] = disk
        for record in inventory:
            if record['domid'] == 0:
                continue
            attachments = record['config'].get('disks')
            if not isinstance(attachments, list):
                raise Refused('Xen did not report source disk attachments')
            mappings = set()
            for attachment in attachments:
                disk = attachment.get('pdev_path') if isinstance(attachment, dict) else None
                if not isinstance(disk, str) or not disk.startswith('/dev/'):
                    raise Refused('guest has an unsupported disk attachment')
                mappings.add((native.device(disk), attachment.get('vdev'), attachment.get('readwrite')))
            overlap = {entry[0] for entry in mappings} & {item['device'] for item in disks.values()}
            if record is guest:
                if len(attachments) != len(expected) or set(expected) != mappings:
                    raise Refused('running source disk mappings differ from its Xen definition')
            elif overlap:
                raise Refused('another Xen guest holds a source disk')
        artifacts = {config[name]: native.artifact(config[name]) for name in ('kernel', 'ramdisk')}
        link = XEN / 'auto' / (role + '.cfg')
        if link.is_symlink():
            if os.readlink(link) not in {'../' + link.name, str(path)}:
                raise Refused('source autostart link differs from its Xen definition')
            autostart = True
        elif link.exists():
            raise Refused('source autostart entry is not a supported link')
        else:
            autostart = False
        return {'kind': 'klokast.vm-unmanaged-source.v1', 'role': role,
                'dom0': socket.gethostname().split('.')[0], 'observed_at': int(time.time()),
                'vm_uuid': identity, 'configuration_sha256': hashlib.sha256(content).hexdigest(),
                'disks': {path: {key: value[key] for key in ('uuid', 'bytes')}
                          for path, value in disks.items()},
                'disk_mappings': {path: vdev for (_device, vdev, _write), path in expected.items()},
                'artifacts': artifacts, 'autostart': autostart, 'runtime': 'running'}


def stage_adoption(request):
    """Stage only the exact running old source, then publish its assignment."""
    if (not isinstance(request, dict) or request.get('kind') != 'klokast.vm-adoption.v1' or
            not isinstance(request.get('operation_id'), str) or not OP.fullmatch(request['operation_id']) or
            request.get('role') not in {'dmz', 'iot'} or request.get('box') not in {'k001', 'k002'} or
            (request['box'], request['role']) not in {('k001', 'dmz'), ('k002', 'dmz'), ('k002', 'iot')}):
        raise Refused('adoption request must select one approved no-application target')
    work = BASE / 'operations' / request['operation_id']
    if work.exists() or work.is_symlink():
        with current_assignment(request['role']) as existing:
            if (existing is None or existing.work != work or existing.request != request or
                    existing.journal['stage'] != 'adopted'):
                raise Refused('adoption operation ID exists without the same durable assignment')
            report = assignment_report(existing)
            if report['configuration_drift'] or report['autostart_drift'] or report['runtime'] != 'running':
                raise Refused('recorded old VM generation has drifted')
            return existing.journal
    source = source_status(request['role'])
    if (source['dom0'] != request['box'] + '-dom0' or source['runtime'] != 'running' or
            source['autostart'] is not True or request.get('autostart') is not True or
            request.get('old_uuid') != source['vm_uuid'] or
            request.get('old_config_sha256') != source['configuration_sha256'] or
            request.get('old_disks') != source['disks'] or
            request.get('old_artifacts') != source['artifacts']):
        raise Refused('adoption source changed from the signed controller request')
    work.mkdir(mode=0o700)
    syncdir(work.parent)
    content = secure(XEN / (request['role'] + '.cfg')).read_bytes()
    if hashlib.sha256(content).hexdigest() != request['old_config_sha256']:
        raise Refused('Xen definition changed while staging adoption')
    atomic(work / 'old.cfg', content)
    store(work / 'request.json', request)
    # Validate the closed request before it can publish a role pointer.
    Transaction(work)
    return invoke(request['operation_id'], 'adopt')


def reconcile_assignment(role, request_sha256, runtime_state, intent_sha256, native=None):
    """Root-only primitive for the existing approved runtime Apply workflow.

    The caller must validate current desired state and general Apply authority.
    This primitive cannot choose a release or change disk, network, or package
    definitions. The box lock covers selection, rendering, and native transitions.
    """
    if (runtime_state not in {'running', 'stopped'} or
            any(not isinstance(v, str) or not HEX.fullmatch(v) for v in (request_sha256, intent_sha256))):
        raise Refused('runtime reconciliation requires exact assignment and intent checksums')
    with current_assignment(role, native) as tx:
        if tx is None or digest(tx.request) != request_sha256:
            raise Refused('runtime reconciliation assignment changed or is absent')
        if tx.journal['stage'] not in {'adopted', 'complete', 'recovered'}:
            raise Refused('runtime reconciliation requires a completed operation')
        before = assignment_report(tx)
        value = {'kind': 'klokast.vm-runtime-intent.v1', 'request_sha256': request_sha256,
                 'intent_sha256': intent_sha256, 'runtime_state': runtime_state}
        previous = tx.runtime_intent()
        changed = (previous != value or before['configuration_drift'] or before['autostart_drift'] or
                   before['runtime'] != runtime_state)
        if previous != value:
            # Intent survives loss of the controller and is used by boot recovery.
            # It cannot modify the immutable accepted release or operation request.
            store(tx.work / 'runtime.json', value)
        side = 'new' if tx.journal['stage'] == 'complete' else 'old'
        if changed:
            tx.assignment(side)
        guest = tx.guest((side,))
        if runtime_state == 'stopped' and guest:
            tx.native.stop(guest, True)
        elif runtime_state == 'running' and not guest:
            tx.native.start(XEN / (role + '.cfg'))
        result = assignment_report(tx)
        if result['runtime'] != runtime_state or result['configuration_drift'] or result['autostart_drift']:
            raise Refused('accepted VM did not reach its approved runtime state')
        return dict(result, changed=changed, intent_sha256=intent_sha256)


def invoke(operation, action):
    if not OP.fullmatch(operation):
        raise Refused('invalid operation ID')
    work = secure(BASE / 'operations' / operation, True)
    # Read immutable request limits before waiting, then revalidate under lock.
    limit = Transaction(work).recovery_seconds if action in {'recover', 'boot-recover'} else 180
    with budget(limit), operation_lock(work), router_operation_lock(work):
        tx = Transaction(work)
        if socket.gethostname().split('.')[0] != tx.request['box'] + '-dom0':
            raise Refused('operation belongs to another box')
        if action == 'adopt':
            if tx.request['kind'] != 'klokast.vm-adoption.v1':
                raise Refused('public adoption requires the old-only v1 request')
            tx.adopt_old()
        elif action == 'arm':
            tx.arm()
        elif action != 'status':
            if read(BASE / 'active' / (tx.request['role'] + '.json')) != {
                    'operation_id': operation, 'request_sha256': digest(tx.request)}:
                raise Refused('historical operation cannot change the active guest')
            if not tx.journal:
                raise Refused('transaction is not armed')
            seconds = 180
            if action in {'recover', 'boot-recover'} and tx.journal['stage'] in PRE_ACCEPT:
                seconds = max(0, tx.journal.get('recovery_started_at', time.time()) + tx.recovery_seconds - time.time())
            elif (tx.journal['stage'] in PRE_ACCEPT and
                  not (tx.request['kind'] in SERVICE_KINDS and tx.journal['stage'] in POST_BOOT)):
                seconds = min(seconds, max(0, tx.journal['deadline'] - time.time()))
            with budget(seconds):
                tx.step(action)
        return tx.journal


@contextmanager
def router_operation_lock(work):
    """Order: common lock, then legacy router lock; no legacy writer can race arm."""
    request = read(work / 'request.json')
    if request.get('role') != 'router':
        yield
        return
    base = secure(router_legacy_base(), True)
    with operation_lock(base, directory=base):
        yield
