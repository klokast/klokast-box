#!/usr/bin/env python3
"""Retire only the fixed shared usr guest; invoked by controller Ansible."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import time


XEN = Path('/etc/xen')
IMAGES = Path('/mnt/dom0_data/xen_images')
FIXED_DISKS = {'lv_usr', 'lv_podman_usr'}


def configuration(text):
    result = {}
    for node in ast.parse(text).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise ValueError('Xen ownership requires literal assignments; executable configuration is refused.')
        target = node.targets[0]
        value = ast.literal_eval(node.value)
        if target.id in ('name', 'disk', 'kernel', 'ramdisk'):
            if target.id in result:
                raise ValueError(f'Duplicate Xen field: {target.id}')
            result[target.id] = value
    return result


def disks(config):
    values = config.get('disk', [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError('Xen disks must be a literal string list.')
    return [value.split(',', 1)[0].removeprefix('phy:') for value in values]


def retirement_plan(box, configs, volumes, domains, existing_files):
    """Pure ownership checks; neither reads nor mutates the host."""
    names = {'usr', f'{box}-usr'}
    target = configs.get(str(XEN / 'usr.cfg'))
    if target is not None and target.get('name') not in names:
        raise ValueError('usr.cfg does not define the fixed usr guest.')
    live = sorted(set(domains) & names)
    if live and target is None:
        raise ValueError('The fixed usr guest is running without usr.cfg; disk ownership is unknown.')
    paths = {volume['lv_path'].strip(): volume for volume in volumes}
    selected = {path for path, volume in paths.items() if volume['lv_name'].strip() in FIXED_DISKS}
    for path in disks(target or {}):
        volume = paths.get(path)
        if volume is None or not re.fullmatch(r'lv_(?:podman_)?usr(?:_[a-z0-9_]+)?', volume['lv_name'].strip()):
            raise ValueError(f'Fixed usr disk ownership is ambiguous: {path}')
        selected.add(path)
    selected_names = {paths[path]['lv_name'].strip() for path in selected}
    for path, volume in paths.items():
        if volume.get('origin', '').strip() in selected_names:
            raise ValueError(f'Fixed usr disk has an LVM snapshot: {path}; resolve its ownership first.')
    for path, config in configs.items():
        if path == str(XEN / 'usr.cfg'):
            continue
        for disk in disks(config):
            # Check mapper aliases as well as /dev/VG/LV spelling.
            if disk in selected or any(name in disk for name in selected_names):
                raise ValueError(f'Fixed usr disk is referenced by another configuration: {path}')
    files = {str(XEN / 'usr.cfg'), str(XEN / 'auto/usr.cfg'), str(XEN / 'auto.disabled/usr.cfg'),
             str(IMAGES / 'usr-kernel'), str(IMAGES / 'usr-initramfs')}
    for field in ('kernel', 'ramdisk'):
        if target and target.get(field) and target[field] not in files:
            raise ValueError(f'Fixed usr {field} ownership is ambiguous: {target[field]}')
    for path, config in configs.items():
        if path != str(XEN / 'usr.cfg') and any(config.get(field) in files for field in ('kernel', 'ramdisk')):
            raise ValueError(f'Fixed usr boot file is referenced by another configuration: {path}')
    return {'domains': live, 'disks': sorted(selected), 'files': sorted(files & set(existing_files))}


def command(argv):
    return subprocess.check_output(argv, text=True, timeout=30)


def inspect(box):
    configs = {}
    for path in sorted(XEN.rglob('*.cfg')):
        if path.is_symlink():
            if path.resolve().parent != XEN:
                raise ValueError(f'Xen configuration link escapes /etc/xen: {path}')
            continue
        configs[str(path)] = configuration(path.read_text())
    data = json.loads(command(['lvs', '--reportformat', 'json', '-o', 'lv_path,lv_name,origin']))
    volumes = [volume for report in data['report'] for volume in report.get('lv', [])]
    domains = [line.split()[0] for line in command(['xl', 'list']).splitlines()[1:] if line.strip()]
    existing = []
    for path in (XEN / 'usr.cfg', XEN / 'auto/usr.cfg', XEN / 'auto.disabled/usr.cfg',
                 IMAGES / 'usr-kernel', IMAGES / 'usr-initramfs'):
        if path.is_symlink():
            if path.parent not in (XEN / 'auto', XEN / 'auto.disabled') or path.resolve() != XEN / 'usr.cfg':
                raise ValueError(f'Unexpected fixed usr file link: {path}')
        elif path.exists() and not path.is_file():
            raise ValueError(f'Fixed usr path is not a regular file: {path}')
        if path.exists() or path.is_symlink():
            existing.append(str(path))
    plan = retirement_plan(box, configs, volumes, domains, existing)
    mounted = {line.split()[2] for line in Path('/proc/self/mountinfo').read_text().splitlines()}
    for path in plan['disks']:
        info = os.stat(path)
        if not stat.S_ISBLK(info.st_mode):
            raise ValueError(f'Fixed usr volume is not a block device: {path}')
        if f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}' in mounted:
            raise ValueError(f'Fixed usr volume is mounted on dom0: {path}')
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', args.box):
        parser.error('box must be a lowercase DNS label')
    plan = inspect(args.box)
    changed = any(plan.values())
    if args.apply and changed:
        # Disable restart before shutdown. Leave disks intact if shutdown fails.
        for path in plan['files']:
            if Path(path).parent in (XEN / 'auto', XEN / 'auto.disabled'):
                Path(path).unlink()
        if any(Path(path).parent == XEN / 'auto' for path in plan['files']):
            command(['lbu', 'commit', '-d'])
        for domain in plan['domains']:
            command(['xl', 'shutdown', domain])
        deadline = time.monotonic() + 60
        while inspect(args.box)['domains']:
            if time.monotonic() >= deadline:
                raise ValueError('Fixed usr shutdown timed out. Its disks are preserved; do not retry until shutdown is confirmed.')
            time.sleep(2)
        current = inspect(args.box)
        if current['disks'] != plan['disks']:
            raise ValueError('Fixed usr disks changed during shutdown; no disk was removed.')
        for path in current['disks']:
            command(['lvremove', '--yes', path])
        for path in current['files']:
            Path(path).unlink()
    print(json.dumps({'apply': args.apply, 'changed': changed if args.apply else False, 'plan': plan}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, SyntaxError, subprocess.SubprocessError) as error:
        raise SystemExit(f'Fixed usr retirement refused: {error}')
