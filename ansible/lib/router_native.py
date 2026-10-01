"""Bounded dom0 primitives for the fixed router profile; no authority decisions."""
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import time

import router_generations as generations
import router_records as records
from router_transaction import TransactionError


def recovery_boot_chain(engine, *, root=Path('/'), media=Path('/media')):
    """Check current router recovery bytes and their unique saved LBU archive."""
    if not generations.matches('[0-9a-f]{40}', engine):
        raise TransactionError('router recovery chain requires an exact engine')
    root, media = Path(root), Path(media)
    prefix = 'usr/local/lib/klokast/router-updates/'
    selected = records.read(root / (prefix + 'current.json'))
    if selected != {'kind': 'klokast.router-installed-engine.v1', 'engine_commit': engine}:
        raise TransactionError('router recovery chain selects another installed engine')
    manifest = records.read(root / (prefix + engine + '/manifest.json'))
    if (not isinstance(manifest, dict) or set(manifest) != {'kind', 'engine_commit', 'files'} or
            manifest['kind'] != 'klokast.router-engine-files.v1' or manifest['engine_commit'] != engine or
            not isinstance(manifest['files'], dict) or not 1 <= len(manifest['files']) <= 64 or
            any(not generations.matches('[a-z][a-z0-9_]*[.]py', name) or
                not generations.matches('[0-9a-f]{64}', checksum) for name, checksum in manifest['files'].items())):
        raise TransactionError('router recovery chain has an invalid module manifest')
    paths = ['usr/local/sbin/router-update-transaction', 'etc/init.d/klokast-router-update-recovery',
        'etc/conf.d/xendomains', prefix + 'current.json', prefix + engine + '/manifest.json',
        *(prefix + engine + '/' + name for name in manifest['files'])]
    hashes = {}
    for name in paths:
        path = root / name
        records.parents(path)
        hashes[name] = hashlib.sha256(records.secure(path).read_bytes()).hexdigest()
    for name, checksum in manifest['files'].items():
        if hashes[prefix + engine + '/' + name] != checksum:
            raise TransactionError('router recovery chain has a changed installed module')
    dependency = b'rc_need="${rc_need:-} localmount klokast-router-update-recovery"'
    if dependency not in records.secure(root / 'etc/conf.d/xendomains').read_bytes():
        raise TransactionError('router recovery chain is not a Xen autostart prerequisite')
    runlevel = 'etc/runlevels/default/klokast-router-update-recovery'
    link = root / runlevel
    records.parents(link)
    info = link.lstat()
    if (not stat.S_ISLNK(info.st_mode) or info.st_uid != records.ROOT_UID or
            os.readlink(link) != '/etc/init.d/klokast-router-update-recovery'):
        raise TransactionError('router recovery chain has no exact enabled runlevel link')
    archives = [*media.glob('*.apkovl.tar.gz'), *media.glob('*/*.apkovl.tar.gz')]
    if len(archives) != 1:
        raise TransactionError('router recovery chain has no unique persisted archive')
    archive = archives[0]
    records.parents(archive)
    records.secure(archive, maximum=128 * 1024 * 1024)
    entries = set()
    total_size = 0
    with tarfile.open(archive, 'r:gz') as saved:
        for index, member in enumerate(saved):
            if index >= 10000:
                raise TransactionError('router persisted recovery archive has too many entries')
            total_size += member.size
            if total_size > 256 * 1024 * 1024:
                raise TransactionError('router persisted recovery archive is too large when expanded')
            member_path = PurePosixPath(member.name)
            if member_path.is_absolute() or '..' in member_path.parts:
                raise TransactionError('router persisted recovery archive contains an unsafe path')
            name = '/'.join(member_path.parts)
            if name == 'mnt/dom0_data' or name.startswith('mnt/dom0_data/'):
                raise TransactionError('router persisted recovery archive includes private live data')
            if name not in hashes and name != runlevel:
                continue
            if name in entries or member.uid != 0:
                raise TransactionError('router persisted recovery entry is duplicated or has a different owner')
            if name == runlevel:
                if not member.issym() or member.linkname != '/etc/init.d/klokast-router-update-recovery':
                    raise TransactionError('router persisted recovery runlevel differs')
            else:
                if not member.isfile() or not 0 < member.size <= 1024 * 1024 or member.mode & 0o022:
                    raise TransactionError('router persisted recovery entry has unsafe metadata')
                stream = saved.extractfile(member)
                if stream is None or hashlib.sha256(stream.read()).hexdigest() != hashes[name]:
                    raise TransactionError('router persisted recovery bytes differ from the installed chain')
            entries.add(name)
    if entries != set(hashes) | {runlevel}:
        raise TransactionError('router persisted recovery chain is incomplete')
    return {'kind': 'klokast.router-recovery-boot-chain.v1', 'engine_commit': engine,
        'files': hashes, 'runlevel': '/etc/init.d/klokast-router-update-recovery'}


def command(argv, deadline, *, maximum_seconds=30, lvm_diagnostic=False):
    remaining = min(maximum_seconds, deadline - time.monotonic())
    if remaining <= 0:
        raise TransactionError('router native command has no remaining action budget')
    try:
        result = subprocess.run([str(v) for v in argv], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=remaining)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TransactionError('router native command unavailable or timed out: ' + str(argv[0])) from error
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        # State-copy and guest diagnostics must never enter this output.
        if lvm_diagnostic and argv[0] == '/sbin/lvcreate':
            detail = result.stderr[:2048].strip()
            if detail and all(character.isprintable() or character in '\r\n\t' for character in detail):
                raise TransactionError('router candidate LV creation failed: ' + detail)
        raise TransactionError('router native command failed: ' + str(argv[0]))
    return result.stdout


def literal_configuration(content):
    value = {}
    try:
        for node in ast.parse(content).body:
            if (not isinstance(node, ast.Assign) or len(node.targets) != 1 or
                    not isinstance(node.targets[0], ast.Name) or node.targets[0].id in value):
                raise ValueError('nonliteral or repeated statement')
            value[node.targets[0].id] = ast.literal_eval(node.value)
    except (ValueError, TypeError, SyntaxError) as error:
        raise TransactionError('router Xen configuration is not a unique literal assignment set') from error
    return value


def validate_runtime(domain, generation, device):
    """Validate the complete live assignment, including aliases by device number."""
    expected = literal_configuration(generations.configuration(generation))
    return validate_runtime_expected(domain, expected, generation['disk']['path'], device)


def validate_runtime_expected(domain, expected, disk_path, device):
    """Check a live router against one recorded literal Xen definition."""
    try:
        config = domain['config']
        info, boot, disks, nics = config['c_info'], config['b_info'], config['disks'], config['nics']
        if (type(domain['domid']) is not int or domain['domid'] <= 0 or
                any(info[k] != expected[k] for k in ('name', 'uuid', 'type')) or
                boot['target_memkb'] != expected['memory'] * 1024 or boot['max_vcpus'] != expected['vcpus'] or
                any(boot[k] != expected[v] for k, v in (('kernel', 'kernel'), ('ramdisk', 'ramdisk'), ('cmdline', 'extra'))) or
                len(disks) != 1 or disks[0]['vdev'] != 'xvda' or disks[0]['readwrite'] != 1 or disks[0]['format'] != 'raw' or
                device(disks[0]['pdev_path']) != device(disk_path) or
                [n['devid'] for n in nics] != list(range(len(nics))) or
                ['bridge=' + n['bridge'] + ',mac=' + n['mac'] for n in nics] != expected['vif']):
            raise ValueError('live assignment differs')
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise TransactionError('live router UUID, disk, boot artifacts, or network differs from its generation') from error
    return domain


def backend_records(content):
    """Read native xenstore-ls output without treating partial backends as absent."""
    result = {}
    root = '/local/domain/0/backend/vbd'
    for line in content.splitlines():
        match = re.fullmatch(re.escape(root) + r'(?:/([0-9]+)(?:/([0-9]+)(?:/([A-Za-z0-9_-]+))?)?)? = (".*")', line)
        if not match:
            raise TransactionError('Xen block backend inventory is incomplete or has an unsupported format')
        domid, vdev, field, raw = match.groups()
        try:
            value = json.loads(raw)
        except ValueError as error:
            raise TransactionError('Xen block backend contains an invalid quoted value') from error
        if domid is not None and vdev is not None:
            key = (int(domid), int(vdev))
            target = result.setdefault(key, {})
            if field is not None:
                if field in target:
                    raise TransactionError('Xen block backend has duplicate fields')
                target[field] = value
    return result


class Native:
    monotonic = staticmethod(time.monotonic)

    def guard(self, box, *, deadline):
        if (os.geteuid() != 0 or os.uname().nodename.split('.')[0] != box + '-dom0' or
                Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
                Path('/sys/hypervisor/uuid').read_text().strip() != '00000000-0000-0000-0000-000000000000'):
            raise TransactionError('router executor requires root on the exact box dom0')
        command(['/bin/mountpoint', '-q', '/mnt/dom0_data'], deadline)
        mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()
                  if line.split()[4] == '/mnt/dom0_data']
        if len(mounts) != 1:
            raise TransactionError('router records require one persistent dom0 data mount')
        mount = mounts[0]
        separator = mount.index('-')
        if mount[separator + 1] != 'ext4' or 'rw' not in mount[5].split(','):
            raise TransactionError('router records require the writable persistent dom0 ext4 filesystem')

    def device(self, path):
        if not isinstance(path, str) or not path.startswith('/dev/') or '..' in Path(path).parts:
            raise TransactionError('Xen disk is not an explicit local block device')
        info = Path(path).stat()
        if not stat.S_ISBLK(info.st_mode):
            raise TransactionError('Xen disk path is not a block device')
        return info.st_rdev

    def bridges(self, xen):
        """Require every approved router bridge before connecting a first guest."""
        generations.xen_identity(xen)
        for item in xen['vif']:
            bridge = item.split(',mac=', 1)[0].removeprefix('bridge=')
            if not (Path('/sys/class/net') / bridge / 'bridge').is_dir():
                raise TransactionError('approved initial router bridge is not active: ' + bridge)

    def disk(self, expected, *, deadline):
        value = json.loads(command(['/sbin/lvs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
            '-o', 'lv_path,lv_uuid,lv_size,lv_attr,origin', expected['path']], deadline), object_pairs_hook=records.unique)
        rows = value['report'][0]['lv']
        if len(rows) != 1:
            raise TransactionError('router logical volume identity is ambiguous')
        row = {k: v.strip() for k, v in rows[0].items()}
        if (row['lv_path'] != expected['path'] or row['lv_uuid'] != expected['uuid'] or
                int(row['lv_size']) != expected['bytes'] or row['origin'] or
                not row['lv_attr'].startswith('-wi-a')):
            raise TransactionError('router logical volume identity, size, or independent writable allocation changed')
        device = self.device(expected['path'])
        node = Path('/sys/dev/block') / (str(os.major(device)) + ':' + str(os.minor(device)))
        # Guest disks must not have dom0 partition maps, mounts or other holders.
        if list((node / 'holders').iterdir()) or list(node.glob('*/partition')):
            raise TransactionError('router logical volume has a dom0 holder or partition mapping')
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            major, minor = map(int, line.split()[2].split(':'))
            if os.makedev(major, minor) == device:
                raise TransactionError('router disk is mounted on dom0')
        return device

    def artifact(self, item, *, deadline):
        path = Path(item['path'])
        records.parents(path)
        records.secure(path, maximum=1024 * 1024 * 1024)
        if path.stat().st_size != item['bytes']:
            raise TransactionError('router boot artifact size changed')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                if self.monotonic() >= deadline:
                    raise TransactionError('router artifact verification deadline expired')
                digest.update(block)
        if digest.hexdigest() != item['sha256']:
            raise TransactionError('router boot artifact checksum changed')

    def inventory(self, *, deadline):
        values = json.loads(command(['/usr/sbin/xl', 'list', '-l'], deadline), object_pairs_hook=records.unique)
        if not isinstance(values, list) or not values:
            raise TransactionError('Xen returned an empty or invalid inventory')
        names, ids, uuids = set(), set(), set()
        for value in values:
            info = value.get('config', {}).get('c_info', {})
            name, identity, domid = info.get('name'), info.get('uuid'), value.get('domid')
            # Xen omits c_info.uuid for Domain-0 on supported Alpine hosts.
            valid_identity = (identity in (None, '00000000-0000-0000-0000-000000000000') if domid == 0 else
                generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', identity))
            if (not isinstance(name, str) or type(domid) is not int or domid < 0 or
                    not valid_identity or name in names or domid in ids or identity in uuids or
                    (domid == 0) != (name == 'Domain-0')):
                raise TransactionError('Xen returned incomplete or ambiguous domain identities')
            names.add(name); ids.add(domid); uuids.add(identity)
        if 0 not in ids:
            raise TransactionError('Xen inventory does not include dom0')
        return values

    def guest(self, pair, *, deadline):
        devices = {side: self.disk(value['disk'], deadline=deadline) for side, value in pair.items()}
        if len(set(devices.values())) != len(devices):
            raise TransactionError('router generation block devices overlap')
        found = None
        for value in self.inventory(deadline=deadline):
            if value['domid'] == 0:
                continue
            config = value['config']
            if not isinstance(config.get('disks'), list):
                raise TransactionError('Xen omitted live disk attachments')
            attached = {self.device(d['pdev_path']) for d in config['disks']}
            identity = config['c_info']['uuid']
            matching = [side for side, g in pair.items() if g['xen']['uuid'] == identity]
            if config['c_info']['name'] == 'router' or matching:
                if found is not None or len(matching) != 1:
                    raise TransactionError('router live identity differs from its recorded generation pair')
                side = matching[0]
                validate_runtime(value, pair[side], self.device)
                found = (side, value)
            elif attached & set(devices.values()):
                raise TransactionError('another Xen guest holds a router generation disk')
        return found

    def initial_guest(self, disk, expected, *, deadline):
        """Find only the recorded first router and fence reused MACs or disks."""
        target = self.disk(disk, deadline=deadline)
        found = None
        macs = {v.split(',mac=', 1)[1] for v in expected['vif']}
        for value in self.inventory(deadline=deadline):
            if value['domid'] == 0:
                continue
            config = value['config']
            try:
                identity = config['c_info']['uuid']
                name = config['c_info']['name']
                attached = {self.device(item['pdev_path']) for item in config['disks']}
                live_macs = {item['mac'] for item in config['nics']}
            except (KeyError, TypeError, ValueError) as error:
                raise TransactionError('Xen guest inventory lacks exact disk or network identities') from error
            if name == 'router' or identity == expected['uuid'] or target in attached:
                if found is not None or name != 'router' or identity != expected['uuid']:
                    raise TransactionError('another guest claims the initial router identity or disk')
                validate_runtime_expected(value, expected, disk['path'], self.device)
                found = value
            elif macs & live_macs:
                raise TransactionError('another guest claims an initial router MAC address')
        return found

    def stop_initial(self, disk, expected, *, deadline):
        """Stop only a recorded first router, then prove its disk detached."""
        current = self.initial_guest(disk, expected, deadline=deadline)
        if current is not None:
            command(['/usr/sbin/xl', 'shutdown', str(current['domid'])], deadline)
            stop_at = min(deadline - 10, self.monotonic() + 20)
            while self.monotonic() < stop_at:
                current = self.initial_guest(disk, expected, deadline=deadline)
                if current is None:
                    break
                time.sleep(min(0.5, max(0, stop_at - self.monotonic())))
            current = self.initial_guest(disk, expected, deadline=deadline)
            if current is not None:
                command(['/usr/sbin/xl', 'destroy', str(current['domid'])], deadline)
        self.wait_detached([disk['path']], deadline=deadline)

    def detached(self, paths, *, deadline):
        devices = {self.device(str(path)) for path in paths}
        for value in self.inventory(deadline=deadline):
            if value['domid'] and devices & {self.device(d['pdev_path']) for d in value['config']['disks']}:
                raise TransactionError('router disk still has a live Xen attachment')
        backends = backend_records(command(['/usr/bin/xenstore-ls', '-f', '/local/domain/0/backend/vbd'], deadline))
        kernel = set()
        for path in Path('/sys/bus/xen-backend/devices').glob('vbd-*'):
            match = re.fullmatch(r'vbd-([0-9]+)-([0-9]+)', path.name)
            if not match:
                raise TransactionError('kernel Xen block backend identity is unsupported')
            kernel.add(tuple(map(int, match.groups())))
        if not kernel <= backends.keys():
            raise TransactionError('kernel Xen block backend has no complete xenstore record')
        for value in backends.values():
            if 'params' not in value:
                raise TransactionError('Xen block backend is still being attached or detached')
            if self.device(value['params']) in devices:
                raise TransactionError('router disk still has a Xen block backend')
            physical = value.get('physical-device')
            if physical is not None:
                if not re.fullmatch('[0-9a-f]+:[0-9a-f]+', physical):
                    raise TransactionError('Xen physical block identity is invalid')
                major, minor = [int(v, 16) for v in physical.split(':')]
                if os.makedev(major, minor) in devices:
                    raise TransactionError('router disk still has an aliased Xen block backend')

    def wait_detached(self, paths, *, deadline):
        while self.monotonic() < deadline:
            try:
                self.detached(paths, deadline=deadline)
                return
            except TransactionError:
                time.sleep(min(0.25, max(0, deadline - self.monotonic())))
        raise TransactionError('router disk did not detach within its fixed budget')

    def stop(self, pair, side, *, deadline, graceful=True):
        current = self.guest(pair, deadline=deadline)
        if current is not None and current[0] == side:
            if graceful:
                command(['/usr/sbin/xl', 'shutdown', str(current[1]['domid'])], deadline)
                stop_at = min(deadline - 10, self.monotonic() + 20)
                while self.monotonic() < stop_at:
                    current = self.guest(pair, deadline=deadline)
                    if current is None or current[0] != side:
                        break
                    time.sleep(min(0.5, max(0, stop_at - self.monotonic())))
            current = self.guest(pair, deadline=deadline)
            if current is not None and current[0] == side:
                command(['/usr/sbin/xl', 'destroy', str(current[1]['domid'])], deadline)
        self.wait_detached([pair[side]['disk']['path']], deadline=deadline)

    def start(self, pair, side, config, *, deadline):
        current = self.guest(pair, deadline=deadline)
        if current is not None:
            if current[0] == side:
                return
            raise TransactionError('cannot start a second router identity')
        self.detached([g['disk']['path'] for g in pair.values()], deadline=deadline)
        for artifact in pair[side]['boot'].values():
            self.artifact(artifact, deadline=deadline)
        expected = generations.configuration(pair[side])
        if records.secure(config).read_text() != expected:
            raise TransactionError('staged router boot configuration differs from its generation')
        command(['/usr/sbin/xl', 'create', config], deadline)
        current = self.guest(pair, deadline=deadline)
        if current is None or current[0] != side:
            raise TransactionError('router did not start with its exact recorded assignment')
