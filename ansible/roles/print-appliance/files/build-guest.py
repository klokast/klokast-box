#!/usr/bin/env python3
"""Networkless print construction, using the common verified APK installer."""
import json
import os
from pathlib import Path
import subprocess
import base_build as base
import print_image


def main():
    args = base.parameters()
    base.require_disks()
    result = dict(kind='klokast.vm-template-build-result.v1', success=False,
                  operation_id=args['klokast_operation'], inputs_sha256=args['klokast_inputs'])
    loops = []
    try:
        manifest = base.unpack(args)
        config = json.loads((base.INPUT / 'print.json').read_text())
        # Fixed MBR layout: read-only OS followed by disposable identity state.
        subprocess.run(['sfdisk', '/dev/xvdb'], input='label: dos\nstart=2048,size=8124416,type=83\nstart=8126464,size=262144,type=83\n',
                       text=True, capture_output=True, check=True)
        base.run(['mdev','-s'])
        base.DISKS['root'] = Path('/dev/xvdb1')
        base.run(['mkfs.ext4', '-F', '-L', 'print-state', '/dev/xvdb2'])
        Path('/state').mkdir()
        base.run(['mount', '/dev/xvdb2', '/state'])
        try:
            Path('/state/authkey').write_bytes((base.INPUT/'authkey').read_bytes())
            Path('/state/authkey').chmod(0o600)
        finally:
            base.run(['umount', '/state'])
        base.baseline = lambda m: print_image.configure(base.ROOT, m, config, base.INPUT)
        export = base.export_file
        def export_and_seal(path, device, maximum):
            value = export(path, device, maximum)
            if path.name == 'initramfs-virt':
                print_image.seal(base.ROOT, manifest)
            return value
        base.export_file = export_and_seal
        result.update(base.build(manifest), success=True)
    except Exception as error:
        # Never emit subprocess output: the private capsule contains enrollment material.
        result['error'] = type(error).__name__ + ': ' + str(error)[:500]
        print(result['error'], flush=True)
    finally:
        for loop in reversed(loops):
            subprocess.run(['losetup', '-d', loop], check=True)
    with Path('/dev/xvde').open('wb', buffering=0) as stream:
        stream.write(json.dumps(result, sort_keys=True).encode()+b'\0')
        os.fsync(stream.fileno())
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
