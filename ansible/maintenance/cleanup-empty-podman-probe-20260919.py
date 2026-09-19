#!/usr/bin/env python3
"""Undo only the empty rootful stores created by the 2026-09-19 inspection.

This correction is restricted to the two guests whose prior scan proved the
store absent. It does not remove the pre-existing k001-dmz store, rootless
stores, or unknown files. The sibling collector is installed by the playbook.
"""
import argparse
from contextlib import ExitStack
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat


def collector():
    loader = importlib.machinery.SourceFileLoader('inspection_collector', str(Path(__file__).with_name('collect-vm-update-facts')))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def remove_entries(root, entries):
    """Keep parent descriptors open; never follow a substituted directory."""
    with ExitStack() as stack:
        current = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, current)
        for part in str(root).strip('/').split('/')[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            stack.callback(os.close, current)
        parents = {str(root.parent): current}
        for row in sorted(entries, key=lambda v: (v['path'].count('/'), v['path'])):
            path = Path(row['path'])
            parent = parents[str(path.parent)]
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_mtime_ns, info.st_size) != (
                    row['inode'], row['uid'], row['gid'], row['mode'], row['mtime_ns'], row['bytes']):
                raise ValueError('empty probe store changed before correction')
            if stat.S_ISDIR(info.st_mode):
                descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, descriptor)
                if os.fstat(descriptor).st_ino != info.st_ino:
                    raise ValueError('probe store directory changed while opening')
                parents[str(path)] = descriptor
        for row in sorted(entries, key=lambda v: (v['path'].count('/'), v['path']), reverse=True):
            path = Path(row['path']); parent = parents[str(path.parent)]
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_ino, info.st_mode, info.st_uid, info.st_gid) != (row['inode'], row['mode'], row['uid'], row['gid']):
                raise ValueError('probe store entry changed during correction')
            if stat.S_ISDIR(info.st_mode):
                os.rmdir(path.name, dir_fd=parent)
            elif stat.S_ISREG(info.st_mode):
                os.unlink(path.name, dir_fd=parent)
            else:
                raise ValueError('probe store contains a non-regular file')
        os.fsync(current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-plan-sha256')
    args = parser.parse_args()
    host = socket.gethostname().split('.')[0]
    if os.geteuid() != 0 or host not in ('k002-dmz', 'k002-iot'):
        raise ValueError('correction is restricted to the two originally absent rootful stores')
    module = collector(); root = Path('/var/lib/containers/storage')
    value = module.collect_no_application_store(Path('/'), module.mount_inventory(), {'uid': 0, 'gid': 0}, str(root))
    if value['complete'] is not True or value['stable'] is not True or value['empty'] is not True:
        raise ValueError('probe store is not the exact supported empty layout; preserve it')
    # Fixed time bounds cover this task's accidental Podman initialization.
    # Older and later directories are outside this correction's scope.
    entries = value['metadata']['entries']
    if not 1 <= len(entries) <= 32 or any(not 1789778800000000000 <= row['mtime_ns'] <= 1789779400000000000 for row in entries):
        raise ValueError('probe store metadata is outside the recorded inspection interval')
    plan = {'kind': 'klokast.empty-podman-probe-correction.v1', 'host': host,
            'store': str(root), 'evidence': value, 'removed': False}
    plan['plan_sha256'] = checksum(plan)
    if args.apply_plan_sha256:
        if args.apply_plan_sha256 != plan['plan_sha256']:
            raise ValueError('probe store changed after the exact correction plan')
        remove_entries(root, entries)
        if root.exists() or root.is_symlink():
            raise ValueError('probe store is still present after correction')
        plan['removed'] = True
    print(json.dumps(plan, sort_keys=True))


if __name__ == '__main__':
    main()
