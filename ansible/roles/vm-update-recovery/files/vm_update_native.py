"""Protected evidence, Xen configuration and native storage operations."""
import ast
import hashlib
import json
import os
from pathlib import (Path)
import re
import secrets
import stat
import subprocess
import time
BASE = Path('/mnt/dom0_data/klokast-vm-updates')
XEN = Path('/etc/xen')
OP = re.compile(r'[0-9a-f]{24}')
SERVICE_KIND = 'klokast.vm-switch.v4'
ROLES = {'bak', 'dmz', 'iot', 'vpn-egress'}
POST_BOOT = {'starting', 'booted', 'tested', 'accepted', 'complete'}
HEX = re.compile(r'[0-9a-f]{64}')
LV = re.compile(r'/dev/[A-Za-z0-9_+.-]+/[A-Za-z0-9_+.-]+')
PRE_ACCEPT = {'armed', 'stopping', 'stopped', 'starting', 'booted', 'tested', 'recovering'}
TERMINAL = {'adopted', 'accepted', 'complete', 'recovered', 'recovery-failed'}
HELPER = '/usr/local/sbin/vm-update-transaction'


class Refused(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate JSON field')
        result[key] = value
    return result


def secure(path, directory=False, limit=1024 * 1024):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or
            (not directory and (info.st_nlink != 1 or info.st_size > limit))):
        raise Refused('transaction path has unsafe type, owner, mode, or size: ' + path.name)
    return path


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data):
    secure(path.parent, True)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        syncdir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def store(path, value):
    atomic(path, (canonical(value) + '\n').encode())


def read(path):
    return json.loads(secure(path).read_text(), object_pairs_hook=unique)


def command(argv, timeout=60):
    try:
        result = subprocess.run([str(v) for v in argv], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused('transaction command unavailable or timed out: ' + str(argv[0])) from error
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        # These fixed native commands receive only local paths and domain
        # identities, never credentials. Keep bounded actionable diagnostics.
        raise Refused('transaction command failed: ' + str(argv[0]) +
                      ' (exit ' + str(result.returncode) + '): ' + result.stderr[-2048:].strip())
    return result.stdout


def configuration(content, role, identity):
    """Parse the supported xl literal subset, without executing Python."""
    allowed = {'name', 'uuid', 'type', 'memory', 'maxmem', 'vcpus', 'cpus', 'kernel',
               'ramdisk', 'extra', 'disk', 'vif', 'on_crash', 'on_reboot', 'on_poweroff'}
    result = {}
    try:
        for node in ast.parse(content).body:
            if (not isinstance(node, ast.Assign) or len(node.targets) != 1 or
                    not isinstance(node.targets[0], ast.Name)):
                raise Refused('Xen configuration must contain literal assignments only')
            name = node.targets[0].id
            if name not in allowed or name in result:
                raise Refused('Xen configuration contains an unknown or repeated setting')
            result[name] = ast.literal_eval(node.value)
    except (SyntaxError, ValueError, TypeError) as error:
        raise Refused('invalid Xen literal configuration') from error
    if result.get('name') != role or result.get('type') != 'pvh':
        raise Refused('configuration does not describe the selected shared PVH guest')
    if result.get('uuid', identity) != identity:
        raise Refused('Xen configuration UUID differs from the recorded domain')
    disks = result.get('disk')
    if not isinstance(disks, list) or not 1 <= len(disks) <= 8:
        raise Refused('guest requires a bounded explicit disk list')
    paths, devices = [], []
    for disk in disks:
        if not isinstance(disk, str):
            raise Refused('invalid Xen disk entry')
        match = re.fullmatch(r'phy:([^,]+),(xvd[a-z]),w', disk)
        if not match or not LV.fullmatch(match[1]):
            raise Refused('only explicit writable LVM guest disks are supported')
        if any(part in {'.', '..'} for part in Path(match[1]).parts):
            raise Refused('disk path contains an alias')
        paths.append(match[1]); devices.append(match[2])
    if len(set(paths)) != len(paths) or len(set(devices)) != len(devices):
        raise Refused('guest disk mappings overlap')
    for name in ('kernel', 'ramdisk'):
        value = result.get(name)
        if (not isinstance(value, str) or not value.startswith('/mnt/dom0_data/') or
                any(part in {'.', '..'} for part in Path(value).parts)):
            raise Refused('boot artifact must use persistent dom0 storage')
    return result, paths


class Native:
    def device(self, path):
        return Path(path).stat().st_rdev

    def watch(self, operation, work):
        helper = secure(Path(HELPER))
        descriptor = os.open(work / 'recovery.log', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'ab') as log:
            child = subprocess.Popen(['/usr/bin/python3', str(helper), 'watch', '--operation-id', operation],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
        self._watch_process = child
        # Bind the watchdog to this process incarnation and this boot. A
        # reused PID or a watchdog for another operation cannot satisfy arm.
        identity = {'pid': child.pid, 'start_ticks': self.process_start(child.pid),
                    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        for _ in range(100):
            if self.watcher_alive(identity, operation):
                return identity
            time.sleep(0.02)
        raise Refused('local recovery process did not start')

    def process_start(self, pid):
        return Path('/proc/' + str(pid) + '/stat').read_text().rsplit(')', 1)[1].split()[19]

    def watcher_alive(self, identity, operation):
        try:
            pid = identity['pid']
            args = Path('/proc/' + str(pid) + '/cmdline').read_bytes().split(b'\0')
            return (args == [b'/usr/bin/python3', HELPER.encode(), b'watch', b'--operation-id', operation.encode(), b''] and
                    self.process_start(pid) == identity['start_ticks'] and
                    Path('/proc/sys/kernel/random/boot_id').read_text().strip() == identity['boot_id'])
        except (OSError, KeyError, TypeError, IndexError):
            return False

    def artifact(self, path):
        path = Path(path)
        for parent in path.parents:
            secure(parent, True)
        secure(path, limit=512 * 1024 * 1024)
        result, size = hashlib.sha256(), 0
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                result.update(block); size += len(block)
        return {'sha256': result.hexdigest(), 'bytes': size}

    def inventory(self):
        records = json.loads(command(['xl', 'list', '-l']), object_pairs_hook=unique)
        if not isinstance(records, list) or not records:
            raise Refused('Xen returned an incomplete inventory')
        names, ids = set(), set()
        for item in records:
            info = item.get('config', {}).get('c_info', {})
            name, domid = info.get('name'), item.get('domid')
            if (not isinstance(name, str) or type(domid) is not int or domid < 0 or
                    name in names or domid in ids or (domid == 0 and name != 'Domain-0')):
                raise Refused('Xen returned an invalid domain identity')
            if domid and not isinstance(info.get('uuid'), str):
                raise Refused('Xen returned a guest without a UUID')
            names.add(name); ids.add(domid)
        if 0 not in ids:
            raise Refused('Xen inventory does not include dom0')
        return records

    def lv(self, path):
        value = json.loads(command(['lvs', '--reportformat', 'json', '--units', 'b', '--nosuffix',
                                    '-o', 'lv_path,lv_uuid,lv_size,lv_attr,origin', path]))
        rows = value['report'][0]['lv']
        if len(rows) != 1 or rows[0]['lv_path'].strip() != path:
            raise Refused('logical volume identity is ambiguous')
        row = {k: v.strip() for k, v in rows[0].items()}
        # Full independent LVs make rollback a disk selection, not a merge that
        # can destroy data or require online snapshot recovery after power loss.
        if row['origin'] or row['lv_attr'][0] != '-':
            raise Refused('replacement requires independent ordinary logical volumes')
        info = Path(path).stat()
        if not stat.S_ISBLK(info.st_mode):
            raise Refused('logical volume is not a block device')
        return {'uuid': row['lv_uuid'], 'bytes': int(float(row['lv_size'])), 'device': info.st_rdev}

    def check_unmounted(self, disks):
        devices = {v['device'] for v in disks.values()}
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            major, minor = map(int, line.split()[2].split(':'))
            if os.makedev(major, minor) in devices:
                raise Refused('transaction disk is mounted on dom0')

    def stop(self, record, graceful):
        current = [item for item in self.inventory() if item['domid'] == record['domid']]
        if not current:
            return
        if (current[0]['config']['c_info']['uuid'] != record['config']['c_info']['uuid'] or
                current[0]['config']['c_info']['name'] != record['config']['c_info']['name']):
            raise Refused('domain identity changed immediately before shutdown')
        domid = str(record['domid'])
        if graceful:
            command(['xl', 'shutdown', domid])
            # Do not force-stop old writers. A failed graceful stop aborts the
            # replacement while the original disk remains authoritative.
            deadline = time.monotonic() + 120
            identity = record['config']['c_info']['uuid']
            while time.monotonic() < deadline:
                matches = [r for r in self.inventory() if r['config']['c_info'].get('uuid') == identity]
                if not matches:
                    return
                time.sleep(1)
            raise Refused('old guest did not stop cleanly; replacement refused')
        command(['xl', 'destroy', domid])

    def start(self, path):
        command(['xl', 'create', path])

    def persist(self):
        command(['lbu', 'commit', '-d'], timeout=120)


