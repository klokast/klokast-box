#!/usr/bin/env python3
"""Offline FAT boot repair, without a reboot or guest shutdown."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import tempfile


def run(argv, codes=(0,)):
    result = subprocess.run(argv, text=True, capture_output=True, timeout=300)
    print(json.dumps({'command': argv, 'returncode': result.returncode,
                      'stdout': result.stdout, 'stderr': result.stderr}), flush=True)
    if result.returncode not in codes:
        raise RuntimeError('boot filesystem maintenance failed: ' + argv[0])
    return result.stdout.strip()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def hashes(mount):
    return {str(p.relative_to(mount)): digest(p)
            for p in mount.rglob('*') if p.is_file()}


def release_boot_modloop(target):
    rows = [r.split() for r in Path('/proc/mounts').read_text().splitlines()
            if r.split()[1] == '/.modloop']
    if len(rows) != 1 or rows[0][2] != 'squashfs' or 'ro' not in rows[0][3].split(','):
        raise RuntimeError('expected one read-only module image')
    loop = rows[0][0]
    if not loop.startswith('/dev/loop') or not loop[9:].isdigit():
        raise RuntimeError('module image has an unexpected device')
    source = Path((Path('/sys/block') / Path(loop).name / 'loop/backing_file').read_text().strip())
    if not source.is_relative_to(target):
        return  # An earlier maintenance run already released the boot media.
    if source.is_symlink() or not source.is_file() or source.name != 'modloop-lts':
        raise RuntimeError('unexpected boot module image source')
    directory = Path(tempfile.mkdtemp(prefix='fat-modloop-', dir='/mnt/dom0_data/klokast-dom0-updates'))
    copy = directory / 'modloop-lts'
    if shutil.disk_usage(directory).free < source.stat().st_size + 64 * 1024**2:
        raise RuntimeError('insufficient data space for the module image copy')
    shutil.copyfile(source, copy)
    if digest(source) != digest(copy):
        raise RuntimeError('module image copy checksum differs')
    run(['sync'])
    run(['umount', '/.modloop'])
    try:
        # Alpine's module loop normally autoclears on unmount.
        backing = Path('/sys/block') / Path(loop).name / 'loop/backing_file'
        if backing.exists():
            if backing.read_text().strip() != str(source):
                raise RuntimeError('module loop was reused; detach refused')
            run(['losetup', '-d', loop])
        run(['mount', '-t', 'squashfs', '-o', 'loop,ro', str(copy), '/.modloop'])
    except Exception:
        run(['mount', '-t', 'squashfs', '-o', 'loop,ro', str(source), '/.modloop'])
        raise
    print('Module image retained until reboot: ' + str(copy), flush=True)


def maintain():
    rows = [r.split() for r in Path('/proc/mounts').read_text().splitlines()
            if r.split()[1] == '/media/nvme0n1p1']
    if len(rows) != 1 or rows[0][0] != '/dev/nvme0n1p1' or rows[0][2] != 'vfat':
        raise RuntimeError('expected one managed FAT boot partition')
    device, target = rows[0][:2]
    mount = Path(target)
    pending = Path('/mnt/dom0_data/klokast-dom0-updates/pending.json')
    if pending.exists():
        value = json.loads(pending.read_text())
        if not value.get('reboot_test') or value['state'] != 'selected':
            raise RuntimeError('only a selected, unstarted reboot test permits boot maintenance')
    before = hashes(mount)
    installed = Path('/lib/apk/db/installed').read_bytes()
    world = Path('/etc/apk/world').read_bytes()
    tool = shutil.which('fsck.fat') or shutil.which('fsck.vfat')
    temporary = tempfile.TemporaryDirectory(prefix="klokast-boot-fsck-") if not tool else None
    try:
        if temporary:
            run(['apk', '--root', temporary.name, '--initdb', '--keys-dir', '/etc/apk/keys',
                 '--repositories-file', '/etc/apk/repositories', 'add', '--no-cache',
                 '--no-scripts', 'dosfstools'])
            candidates = [Path(temporary.name) / name for name in ('sbin/fsck.fat', 'usr/sbin/fsck.fat')]
            tool = str(next(p for p in candidates if p.is_file()))
        release_boot_modloop(target)
        run(['sync'])
        run(['umount', target])  # Never use forced or lazy unmount.
        try:
            if any(r.split()[0] == device for r in Path('/proc/mounts').read_text().splitlines()):
                raise RuntimeError('boot device remains mounted; repair refused')
            size = int(Path('/sys/class/block/nvme0n1p1/size').read_text()) * 512
            evidence = Path('/mnt/dom0_data/klokast-dom0-updates') / ('fat-check-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
            evidence.mkdir(mode=0o700)
            if shutil.disk_usage(evidence).free < size + 64 * 1024**2:
                raise RuntimeError('insufficient data space for a complete boot partition backup')
            backup = evidence / 'before.img'
            with open(device, 'rb') as source, backup.open('xb') as destination:
                shutil.copyfileobj(source, destination)
                destination.flush(); os.fsync(destination.fileno())
            if backup.stat().st_size != size:
                raise RuntimeError('incomplete boot partition backup')
            (evidence / 'before.sha256').write_text(digest(backup) + '\n')
            run([tool, '-a', device], codes=(0, 1))
            run([tool, '-n', device])
        finally:
            run(['mount', '-t', 'vfat', '-o', 'ro', device, target])
        if hashes(mount) != before:
            raise RuntimeError('boot file contents changed during repair; preserve backup and inspect before reboot')
        print('Boot FAT verified; boot file contents unchanged; media mounted read-only.', flush=True)
    finally:
        if temporary:
            temporary.cleanup()
        if Path('/etc/apk/world').read_bytes() != world or Path('/lib/apk/db/installed').read_bytes() != installed:
            raise RuntimeError('temporary tool cleanup changed the runtime package set; inspect before reboot')


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != sys.argv[1] + '-dom0':
        raise RuntimeError('run boot maintenance on the selected dom0 as root')
    os.umask(0o077)
    with contextlib.ExitStack() as stack:
        for name in ('klokast-vm-updates/transaction.lock', 'klokast-router-updates/transaction.lock', 'klokast-vm-templates/build.lock'):
            lock = stack.enter_context(open(Path('/mnt/dom0_data') / name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        maintain()


if __name__ == '__main__':
    main()
