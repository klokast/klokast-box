#!/usr/bin/env python3
"""Diskless host transaction. Run through the active controller, never on AIR."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import time

BASE = Path('/mnt/dom0_data/klokast-dom0-updates')
POLICY = Path('/etc/klokast/dom0-update.json')
ID = re.compile('[0-9a-f]{24}')
TAILSCALE_FILES = {'usr/bin/tailscale': 0o755, 'usr/sbin/tailscaled': 0o755,
                  'etc/init.d/tailscale': 0o755,
                  'usr/lib/modules-load.d/tailscale.conf': 0o644}
# The overlay does not carry the binaries: lbu status unpacks the overlay
# three times into the small RAM-backed /tmp. A boot one-shot copies them
# from the generation that the protected image policy names.
TAILSCALE_BINARIES = ('usr/bin/tailscale', 'usr/sbin/tailscaled')
TAILSCALE_SERVICE = 'etc/init.d/klokast-dom0-tailscale'
TAILSCALE_RUNLEVEL = Path('/etc/runlevels/default/klokast-dom0-tailscale')
TAILSCALE_SERVICE_TEXT = r'''#!/sbin/openrc-run
# Managed by the dom0 update helper. Copy the upstream Tailscale binaries
# from the accepted generation before Tailscale starts.
description="Install upstream Tailscale from the accepted dom0 generation"
depend() {
    before tailscale
}
start() {
    ebegin "Installing upstream Tailscale from the accepted dom0 generation"
    /usr/bin/python3 -I - <<'BOOT'
import hashlib, json, os, re
from pathlib import Path
policy = json.loads(Path('/etc/klokast/dom0-update.json').read_text())
generation = Path(policy['generation'])
if generation.parent.name != 'klokast-dom0' or not re.fullmatch('[0-9a-f]{24}', generation.name):
    raise SystemExit('invalid generation in the dom0 image policy')
if set(policy['tailscale']) != {'usr/bin/tailscale', 'usr/sbin/tailscaled'}:
    raise SystemExit('invalid Tailscale files in the dom0 image policy')
for relative, expected in sorted(policy['tailscale'].items()):
    data = (generation / 'tailscale' / relative).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit('Tailscale file checksum differs in the generation: ' + relative)
    target = Path('/') / relative
    temporary = target.with_name(target.name + '.dom0-boot')
    temporary.write_bytes(data)
    temporary.chmod(0o755)
    os.replace(temporary, target)
BOOT
    eend $?
}
'''


def run(args, timeout=120):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('dom0 update command failed: ' + str(args[0]) + ': ' + result.stderr[-500:])
    return result.stdout.strip()


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024**2), b''):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    os.sync()


def read(path):
    return json.loads(path.read_text())


def media(writable=False):
    matches = [line.split()[1] for line in Path('/proc/mounts').read_text().splitlines()
               if line.split()[0] == '/dev/nvme0n1p1' and line.split()[2] == 'vfat']
    if len(matches) != 1:
        raise RuntimeError('expected one mounted managed EFI partition')
    if writable:
        run(['mount', '-o', 'remount,rw', '/dev/nvme0n1p1', matches[0]])
    return Path(matches[0])


def identity():
    status = json.loads(run(['tailscale', 'status', '--json']))
    if status.get('BackendState') != 'Running' or not status.get('Self', {}).get('ID'):
        raise RuntimeError('dom0 Tailscale identity is unavailable')
    return status['Self']['ID']


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def domains():
    entries = json.loads(run(['xl', 'list', '-l']))
    return {v['config']['c_info']['name']: v['config']['c_info']['uuid'] for v in entries if v['domid'] != 0}


def clean():
    # lbu status exits 0 and lists every file as deleted when it cannot unpack.
    result = subprocess.run(['lbu', 'status'], capture_output=True, text=True, timeout=300)
    if result.returncode or result.stderr.strip():
        raise RuntimeError('lbu status failed: ' +
                           (result.stderr.strip() or 'exit status %d' % result.returncode)[-500:])
    if result.stdout.strip():
        raise RuntimeError('dom0 has pending lbu changes; reconcile them before activation: ' +
                           ', '.join(result.stdout.splitlines()[:5]))


def inspect_bundle(path, expected):
    if path.is_symlink() or not path.is_file() or sha(path) != expected:
        raise RuntimeError('dom0 bundle bytes differ from the selected build')
    with tarfile.open(path) as archive:
        entries = archive.getmembers()
        names = [m.name for m in entries]
        if (len(entries) > 600 or len(set(names)) != len(names) or
                any(not m.isfile() or m.name.startswith('/') or '..' in Path(m.name).parts for m in entries)):
            raise RuntimeError('unsafe dom0 bundle members')
        raw = archive.extractfile('bundle.json').read(1024**2 + 1)
        if len(raw) > 1024**2:
            raise RuntimeError('dom0 bundle metadata is too large')
        bundle = json.loads(raw)
        if (bundle.get('kind') != 'klokast.dom0-bundle.v1' or
                bundle.get('offline_reconstruction_verified') is not True or
                not re.fullmatch(r'v[0-9]+\.[0-9]+', bundle.get('branch', '')) or
                set(names) != set(bundle['files']) | {'bundle.json'} or
                not {'boot/xen.gz', 'boot/vmlinuz-lts', 'boot/initramfs-lts', 'boot/modloop-lts'} <= set(names)):
            raise RuntimeError('dom0 bundle lacks complete offline build evidence')
        total = 0
        for member in entries:
            if member.name == 'bundle.json':
                continue
            record = bundle['files'][member.name]
            total += member.size
            if member.size != record['bytes'] or total > 1024**3:
                raise RuntimeError('dom0 bundle exceeds its file size contract')
            digest = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024**2), b''):
                    digest.update(block)
            if digest.hexdigest() != record['sha256']:
                raise RuntimeError('dom0 bundle file checksum differs')
    return bundle, total


def grub(generation, mount, recovery):
    relative = '/' + str(generation.relative_to(mount))
    repos = ','.join(str(generation / 'repos' / str(i)) for i in range(2))
    args = ('modules=loop,squashfs,sd-mod,usb-storage,nvme quiet alpine_dev=LABEL=ALPINE_EFI '
            'modloop=' + relative + '/boot/modloop-lts alpine_repo=' + repos)
    return ('set timeout=5\nset default=0\n'
            'menuentry "Alpine Linux (Xen, from RAM)" {\n    insmod multiboot2\n'
            '    multiboot2 ' + relative + '/boot/xen.gz dom0_mem=1024M,max:1024M loglvl=all guest_loglvl=all\n'
            '    module2 ' + relative + '/boot/vmlinuz-lts ' + args + '\n'
            '    module2 ' + relative + '/boot/initramfs-lts\n}\n' + recovery)


def first_entry(config):
    entry = re.search(r'(?ms)^menuentry "[^"]+" \{.*?^\}', config)
    if entry is None:
        raise RuntimeError('the previous GRUB configuration has no managed recovery entry')
    return entry[0] + '\n'


def overlay_entry(config, operation, mount):
    """Boot the first entry of config with the overlay saved for operation."""
    # The initramfs reads a plain path from the boot partition that
    # nlplug-findfs has already mounted there. The device:path form would
    # mount the partition a second time.
    old = re.sub(r'\s+apkovl=\S+', '', first_entry(config))
    overlay_arg = ' apkovl=' + str(mount / 'klokast-dom0-recovery' / operation / 'overlay.tar.gz')
    return re.sub(r'(?m)^(\s*(?:module2|linux) .*vmlinuz[^\n]*)$', lambda line: line[1] + overlay_arg, old)


def recovery_entry(config, operation, mount):
    # The old boot entry remains reachable from the console. A maintenance
    # boot uses the old GRUB selection with single-user mode, so guests stay off.
    old = overlay_entry(config, operation, mount).replace('quiet ', 'init=/bin/sh quiet ')
    return old.replace('menuentry "', 'menuentry "Recovery: ')


# GRUB clears the one-time selection before it starts the candidate. After a
# failed candidate boot, the next boot (a power cycle is sufficient) starts
# entry 0: the accepted generation with its saved overlay.
TRIAL_HEADER = ('set timeout=5\nset default=0\ninsmod loadenv\n'
                'if [ -s ${prefix}/grubenv ]; then\n    load_env --file ${prefix}/grubenv next_entry\nfi\n'
                'if [ "${next_entry}" = "trial" ]; then\n    set default=1\n    set next_entry=\n'
                '    save_env --file ${prefix}/grubenv next_entry\nfi\n')


def trial_grub(generation, mount, before, operation):
    """The selection for a trial: the accepted entry stays the default."""
    candidate = first_entry(grub(generation, mount, '')).replace('menuentry "', 'menuentry "Trial: ', 1)
    return (TRIAL_HEADER + overlay_entry(before, operation, mount) + candidate +
            recovery_entry(before, operation, mount))


def grubenv(mount, values=()):
    """Write the 1024-byte GRUB environment block that load_env and save_env use."""
    text = ('# GRUB Environment Block\n# WARNING: Do not edit this file by tools other than grub-editenv!!!\n' +
            ''.join(name + '=' + value + '\n' for name, value in values)).encode()
    if len(text) > 1024:
        raise RuntimeError('GRUB environment exceeds its block')
    path = mount / 'boot/grub/grubenv'
    temporary = path.with_name('grubenv.dom0-update')
    with temporary.open('wb') as stream:
        stream.write(text + b'#' * (1024 - len(text))); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path); os.sync()


def trial_pending(mount):
    path = mount / 'boot/grub/grubenv'
    return path.exists() and 'next_entry=trial' in path.read_text(errors='replace').splitlines()


def replace_text(path, text):
    temporary = path.with_name(path.name + '.dom0-update')
    temporary.write_text(text)
    os.sync(); os.replace(temporary, path); os.sync()


def fallback(current, cmdline):
    """Tell if the host started the accepted entry of a trial selection."""
    words = cmdline.split()
    overlay = 'apkovl=' + str(Path(current['generation']).parents[1] / 'klokast-dom0-recovery' /
                              current['operation'] / 'overlay.tar.gz')
    policy = read(POLICY).get('generation') if POLICY.exists() else None
    return (overlay in words and not any(w.startswith('init=') for w in words) and
            not any('/klokast-dom0/' + current['image'] + '/' in w for w in words) and
            policy != current['generation'])


def rearm(mount, accepted, operation):
    # A/B in one EFI filesystem: before staging, Recovery boots the running
    # accepted generation with this operation's saved overlay. Older
    # generations and the legacy /boot files are then unreferenced.
    destination = mount / 'boot/grub/grub.cfg'
    config = destination.read_text()
    if not config.startswith(grub(Path(accepted['generation']), mount, '')):
        raise RuntimeError('GRUB does not boot the accepted dom0 generation; explicit recovery is required')
    temporary = destination.with_name('grub.cfg.dom0-update')
    temporary.write_text(grub(Path(accepted['generation']), mount, recovery_entry(config, operation, mount)))
    os.sync(); os.replace(temporary, destination); os.sync()


def reclaim(mount, accepted, operation):
    # Without a pending transaction, the rearmed GRUB references only the
    # accepted generation and this operation's recovery overlay. The
    # pre-policy root cache is unused once the policy names a generation cache.
    keep = Path(accepted['generation'])
    if keep.parent != mount / 'klokast-dom0' or not keep.is_dir():
        raise RuntimeError('accepted dom0 generation is absent; explicit recovery is required')
    if Path('/etc/apk/cache').resolve() != keep / 'cache':
        raise RuntimeError('/etc/apk/cache does not use the accepted generation; explicit recovery is required')
    config = (mount / 'boot/grub/grub.cfg').read_text()
    if (re.search(r'[ =]/boot/', config) or
            set(re.findall(r'klokast-dom0/([0-9a-f]{24})', config)) != {keep.name} or
            set(re.findall(r'klokast-dom0-recovery/([0-9a-f]{24})', config)) != {operation}):
        raise RuntimeError('GRUB references data outside the accepted generation; explicit recovery is required')
    loops = {Path(p.read_text().strip()) for p in Path('/sys/block').glob('loop*/loop/backing_file')}
    stale = [p for p in keep.parent.iterdir() if p != keep and ID.fullmatch(p.name)]
    stale += [p for p in (mount / 'klokast-dom0-recovery').iterdir() if p.name != operation and ID.fullmatch(p.name)]
    if POLICY.exists():
        stale.append(mount / 'cache')
    for path in stale:
        if any(loop.is_relative_to(path) for loop in loops):
            raise RuntimeError('stale EFI data is still in use: ' + str(path))
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    for name in ('xen.gz', 'vmlinuz-lts', 'initramfs-lts', 'modloop-lts'):
        legacy = mount / 'boot' / name
        if legacy in loops:
            raise RuntimeError('legacy boot file is still in use: ' + str(legacy))
        if legacy.is_file() and not legacy.is_symlink():
            legacy.unlink()
    os.sync()


def prepare(args):
    clean()
    pending = BASE / 'pending.json'
    if pending.exists():
        current = read(pending)
        if current['operation'] != args.operation:
            raise RuntimeError('another dom0 update is pending: ' + current['operation'])
        return current
    mount = media(writable=True)
    bundle_path = Path('/mnt/dom0_data/klokast-dom0-builds') / args.image / 'bundle.tar'
    bundle, size = inspect_bundle(bundle_path, args.sha256)
    if not isinstance(bundle.get('tailscale'), dict):
        raise RuntimeError('build a new dom0 bundle with signed upstream Tailscale')
    accepted_path = BASE / 'accepted.json'
    if POLICY.exists() and not accepted_path.exists():
        raise RuntimeError('dom0 image policy has no accepted transaction; explicit recovery is required')
    if accepted_path.exists():
        accepted = read(accepted_path)
        if (accepted['bundle']['packages'] == bundle['packages'] and
                accepted['bundle']['kernel_release'] == bundle['kernel_release'] and
                accepted['bundle'].get('tailscale') == bundle.get('tailscale')):
            for name, record in accepted['bundle']['files'].items():
                if sha(Path(accepted['generation']) / name) != record['sha256']:
                    raise RuntimeError('accepted dom0 bundle bytes changed; explicit recovery is required')
            return {'state': 'unchanged', 'box': args.box}
    old_world = set(Path('/etc/apk/world').read_text().splitlines())
    transition = {'tailscale', 'tailscale-openrc', 'nftables', 'openresolv'}
    if old_world - transition != set(bundle['world']) - transition:
        raise RuntimeError('dom0 runtime package world differs from the image policy')
    for path in Path('/mnt/dom0_data/klokast-vm-updates').rglob('journal.json'):
        if read(path).get('stage') not in ('accepted', 'complete', 'adopted', 'recovered'):
            raise RuntimeError('unfinished VM update prevents a host reboot')
    for path in ('klokast-router-updates/pending.json', 'klokast-router-updates/cold-test.json'):
        if (Path('/mnt/dom0_data') / path).exists():
            raise RuntimeError('unfinished router operation prevents a host reboot')
    overlay = mount / (args.box + '-dom0.apkovl.tar.gz')
    if not overlay.is_file():
        raise RuntimeError('managed root apkovl is absent')
    efi_recovery = mount / 'klokast-dom0-recovery' / args.operation
    efi_recovery.mkdir(parents=True, mode=0o700)
    # Keep recovery archives outside Alpine's automatic *.apkovl discovery.
    shutil.copyfile(overlay, efi_recovery / 'overlay.tar.gz')
    if accepted_path.exists():
        rearm(mount, read(accepted_path), args.operation)
        reclaim(mount, read(accepted_path), args.operation)
    capacity = os.statvfs(mount)
    free, needed = capacity.f_bavail * capacity.f_frsize, size + 64 * 1024**2
    if free < needed:
        raise RuntimeError('EFI storage cannot hold this bundle and its recovery reserve: %d MiB free, '
                           '%d MiB needed (bundle %d MiB + 64 MiB reserve) on %s' %
                           (free // 1024**2, -(-needed // 1024**2), size // 1024**2, mount))
    generation = mount / 'klokast-dom0' / args.image
    if generation.exists():
        raise RuntimeError('dom0 generation already exists without a pending transaction')
    generation.mkdir(parents=True, mode=0o700)
    with tarfile.open(bundle_path) as archive:
        archive.extractall(generation, filter='data')
    for number in range(2):
        (generation / 'repos' / str(number) / '.boot_repository').touch()
    (generation / 'cache').mkdir()
    recovery = BASE / 'operations' / args.operation
    recovery.mkdir(parents=True, mode=0o700)
    shutil.copyfile(overlay, recovery / 'before.apkovl.tar.gz')
    shutil.copyfile(mount / 'boot/grub/grub.cfg', recovery / 'before-grub.cfg')
    shutil.copyfile('/etc/apk/repositories', recovery / 'before-repositories')
    shutil.copyfile('/etc/apk/world', recovery / 'before-world')
    shutil.copyfile('/etc/apk/protected_paths.d/lbu.list', recovery / 'before-lbu-list')
    for relative in (*TAILSCALE_FILES, TAILSCALE_SERVICE):
        source = Path('/') / relative
        if source.exists():
            target = recovery / 'tailscale-before' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    shutil.copyfile('/usr/local/sbin/vm_update_engine.py', recovery / 'before-vm-engine.py')
    shutil.copyfile('/usr/local/sbin/router-update-transaction', recovery / 'before-router-helper.py')
    if POLICY.exists():
        shutil.copyfile(POLICY, recovery / 'before-policy.json')
    current = {'operation': args.operation, 'image': args.image, 'box': args.box, 'state': 'prepared',
               'bundle_sha256': args.sha256, 'bundle': bundle, 'generation': str(generation),
               'boot_before': boot_id(), 'tailscale_id': identity(), 'domains': domains(),
               'cache_before': os.readlink('/etc/apk/cache')}
    save(recovery / 'record.json', current)
    save(pending, current)
    return current


def select(current):
    if current['state'] not in ('prepared', 'selecting'):
        return current
    if current['state'] == 'prepared':
        clean()
        current['state'] = 'selecting'; save(BASE / 'pending.json', current)
    generation = Path(current['generation'])
    mount = media(writable=True)
    recovery = BASE / 'operations' / current['operation']
    before = (recovery / 'before-grub.cfg').read_text()
    repositories = Path('/etc/apk/repositories')
    old_repos = (recovery / 'before-repositories').read_text()
    new_repos = re.sub(r'/v[0-9]+\.[0-9]+/', '/' + current['bundle']['branch'] + '/', old_repos)
    if new_repos == old_repos and current['bundle']['branch'] not in old_repos:
        raise RuntimeError('cannot preserve the current mirror for the selected Alpine branch')
    repositories.write_text(new_repos)
    cache = Path('/etc/apk/cache')
    cache.unlink(missing_ok=True); cache.symlink_to(generation / 'cache')
    policy = {'image': current['image'], 'branch': current['bundle']['branch'],
              'bundle_sha256': current['bundle_sha256'], 'generation': str(generation),
              'tailscale': {relative: current['bundle']['files']['tailscale/' + relative]['sha256']
                            for relative in TAILSCALE_BINARIES}}
    save(POLICY, policy)
    shutil.copyfile(BASE / ('engine-' + current['operation'] + '.py'), '/usr/local/sbin/vm_update_engine.py')
    shutil.copyfile(BASE / ('router-' + current['operation'] + '.py'), '/usr/local/sbin/router-update-transaction')
    install_tailscale(generation, current['bundle'])
    Path('/etc/apk/world').write_text('\n'.join(current['bundle']['world']) + '\n')
    drop_console_getty()
    run(['lbu', 'commit', '-d'])
    # An unplanned reboot before the reboot step starts the accepted entry.
    grubenv(mount)
    replace_text(mount / 'boot/grub/grub.cfg', trial_grub(generation, mount, before, current['operation']))
    current.update(state='selected', trial=True)
    save(BASE / 'pending.json', current)
    return current


def verify(current):
    if boot_id() == current['boot_before']:
        raise RuntimeError('dom0 has not rebooted; resume the selected update')
    if current.get('trial'):
        cmdline = Path('/proc/cmdline').read_text()
        if fallback(current, cmdline):
            # The candidate did not complete its one boot. abort records it.
            return dict(current, state='fallback')
        if 'modloop=/klokast-dom0/' + current['image'] + '/boot/modloop-lts' not in cmdline.split():
            raise RuntimeError('dom0 started neither the trial nor the accepted entry; keep recovery evidence')
        # A host whose GRUB cannot clear the entry would start the trial again.
        current['one_time_cleared'] = not trial_pending(media())
    if socket.gethostname().split('.')[0] != current['box'] + '-dom0' or identity() != current['tailscale_id']:
        raise RuntimeError('dom0 machine identity changed after reboot')
    if run(['uname', '-r']) != current['bundle']['kernel_release']:
        raise RuntimeError('dom0 booted another kernel; keep recovery evidence')
    xen = {k.strip(): v.strip() for k, v in
           (line.split(':', 1) for line in run(['xl', 'info']).splitlines() if ':' in line)}
    version = xen.get('xen_major', '') + '.' + xen.get('xen_minor', '') + xen.get('xen_extra', '')
    if version != current['bundle']['packages']['xen-hypervisor'].rsplit('-r', 1)[0]:
        raise RuntimeError('running Xen version differs from the selected hypervisor package')
    installed = {}
    for block in Path('/lib/apk/db/installed').read_text().split('\n\n'):
        values = dict(line.split(':', 1) for line in block.splitlines() if line.startswith(('P:', 'V:')))
        if 'P' in values: installed[values['P']] = values['V']
    if installed != current['bundle']['packages']:
        raise RuntimeError('dom0 installed package closure differs from the selected bundle')
    verify_tailscale(current['bundle'])
    if domains() != current['domains']:
        raise RuntimeError('guest names or UUIDs differ after the host reboot')
    persist_runtime(current)
    clean()
    current.update(state='boot-verified', boot_after=boot_id())
    save(BASE / 'pending.json', current)
    return current


def drop_console_getty(inittab=Path('/etc/inittab')):
    # /dev/tty0 is the visible console. A getty there reads tty1 together with
    # the tty1 getty, so each one receives only part of a console login.
    lines = inittab.read_text().splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith('tty0:')]
    if kept != lines:
        inittab.write_text(''.join(kept))


def install_tailscale(generation, bundle, root=Path('/')):
    # The apkovl supplies signed upstream binaries before OpenRC starts.
    # Do not restart the current daemon during boot selection.
    for relative, mode in TAILSCALE_FILES.items():
        source = generation / 'tailscale' / relative
        record = bundle['files'].get('tailscale/' + relative)
        if not record or sha(source) != record['sha256']:
            raise RuntimeError('selected upstream Tailscale bytes changed')
    for relative, mode in TAILSCALE_FILES.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.dom0-update')
        shutil.copyfile(generation / 'tailscale' / relative, temporary)
        temporary.chmod(mode)
        os.replace(temporary, target)
        if relative not in TAILSCALE_BINARIES:
            run(['lbu', 'include', str(target)])
    included = (root / 'etc/apk/protected_paths.d/lbu.list').read_text().splitlines()
    for relative in TAILSCALE_BINARIES:
        if '+' + relative in included:
            run(['lbu', 'include', '-r', str(root / relative)])
    service = root / TAILSCALE_SERVICE
    temporary = service.with_name(service.name + '.dom0-update')
    temporary.write_text(TAILSCALE_SERVICE_TEXT)
    temporary.chmod(0o755)
    os.replace(temporary, service)
    if not TAILSCALE_RUNLEVEL.is_symlink():
        run(['rc-update', 'add', service.name, 'default'])


def verify_tailscale(bundle):
    record = bundle['tailscale']
    for relative, field in (('usr/bin/tailscale', 'tailscale_sha256'),
                            ('usr/sbin/tailscaled', 'tailscaled_sha256'),
                            ('etc/init.d/tailscale', 'openrc_sha256')):
        if sha(Path('/') / relative) != record[field]:
            raise RuntimeError('dom0 upstream Tailscale file differs: ' + relative)
    version = json.loads(run(['tailscale', 'status', '--json']))['Version'].split('-')[0]
    if version != record['version']:
        raise RuntimeError('dom0 is not running the selected upstream Tailscale release')


def persist_runtime(current):
    """Commit only known boot outputs after the identity and guests pass."""
    if identity() != current['tailscale_id'] or domains() != current['domains']:
        raise RuntimeError('identity or guest verification must pass before runtime persistence')
    commit_boot_outputs(current)


def commit_boot_outputs(current):
    allowed = {'var/lib/tailscale/tailscaled.state', 'etc/apk/protected_paths.d/lbu.list'}
    for line in run(['lbu', 'status']).splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError('unrecognized lbu change record')
        name = parts[1]
        cache = re.fullmatch(r'var/lib/tailscale/profile-data(?:/[^/]+(?:/netmap-cache(?:/.*)?)?)?', name)
        if name == 'usr/local/sbin/router-update-transaction':
            frozen = BASE / ('router-' + current['operation'] + '.py')
            if not frozen.exists() or sha(frozen) != sha(Path('/' + name)):
                raise RuntimeError('router recovery correction differs from the approved source')
        elif name not in allowed and not cache:
            raise RuntimeError('unrelated post-boot lbu change requires inspection: ' + name)
    # Exclude only the regenerable network-map cache, preserving other data
    # that current or future Tailscale versions place in the profile directory.
    for directory in Path('/var/lib/tailscale/profile-data').glob('*/netmap-cache'):
        if directory.is_dir() and not directory.is_symlink():
            run(['lbu', 'exclude', str(directory)])
    if run(['lbu', 'status']):
        media(writable=True)
        run(['lbu', 'commit', '-d'])


def accept(current):
    current = verify(current)
    if current['state'] == 'fallback':
        raise RuntimeError('dom0 started the accepted generation; the trial boot did not complete')
    if current.get('trial'):
        mount = media(writable=True)
        before = (BASE / 'operations' / current['operation'] / 'before-grub.cfg').read_text()
        replace_text(mount / 'boot/grub/grub.cfg', grub(Path(current['generation']), mount,
                                                        recovery_entry(before, current['operation'], mount)))
        grubenv(mount)
    current['state'] = 'accepted'
    save(BASE / 'accepted.json', current)
    save(BASE / 'operations' / current['operation'] / 'record.json', current)
    (BASE / 'pending.json').unlink(); os.sync()
    return current


def restore(source, target):
    # Replace by rename: an in-place write fails with ETXTBSY while the file
    # runs, for example tailscaled for each Tailscale SSH session.
    temporary = target.with_name(target.name + '.dom0-restore')
    shutil.copy2(source, temporary)
    os.replace(temporary, target)


def roll_back(current):
    """Make EFI select the accepted system that the fallback entry started."""
    if identity() != current['tailscale_id']:
        raise RuntimeError('dom0 identity changed after the fallback boot; inspect it before a rollback')
    recovery = BASE / 'operations' / current['operation']
    mount = media(writable=True)
    # The fallback started this saved overlay; it becomes the normal overlay again.
    restore(recovery / 'before.apkovl.tar.gz', mount / (current['box'] + '-dom0.apkovl.tar.gz'))
    replace_text(mount / 'boot/grub/grub.cfg', (recovery / 'before-grub.cfg').read_text())
    grubenv(mount)
    commit_boot_outputs(current)
    current['state'] = 'rolled-back'
    save(recovery / 'record.json', current)
    (BASE / 'pending.json').unlink(); os.sync()
    return current


def abort(current):
    if (current.get('trial') and boot_id() != current['boot_before'] and
            fallback(current, Path('/proc/cmdline').read_text())):
        return roll_back(current)
    if boot_id() != current['boot_before'] or current['state'] == 'rebooting':
        raise RuntimeError('automatic restoration is forbidden after a reboot started; use console recovery')
    if current.get('reboot_test'):
        current['state'] = 'aborted'
        save(BASE / 'operations' / current['operation'] / 'record.json', current)
        (BASE / 'pending.json').unlink(); os.sync()
        return current
    recovery = BASE / 'operations' / current['operation']
    mount = media(writable=True)
    shutil.copyfile(recovery / 'before.apkovl.tar.gz', mount / (current['box'] + '-dom0.apkovl.tar.gz'))
    shutil.copyfile(recovery / 'before-grub.cfg', mount / 'boot/grub/grub.cfg')
    shutil.copyfile(recovery / 'before-repositories', '/etc/apk/repositories')
    if (recovery / 'before-world').exists():
        shutil.copyfile(recovery / 'before-world', '/etc/apk/world')
        shutil.copyfile(recovery / 'before-lbu-list', '/etc/apk/protected_paths.d/lbu.list')
        for relative in (*TAILSCALE_FILES, TAILSCALE_SERVICE):
            target = Path('/') / relative
            source = recovery / 'tailscale-before' / relative
            if source.exists():
                restore(source, target)
            else:
                target.unlink(missing_ok=True)
        service = Path('/') / TAILSCALE_SERVICE
        if service.exists() != TAILSCALE_RUNLEVEL.is_symlink():
            run(['rc-update', 'add' if service.exists() else 'del', service.name, 'default'])

    restore(recovery / 'before-vm-engine.py', Path('/usr/local/sbin/vm_update_engine.py'))
    restore(recovery / 'before-router-helper.py', Path('/usr/local/sbin/router-update-transaction'))
    cache = Path('/etc/apk/cache')
    cache.unlink(); cache.symlink_to(current['cache_before'])
    if (recovery / 'before-policy.json').exists():
        shutil.copyfile(recovery / 'before-policy.json', POLICY)
    else:
        POLICY.unlink(missing_ok=True)
    current['state'] = 'aborted'
    save(recovery / 'record.json', current)
    (BASE / 'pending.json').unlink(); os.sync()
    return current


def test_reboot(args):
    if (BASE / 'pending.json').exists():
        raise RuntimeError('complete the pending host update before testing reboot persistence')
    current = read(BASE / 'accepted.json')
    clean()
    if (identity() != current['tailscale_id'] or domains() != current['domains'] or
            run(['uname', '-r']) != current['bundle']['kernel_release']):
        raise RuntimeError('the accepted host changed; verify it before a reboot test')
    current.update(operation=args.operation, state='selected', reboot_test=True, boot_before=boot_id())
    save(BASE / 'operations' / args.operation / 'record.json', current)
    save(BASE / 'pending.json', current)
    return current


def reboot(current):
    if boot_id() != current['boot_before']:
        return verify(current)
    if current['state'] not in ('selected', 'rebooting'):
        raise RuntimeError('select and persist the candidate before a reboot')
    if current.get('trial'):
        # Check before the guests stop: a refusal must leave them running.
        mount = media(writable=True)
        before = (BASE / 'operations' / current['operation'] / 'before-grub.cfg').read_text()
        if (mount / 'boot/grub/grub.cfg').read_text() != trial_grub(Path(current['generation']), mount, before,
                                                                     current['operation']):
            raise RuntimeError('GRUB does not hold the trial selection of this operation; host reboot stopped')
    if current['state'] == 'selected':
        clean()
        if domains() != current['domains']:
            raise RuntimeError('guest inventory changed before reboot')
        current['state'] = 'rebooting'; save(BASE / 'pending.json', current)
        run(['xl', 'shutdown', '-a'])
    deadline = time.monotonic() + 300
    while domains():
        if time.monotonic() >= deadline:
            raise RuntimeError('a guest did not shut down; host reboot stopped; resume explicitly')
        time.sleep(3)
    if current.get('trial'):
        grubenv(mount, [('next_entry', 'trial')])
    run(['sync'])
    subprocess.Popen(['/sbin/reboot'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return {'state': 'rebooting', 'operation': current['operation']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'select', 'reboot', 'verify', 'accept', 'abort', 'status', 'test-reboot'))
    p.add_argument('--box', required=True)
    p.add_argument('--operation'); p.add_argument('--image'); p.add_argument('--sha256')
    args = p.parse_args()
    if (os.geteuid() != 0 or socket.gethostname().split('.')[0] != args.box + '-dom0' or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', args.box) or
            'control_d' not in Path('/proc/xen/capabilities').read_text()):
        raise RuntimeError('run the transaction on its matching dom0 as root')
    if args.action != 'status' and not ID.fullmatch(args.operation or ''):
        raise RuntimeError('one recorded operation ID is required')
    os.umask(0o077); BASE.mkdir(mode=0o700, exist_ok=True)
    import contextlib
    with contextlib.ExitStack() as stack:
        for name in ('klokast-vm-updates/transaction.lock', 'klokast-router-updates/transaction.lock',
                     'klokast-vm-templates/build.lock'):
            lock = stack.enter_context(open(Path('/mnt/dom0_data') / name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'status':
            result = {k: read(BASE / (k + '.json')) if (BASE / (k + '.json')).exists() else None
                      for k in ('accepted', 'pending')}
        elif args.action == 'test-reboot':
            result = test_reboot(args)
        elif args.action == 'prepare':
            if not ID.fullmatch(args.image or '') or not re.fullmatch('[0-9a-f]{64}', args.sha256 or ''):
                raise RuntimeError('selected build identity is incomplete')
            result = prepare(args)
        else:
            if args.action == 'verify' and not (BASE / 'pending.json').exists():
                accepted = read(BASE / 'accepted.json')
                if accepted['operation'] != args.operation:
                    raise RuntimeError('no matching accepted operation')
                print(json.dumps(accepted, sort_keys=True)); return
            current = read(BASE / 'pending.json')
            if current['operation'] != args.operation or current['box'] != args.box:
                raise RuntimeError('another host transaction is pending')
            result = globals()[args.action](current)
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
