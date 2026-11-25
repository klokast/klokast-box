#!/usr/bin/env python3
"""One-time Xen cpufreq=hwp boot entry. Run through 74-dom0-cpufreq-trial.yml."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket

OPTION = 'cpufreq=hwp'
BEGIN = '# BEGIN klokast one-time cpufreq trial\n'
END = '# END klokast one-time cpufreq trial\n'


def load_native():
    # The playbook stages the transaction helper beside this file.
    spec = importlib.util.spec_from_file_location('dom0_update', Path(__file__).with_name('dom0_update.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def block(native, config):
    """Return the text that adds the trial after the two existing entries."""
    if len(re.findall(r'(?m)^\s*menuentry ', config)) != 2 or re.search(r'(?m)^\s*submenu ', config):
        raise RuntimeError('expected exactly the accepted and the Recovery GRUB entry; nothing was changed')
    entry, count = re.subn(r'(?m)^(\s*multiboot2 .*)$', lambda line: line[1] + ' ' + OPTION,
                           native.first_entry(config))
    if count != 1 or 'cpufreq=' in native.first_entry(config):
        raise RuntimeError('the accepted GRUB entry needs one Xen line without a cpufreq option')
    # GRUB selects the trial only after it reads back from disk that it cleared
    # the flag. Otherwise a hang could repeat at each power cycle.
    return (BEGIN + 'insmod loadenv\ninsmod test\nset next_entry=\n'
            'load_env --file ${prefix}/grubenv next_entry\n'
            'if [ "x${next_entry}" = "xtrial" ]; then\n'
            '    set next_entry=\n'
            '    save_env --file ${prefix}/grubenv next_entry\n'
            '    set next_entry=unknown\n'
            '    load_env --file ${prefix}/grubenv next_entry\n'
            '    if [ "x${next_entry}" = "x" ]; then\n        set default=2\n    fi\n'
            'fi\n' + entry.replace('menuentry "', 'menuentry "Trial: ', 1) + END)


def replace(path, data):
    temporary = path.with_name(path.name + '.cpufreq-trial')
    with temporary.open('wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    os.sync()


@contextlib.contextmanager
def writable(native):
    mount = native.media(writable=True)
    try:
        yield mount
    finally:
        os.sync()
        native.run(['mount', '-o', 'remount,ro', '/dev/nvme0n1p1', mount])


def saved(native):
    return native.BASE / 'cpufreq-trial' / 'grub.cfg'


def require_idle(native, box):
    if (native.BASE / 'pending.json').exists():
        raise RuntimeError('a dom0 transaction is pending; nothing was changed')
    accepted = native.read(native.BASE / 'accepted.json')
    if accepted['box'] != box:
        raise RuntimeError('the accepted dom0 generation belongs to another box')
    return accepted


def arm(native, box):
    accepted = require_idle(native, box)
    mount = native.media()
    destination = mount / 'boot/grub/grub.cfg'
    environment = mount / 'boot/grub/grubenv'
    before = destination.read_bytes()
    config = before.decode()
    if BEGIN in config:
        raise RuntimeError('a cpufreq trial block already exists; run clear first')
    if not config.startswith(native.grub(Path(accepted['generation']), mount, '')) or not config.endswith('}\n'):
        raise RuntimeError('GRUB does not boot the accepted dom0 generation; nothing was changed')
    if native.trial_pending(mount) or b'next_entry' in environment.read_bytes():
        raise RuntimeError('grubenv is not empty; nothing was changed')
    for name in ('loadenv.mod', 'test.mod', 'multiboot2.mod'):
        if not (mount / 'boot/grub/x86_64-efi' / name).is_file():
            raise RuntimeError('GRUB module is absent: ' + name)
    added = block(native, config).encode()
    saved(native).parent.mkdir(mode=0o700, exist_ok=True)
    replace(saved(native), before)
    replace(saved(native).with_name('grubenv'), environment.read_bytes())
    # grubenv comes last: a block without the flag starts the accepted entry.
    with writable(native):
        replace(destination, before + added)
        if destination.read_bytes() != before + added:
            raise RuntimeError('grub.cfg readback differs; run clear')
        native.grubenv(mount, [('next_entry', 'trial')])
    return status(native, box)


def clear(native, box):
    require_idle(native, box)
    mount = native.media()
    destination = mount / 'boot/grub/grub.cfg'
    before = saved(native).read_bytes()
    current = destination.read_bytes()
    if current not in (before, before + block(native, before.decode()).encode()):
        raise RuntimeError('grub.cfg is neither the saved copy nor the saved copy with the trial; inspect it')
    values = re.findall(rb'(?m)^([a-z_]+)=(.*)$', (mount / 'boot/grub/grubenv').read_bytes())
    if any(name != b'next_entry' or value not in (b'', b'trial') for name, value in values):
        raise RuntimeError('grubenv holds unknown values; inspect it')
    with writable(native):
        native.grubenv(mount)
        replace(destination, before)
    if (destination.read_bytes() != before or
            (mount / 'boot/grub/grubenv').read_bytes() != saved(native).with_name('grubenv').read_bytes()):
        raise RuntimeError('the GRUB files differ from the saved copies after clear; inspect them')
    return status(native, box)


def status(native, box):
    mount = native.media()
    destination = mount / 'boot/grub/grub.cfg'
    environment = mount / 'boot/grub/grubenv'
    options = [line.split()[3].split(',') for line in Path('/proc/mounts').read_text().splitlines()
               if line.split()[1] == str(mount)]
    text = environment.read_bytes()
    commandline = [line.split(':', 1)[1].strip() for line in native.run(['xl', 'info']).splitlines()
                   if line.startswith('xen_commandline')]
    return {'box': box, 'block': BEGIN in destination.read_text(), 'grubenv_bytes': len(text),
            'grubenv': [line for line in text.decode(errors='replace').splitlines() if line.strip('#') and
                        not line.startswith('# ')],
            'grub_sha256': digest(destination), 'saved_sha256': digest(saved(native)),
            'efi_read_only': bool(options) and all('ro' in value for value in options),
            'pending': (native.BASE / 'pending.json').exists(), 'xen_commandline': commandline}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('arm', 'clear', 'status'))
    parser.add_argument('--box', required=True)
    args = parser.parse_args()
    native = load_native()
    if (os.geteuid() != 0 or socket.gethostname().split('.')[0] != args.box + '-dom0' or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', args.box) or
            'control_d' not in Path('/proc/xen/capabilities').read_text()):
        raise RuntimeError('run the trial on its matching dom0 as root')
    os.umask(0o077)
    with contextlib.ExitStack() as stack:
        for name in ('klokast-vm-updates/transaction.lock', 'klokast-router-updates/transaction.lock',
                     'klokast-vm-templates/build.lock'):
            lock = stack.enter_context(open(Path('/mnt/dom0_data') / name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(globals()[args.action](native, args.box), sort_keys=True))


if __name__ == '__main__':
    main()
